"""KeywordMapper: turn per-page verdicts into the site-wide keyword map the on-page stage reads.

    accepted records of every page
      -> candidate pairs across pages (same keyword | token Jaccard >= SEO_KEYWORD_OVERLAP | top-10 SERP overlap)
      -> judge.same_intent(pairs)  (identical keywords skip the judge: same intent by definition)
      -> same intent: keep on the page with the best composite_score * content_alignment,
                      demote the other to REJECT + CANNIBALIZATION (reason names the winning page)
      -> one primary per page (extra primaries -> secondary or QUOTA_FULL; a page that lost its primary promotes its
         strongest secondary), no primary shared by two pages
      -> PageKeywordPlan per page: slots, intent + journey stage, topic cluster role, competitor URLs, content gaps

Records are copied, never mutated in place; the returned records are the final CSV rows.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
import os

from seo_agents.keyword_research.contracts import (Decision, Intent, KeywordKind, PageKeywordPlan, RejectionCode, Source,
                                                   Tail)
from seo_agents.keyword_research.keyword_text import coverage, jaccard, normalize, tokens
from seo_agents.utils.step_logger import log

SLOTS = ('primary', 'secondary', 'question', 'conversational', 'short_tail')
INTENT_JOURNEY = {Intent.INFORMATIONAL: 'awareness', Intent.COMMERCIAL: 'consideration', Intent.TRANSACTIONAL: 'decision',
                  Intent.LOCAL: 'decision', Intent.NAVIGATIONAL: 'retention'}
JOURNEY_STAGES = {'awareness', 'consideration', 'decision', 'retention'}
INTENT_KEYS = ('user_intent', 'judged_user_intent', 'intent')
SERP_INTENT_KEYS = ('serp_intent', 'serp_dominant_intent')
JOURNEY_KEYS = ('journey_stage', 'journey', 'funnel_stage')


# ---------------------------------------------------------------- record helpers (also used by the output writer)
def answer(value):
    """A raw Jev judgment -> its plain answer (judgments may be stored as {'value'|'choice'|'answer': ...})."""
    if isinstance(value, dict):
        for key in ('value', 'choice', 'answer', 'label'):
            if value.get(key) is not None:
                return value[key]
        return None
    return value


def judgment(verdict, keys):
    if verdict is None:
        return None
    for key in keys:
        found = answer((verdict.judgments or {}).get(key))
        if found not in (None, ''):
            return found
    return None


def as_intent(value):
    if isinstance(value, Intent):
        return value
    try:
        return Intent(str(value).strip().lower())
    except ValueError:
        return None


def slot_of(record):
    """Which plan slot an accepted record fills ('primary' | ... | 'short_tail'), None when not accepted."""
    verdict = record.verdict
    if verdict is None or verdict.decision != Decision.ACCEPT:
        return None
    kind = verdict.assigned_kind or record.candidate.kind
    if kind == KeywordKind.PRIMARY:
        return 'primary'
    if kind == KeywordKind.SECONDARY and record.candidate.tail == Tail.SHORT:
        return 'short_tail'
    return kind.value


def final_status(record):
    if record.final_status == 'unevaluated':
        return 'unevaluated'
    slot = slot_of(record)
    if slot:
        return slot
    if record.verdict is not None and record.verdict.decision == Decision.REVIEW:
        return 'review'
    return 'rejected'


def metric(record, name):
    return record.evidence.value(name) if record.evidence is not None else None


def page_tokens(page):
    return tokens(' '.join([page.title or '', page.description or ''] + list(page.headings) + list(page.copy)))


def demote(record, code, reason, *, mapped_page_id=None):
    verdict = record.verdict
    record.verdict = replace(verdict, decision=Decision.REJECT, rejection_codes=list(verdict.rejection_codes) + [code],
                             reasons=list(verdict.reasons) + [reason])
    record.mapped_page_id = mapped_page_id
    record.final_status = 'rejected'


@dataclass
class MappingResult:
    plans: dict                      # page_id -> PageKeywordPlan
    records: list                    # every KeywordRecord (copies), final_status / mapped_page_id set
    cannibalization: list = field(default_factory=list)   # [{'keyword','page_id','winner_keyword','winner_page_id',...}]
    filters: dict = field(default_factory=dict)


class KeywordMapper:
    """Tool: site-wide cannibalization guard + PageKeywordPlan per page."""

    def __init__(self, judge=None, *, same_intent_threshold=None, overlap_threshold=None, serp_overlap=None, max_pairs=None):
        self.judge = judge
        self.same_intent_threshold = float(same_intent_threshold if same_intent_threshold is not None
                                           else os.getenv('SEO_KEYWORD_SAME_INTENT', '0.7'))
        self.overlap_threshold = float(overlap_threshold if overlap_threshold is not None
                                       else os.getenv('SEO_KEYWORD_OVERLAP', '0.5'))
        self.serp_overlap = int(serp_overlap if serp_overlap is not None else os.getenv('SEO_KEYWORD_SERP_OVERLAP', '4'))
        self.max_pairs = int(max_pairs if max_pairs is not None else os.getenv('SEO_KEYWORD_MAX_INTENT_PAIRS', '200'))

    def filters(self):
        return {'same_intent_threshold': self.same_intent_threshold, 'token_overlap_threshold': self.overlap_threshold,
                'serp_overlap_urls': self.serp_overlap, 'max_intent_pairs': self.max_pairs}

    # ------------------------------------------------------------ public
    def map(self, pages, results, *, business=None, targets=None):
        """pages: {page_id: PageBrief}; results: {page_id: {'records', 'iterations', 'status', 'competitor_urls'}}."""
        records = []
        for page_id in pages:
            for record in (results.get(page_id) or {}).get('records') or []:
                copy = replace(record)
                if copy.final_status != 'unevaluated':
                    copy.mapped_page_id = page_id if copy.verdict is not None and copy.verdict.decision != Decision.REJECT else None
                records.append(copy)
        corpora = {pid: page_tokens(page) for pid, page in pages.items()}
        cannibalized = self.resolve_cannibalization(pages, records, corpora)
        self.one_primary_per_page(pages, records, corpora, targets)
        for record in records:
            record.final_status = final_status(record)
        plans = self.build_plans(pages, results, records, corpora, business, targets)
        return MappingResult(plans=plans, records=records, cannibalization=cannibalized, filters=self.filters())

    # ------------------------------------------------------------ cannibalization
    def strength(self, record, corpora):
        score = record.verdict.composite_score if record.verdict and record.verdict.composite_score is not None else 0.0
        alignment = metric(record, 'content_alignment')
        if alignment is None:
            alignment = coverage(record.candidate.keyword, corpora.get(record.candidate.page_id, set()))
        return score * (alignment if alignment is not None else 0.5)

    def candidate_pairs(self, accepted):
        """[(similarity, left, right)] across different pages, identical keywords first."""
        pairs = []
        for i, left in enumerate(accepted):
            left_tokens, left_serp = tokens(left.candidate.keyword), serp_urls(left)
            for right in accepted[i + 1:]:
                if left.candidate.page_id == right.candidate.page_id:
                    continue
                if normalize(left.candidate.keyword) == normalize(right.candidate.keyword):
                    pairs.append((2.0, left, right))
                    continue
                similarity = jaccard(left_tokens, tokens(right.candidate.keyword))
                shared_serp = len(left_serp & serp_urls(right))
                if similarity >= self.overlap_threshold or (shared_serp and shared_serp >= self.serp_overlap):
                    pairs.append((max(similarity, shared_serp / 10), left, right))
        pairs.sort(key=lambda p: -p[0])
        return pairs

    def same_intent_scores(self, pairs, records):
        asked = [(l.candidate.keyword, r.candidate.keyword) for s, l, r in pairs if s < 2.0][:self.max_pairs]
        if not asked or self.judge is None:
            return {}
        evidence = {}
        for record in records:
            if record.evidence is not None:
                evidence.setdefault(record.candidate.keyword, record.evidence)
        try:
            return dict(self.judge.same_intent(asked, evidence) or {})
        except Exception as error:  # noqa: BLE001 - mapping must still produce a plan
            log(f'keyword mapping: same_intent failed ({type(error).__name__}: {error}); only identical keywords merged')
            return {}

    def resolve_cannibalization(self, pages, records, corpora):
        accepted = [r for r in records if slot_of(r)]
        pairs = self.candidate_pairs(accepted)
        scores = self.same_intent_scores(pairs, records)
        out = []
        for similarity, left, right in pairs:
            if not slot_of(left) or not slot_of(right):
                continue  # already demoted by a stronger pair
            key, rkey = (left.candidate.keyword, right.candidate.keyword), (right.candidate.keyword, left.candidate.keyword)
            probability = 1.0 if similarity >= 2.0 else scores.get(key, scores.get(rkey))
            if probability is None or probability < self.same_intent_threshold:
                continue
            ls, rs = self.strength(left, corpora), self.strength(right, corpora)
            winner, loser = (left, right) if ls >= rs else (right, left)
            win_page = pages[winner.candidate.page_id]
            reason = (f"cannibalization: '{loser.candidate.keyword}' has the same search intent as '{winner.candidate.keyword}' "
                      f"(p={probability:.2f}), which is kept on {win_page.url} (score x alignment "
                      f"{max(ls, rs):.1f} vs {min(ls, rs):.1f})")
            demote(loser, RejectionCode.CANNIBALIZATION, reason, mapped_page_id=winner.candidate.page_id)
            out.append({'keyword': loser.candidate.keyword, 'page_id': loser.candidate.page_id,
                        'winner_keyword': winner.candidate.keyword, 'winner_page_id': winner.candidate.page_id,
                        'same_intent': round(probability, 3), 'reason': reason})
        if out:
            log(f'keyword mapping: {len(out)} cannibalizing keywords moved to their stronger page')
        return out

    # ------------------------------------------------------------ primaries
    def one_primary_per_page(self, pages, records, corpora, targets):
        by_page = {}
        for record in records:
            if slot_of(record):
                by_page.setdefault(record.candidate.page_id, []).append(record)
        secondary_room = targets.secondary if targets is not None else 4
        for page_id, accepted in by_page.items():
            ranked = sorted(accepted, key=lambda r: -self.strength(r, corpora))
            primaries = [r for r in ranked if slot_of(r) == 'primary']
            if not primaries:
                promotable = [r for r in ranked if slot_of(r) in ('secondary', 'short_tail')]
                if promotable:
                    best = promotable[0]
                    best.verdict = replace(best.verdict, assigned_kind=KeywordKind.PRIMARY,
                                           reasons=list(best.verdict.reasons) + ['promoted to primary by the mapper: the page '
                                                                                 'had no accepted primary'])
                continue
            for extra in primaries[1:]:
                used = sum(1 for r in accepted if slot_of(r) == 'secondary')
                if used < secondary_room:
                    extra.verdict = replace(extra.verdict, assigned_kind=KeywordKind.SECONDARY,
                                            reasons=list(extra.verdict.reasons) + [
                                                f"kept as secondary: '{primaries[0].candidate.keyword}' is this page's primary"])
                else:
                    demote(extra, RejectionCode.QUOTA_FULL,
                           f"quota full: '{primaries[0].candidate.keyword}' is this page's primary and secondaries are full")
        owners = {}
        for record in sorted(records, key=lambda r: -self.strength(r, corpora)):
            if slot_of(record) != 'primary':
                continue
            key = normalize(record.candidate.keyword)
            if key in owners:
                winner = owners[key]
                demote(record, RejectionCode.CANNIBALIZATION,
                       f"cannibalization: '{record.candidate.keyword}' is already the primary of {pages[winner.candidate.page_id].url}",
                       mapped_page_id=winner.candidate.page_id)
            else:
                owners[key] = record

    # ------------------------------------------------------------ plans
    def build_plans(self, pages, results, records, corpora, business, targets):
        by_page = {pid: [] for pid in pages}
        for record in records:
            by_page.setdefault(record.candidate.page_id, []).append(record)
        plans = {}
        for page_id, page in pages.items():
            rows = by_page.get(page_id) or []
            ranked = sorted([r for r in rows if slot_of(r)], key=lambda r: -self.strength(r, corpora))
            slots = {name: [r.candidate.keyword for r in ranked if slot_of(r) == name] for name in SLOTS}
            primary = next((r for r in ranked if slot_of(r) == 'primary'), None)
            intent, journey = self.intent_and_journey(primary, ranked)
            result = results.get(page_id) or {}
            plans[page_id] = PageKeywordPlan(
                page_id=page_id, url=page.url, intent=intent, journey_stage=journey,
                primary=primary.candidate.keyword if primary else None, secondary=slots['secondary'],
                questions=slots['question'], conversational=slots['conversational'], short_tail=slots['short_tail'],
                competitor_urls=list(result.get('competitor_urls') or []),
                content_gaps=self.content_gaps(rows, corpora.get(page_id, set())),
                iterations=int(result.get('iterations') or 0),
                status=self.status(result, slots, targets))
        self.assign_clusters(pages, plans, records, business)
        return plans

    @staticmethod
    def intent_and_journey(primary, ranked):
        intent = None
        if primary is not None:
            intent = as_intent(judgment(primary.verdict, INTENT_KEYS)) or as_intent(metric(primary, 'provider_intent')) \
                or primary.candidate.hypothesized_intent
        if intent is None and ranked:
            votes = Counter(i for i in (as_intent(judgment(r.verdict, INTENT_KEYS)) or r.candidate.hypothesized_intent
                                        for r in ranked) if i)
            intent = votes.most_common(1)[0][0] if votes else None
        journey = judgment(primary.verdict, JOURNEY_KEYS) if primary is not None else None
        journey = str(journey).lower() if journey and str(journey).lower() in JOURNEY_STAGES else INTENT_JOURNEY.get(intent)
        return intent, journey

    @staticmethod
    def status(result, slots, targets):
        if result.get('status') == 'failed':
            return 'failed'
        if targets is None:
            return 'complete' if slots['primary'] else 'partial'
        need = {'primary': targets.primary, 'secondary': targets.secondary, 'question': targets.question,
                'conversational': targets.conversational, 'short_tail': targets.short_tail}
        return 'complete' if all(len(slots[k]) >= n for k, n in need.items()) else 'partial'

    @staticmethod
    def content_gaps(rows, corpus, limit=15):
        """Competitor-ranked keywords judged relevant (accepted, or valid but over quota) the page copy does not cover."""
        gaps = []
        for record in rows:
            if record.candidate.source != Source.COMPETITOR_REVERSE or record.verdict is None:
                continue
            codes = set(record.verdict.rejection_codes)
            relevant = slot_of(record) or (record.verdict.decision == Decision.REJECT and codes == {RejectionCode.QUOTA_FULL})
            covered = coverage(record.candidate.keyword, corpus)
            if relevant and covered is not None and covered < 0.6:
                gaps.append(record)
        gaps.sort(key=lambda r: -(metric(r, 'search_volume') or 0))
        out = []
        for record in gaps:
            if record.candidate.keyword not in out:
                out.append(record.candidate.keyword)
        return out[:limit]

    def assign_clusters(self, pages, plans, records, business):
        """Pages whose primaries share a content token (brand words excluded) form a cluster; the pillar is the page
        whose primary is the broadest head (fewest tokens, then highest volume, then shallowest URL)."""
        brand = tokens(getattr(business, 'brand', '') or '')
        heads = {pid: tokens(plan.primary, drop=brand) for pid, plan in plans.items() if plan.primary}
        parent = {pid: pid for pid in heads}

        def root(pid):
            while parent[pid] != pid:
                parent[pid] = parent[parent[pid]]
                pid = parent[pid]
            return pid
        ids = list(heads)
        for i, left in enumerate(ids):
            for right in ids[i + 1:]:
                if heads[left] & heads[right]:
                    parent[root(right)] = root(left)
        groups = {}
        for pid in ids:
            groups.setdefault(root(pid), []).append(pid)
        volume = {}
        for record in records:
            if slot_of(record) == 'primary':
                volume[record.candidate.page_id] = metric(record, 'search_volume') or 0
        for plan in plans.values():
            plan.cluster = {'role': 'standalone', 'pillar_url': None, 'related_urls': []}
        for members in groups.values():
            if len(members) < 2:
                continue
            pillar = min(members, key=lambda p: (len(heads[p]), -volume.get(p, 0), pages[p].url.count('/'), pages[p].url))
            shared = sorted(set.intersection(*(heads[p] for p in members)) or set().union(*(heads[p] for p in members)))
            for pid in members:
                related = [pages[p].url for p in members if p != pid]
                plans[pid].cluster = {'role': 'pillar' if pid == pillar else 'cluster', 'pillar_url': pages[pillar].url,
                                      'related_urls': related, 'shared_terms': shared}


def serp_urls(record):
    serp = record.evidence.serp if record.evidence is not None else None
    return {r.url for r in (serp.results if serp else []) if r.result_type == 'organic' and r.position <= 10}
