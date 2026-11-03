"""JevKeywordJudge: gates (gates.py) -> Jev judgments (jev_questions.py via jev_client.py) -> decision table (decision.py)
-> quota fill. Implements contracts.KeywordJudge.

Without TYPESAFE_API_KEY the judge runs in 'heuristic-no-jev' mode: gates and metric-only scores still run, but every
keyword that passes the gates becomes Decision.REVIEW (semantic judgments are never guessed).
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..contracts import (BusinessProfile, Decision, JudgeVerdict, KeywordCandidate, KeywordEvidence, KeywordRecord,
                         PageBrief, ResearchTargets)
from . import jev_questions as jq
from .decision import decide, fill_quota, gate_verdict
from .gates import run_gates
from .jev_client import JevBackend, JevClient, JevError

logger = logging.getLogger(__name__)

HEURISTIC_MODE = 'heuristic-no-jev'
JEV_MODE = 'jev'
DEFAULT_KEYWORDS_PER_REQUEST = 1      # focused state per keyword (see jev_questions docstring)
DEFAULT_MAX_WORKERS = 4

# same_intent: SERP overlap first, Jev only when SERP data is missing or ambiguous
SERP_OVERLAP_TOP_N = 10
SERP_OVERLAP_SAME = 4                 # >= this many shared top-10 URLs -> same intent (1.0)
SERP_DISJOINT_PROBABILITY = 0.05      # both SERPs known, nothing shared
UNKNOWN_PROBABILITY = 0.5             # no SERP and no Jev (or Jev failed): unsettled, never a "no"


class JevKeywordJudge:
    def __init__(self, client: JevBackend | None, *, evidence_dir: str | Path | None = None,
                 keywords_per_request: int = DEFAULT_KEYWORDS_PER_REQUEST, max_workers: int = DEFAULT_MAX_WORKERS,
                 site_authority: str = 'low'):
        self.client = client
        self.mode = JEV_MODE if client is not None else HEURISTIC_MODE
        self.evidence_dir = evidence_dir
        self.keywords_per_request = max(1, keywords_per_request)
        self.max_workers = max(1, max_workers)
        self.site_authority = site_authority
        if client is None:
            logger.warning('!!! TYPESAFE_API_KEY not set: keyword judge runs in %s mode. Gates and metric scores only; '
                           'every keyword that passes the gates is marked REVIEW. !!!', HEURISTIC_MODE)

    @classmethod
    def from_env(cls, *, evidence_dir: str | Path | None = None, **kwargs) -> 'JevKeywordJudge':
        """Env: TYPESAFE_API_KEY, TYPESAFE_MODEL, KEYWORD_SITE_AUTHORITY (low|medium|high, default low)."""
        kwargs.setdefault('site_authority', os.getenv('KEYWORD_SITE_AUTHORITY', 'low').strip().lower() or 'low')
        return cls(JevClient.from_env(evidence_dir=evidence_dir), evidence_dir=evidence_dir, **kwargs)

    # ------------------------------------------------------------ KeywordJudge.judge
    def judge(self, page: PageBrief, business: BusinessProfile, candidates: list[KeywordCandidate],
              evidence: dict[str, KeywordEvidence], *, accepted: list[KeywordRecord],
              targets: ResearchTargets) -> list[JudgeVerdict]:
        verdicts: dict[int, JudgeVerdict] = {}
        pending: list[tuple[int, KeywordCandidate, KeywordEvidence, list]] = []
        for index, candidate in enumerate(candidates):
            ev = evidence.get(candidate.keyword) or KeywordEvidence(keyword=candidate.keyword)
            hits = run_gates(candidate, ev, business, targets, accepted, site_authority=self.site_authority)
            if any(h.hard for h in hits):
                verdicts[index] = gate_verdict(candidate, ev, hits)                     # G1: no Jev call
            else:
                pending.append((index, candidate, ev, hits))

        if self.client is None:
            for index, candidate, ev, hits in pending:
                verdicts[index] = decide(candidate, ev, page, business, hits, None, mode=HEURISTIC_MODE)
        else:
            for index, verdict in self._judge_with_jev(page, business, pending):
                verdicts[index] = verdict

        ordered = [verdicts[i] for i in range(len(candidates))]
        fill_quota(list(zip(candidates, ordered)), targets, accepted, page.page_id)
        return ordered

    def _judge_with_jev(self, page, business, pending):
        base_tokens = jq.estimate_tokens({'business': jq.business_state(business), 'page': jq.page_state(page)})
        items = [(candidate, ev) for _, candidate, ev, _ in pending]
        chunks, offset = [], 0
        for chunk in jq.chunk_items(items, self.keywords_per_request, base_tokens):
            chunks.append(pending[offset:offset + len(chunk)])
            offset += len(chunk)

        def run(chunk):
            state, questions, keys = jq.build_request(business, page, [(c, ev) for _, c, ev, _ in chunk])
            try:
                result = self.client.ask(state, questions)
                error, answers, model, ref = None, result.answers, result.model, result.payload_ref
            except JevError as exc:
                error, answers, model, ref = str(exc), {}, None, exc.payload_ref
            except Exception as exc:  # a broken client is still an error, never a "no"
                error, answers, model, ref = f'{type(exc).__name__}: {exc}', {}, None, None
            out = []
            for key, (index, candidate, ev, hits) in zip(keys, chunk):
                prefix = f'{key}__'
                own = {qid[len(prefix):]: a for qid, a in answers.items() if qid.startswith(prefix)}
                out.append((index, decide(candidate, ev, page, business, hits, own, mode=JEV_MODE, model=model,
                                          error=error, payload_ref=ref)))
            return out

        with ThreadPoolExecutor(max_workers=min(self.max_workers, max(1, len(chunks)))) as pool:
            for result in pool.map(run, chunks):
                yield from result

    # ------------------------------------------------------------ KeywordJudge.same_intent
    def same_intent(self, pairs: list[tuple[str, str]],
                    evidence: dict[str, KeywordEvidence]) -> dict[tuple[str, str], float]:
        out: dict[tuple[str, str], float] = {}
        ask: list[tuple[str, str]] = []
        fallback: dict[tuple[str, str], float] = {}
        for a, b in pairs:
            urls_a, urls_b = _serp_urls(evidence.get(a)), _serp_urls(evidence.get(b))
            if urls_a and urls_b:
                shared = len(urls_a & urls_b)
                if shared >= SERP_OVERLAP_SAME:
                    out[(a, b)] = 1.0
                    continue
                if shared == 0:
                    out[(a, b)] = SERP_DISJOINT_PROBABILITY
                    continue
                fallback[(a, b)] = shared / SERP_OVERLAP_SAME          # partial overlap: ambiguous
            else:
                fallback[(a, b)] = UNKNOWN_PROBABILITY                 # SERP missing
            ask.append((a, b))

        if ask and self.client is not None:
            state, questions, keys = jq.same_intent_request(ask)
            try:
                answers = self.client.ask(state, questions).answers
            except Exception as exc:
                logger.warning('same_intent Jev call failed (%s); using SERP-overlap fallback', exc)
                answers = {}
            for key, pair in keys.items():
                answer = answers.get(f'{key}__same_intent')
                out[pair] = answer['noul'] if answer and answer.get('noul') is not None else fallback[pair]
        else:
            out.update({pair: fallback[pair] for pair in ask})
        return out


def _serp_urls(evidence: KeywordEvidence | None) -> set[str]:
    if evidence is None or evidence.serp is None:
        return set()
    organic = sorted((r for r in evidence.serp.results if r.result_type == 'organic'), key=lambda r: r.position)
    return {r.url.rstrip('/').lower() for r in organic[:SERP_OVERLAP_TOP_N]}
