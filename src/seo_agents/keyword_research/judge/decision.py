"""The explicit decision table: gates + Jev judgments + metrics -> accept / reject / review. Jev never decides; this does.

Noul bands (jev_questions.NOUL_NO / NOUL_YES = 0.3 / 0.7): NO (<=0.3), UNSETTLED (0.3-0.7), YES (>=0.7).
Score "lowest" = expectation < SCORE_LOWEST_MAX (0.5).  page-type intents = PAGE_TYPE_INTENTS below.

Rows are evaluated top-down. Stage 1 rows are terminal. In stage 2 ALL matching REJECT rows contribute a code; if none
match, the first matching REVIEW row applies; otherwise the keyword is a pre-quota ACCEPT. Stage 3 fills quotas.

| #   | Condition                                                                 | Decision | Code                    |
|-----|---------------------------------------------------------------------------|----------|-------------------------|
| G1  | any hard gate (gates.py)                                                  | REJECT   | gate code(s), no Jev    |
| E1  | Jev call failed, or an expected answer is missing                         | REVIEW   | JUDGE_ERROR (never "no")|
| H1  | no TYPESAFE_API_KEY (heuristic-no-jev mode)                               | REVIEW   | (+ MISSING_DATA if flagged) |
| R1  | page_satisfies_intent NO                                                  | REJECT   | INTENT_MISMATCH         |
| R2  | SERP present and serp_intent not in page-type intents                     | REJECT   | INTENT_MISMATCH         |
| R3  | no SERP, user_intent not in page-type intents, intent_clear YES           | REJECT   | INTENT_MISMATCH         |
| R4  | business_relevant NO                                                      | REJECT   | NOT_RELEVANT            |
| R5  | competitor_brand YES                                                      | REJECT   | COMPETITOR_BRAND        |
| R6  | resolves_user_purpose NO                                                  | REJECT   | UNSATISFIABLE           |
| R7  | kind=question and question_answerable NO                                  | REJECT   | UNSATISFIABLE           |
| R8  | kind=conversational and ai_answer_citable NO                              | REJECT   | UNSATISFIABLE           |
| R9  | content_alignment lowest AND page_extendable NO                           | REJECT   | POOR_CONTENT_ALIGNMENT  |
|     |   (lowest alignment but page_extendable YES -> not a reject: the on-page stage can add a section) |
| R10 | commercial page, kind primary/secondary, conversion_potential lowest      | REJECT   | LOW_CONVERSION          |
| V1  | any KEY_NOUL (intent_clear, page_satisfies_intent, business_relevant,     | REVIEW   | -                       |
|     |   resolves_user_purpose, competitor_brand) UNSETTLED; or the kind Noul    |          |                         |
|     |   UNSETTLED; or alignment lowest and page_extendable UNSETTLED            |          |                         |
| V2  | intent_clear NO (the user_intent Choice is not reliable)                  | REVIEW   | -                       |
| V3  | no SERP and user_intent not in page-type intents and intent_clear not YES | REVIEW   | -                       |
| V4  | user_intent not in page-type intents but serp_intent is (disagreement)    | REVIEW   | -                       |
| V5  | soft gate MISSING_DATA                                                    | REVIEW   | MISSING_DATA            |
| A1  | otherwise                                                                 | ACCEPT*  | - (composite score)     |
| Q1  | *quota for the role full, or short-tail with targets.short_tail == 0      | REJECT   | QUOTA_FULL              |
| Q2  | *variant of a keyword selected earlier in the same batch                  | REJECT   | DUPLICATE               |

Quota fill (Q): pre-quota ACCEPTs sorted by composite score (desc). Roles: long-tail PRIMARY -> primary slot, overflow
to secondary; SECONDARY -> secondary; QUESTION -> question; CONVERSATIONAL -> conversational. Short-tail candidates
only count when targets.short_tail > 0: a short-tail PRIMARY may take the primary slot, otherwise it fills the
short_tail bucket. Remaining capacity = target - already accepted on the page.
"""

from __future__ import annotations

from ..contracts import (BusinessProfile, Decision, JudgeVerdict, KeywordCandidate, KeywordEvidence, KeywordKind,
                         KeywordRecord, PageBrief, RejectionCode, ResearchTargets, Tail)
from . import jev_questions as jq
from .gates import GateHit, accepted_on_page, keyword_key
from .scoring import composite_score, factor_scores

ALL_INTENTS = frozenset(jq.INTENT_OPTIONS)
PAGE_TYPE_INTENTS = {
    'article': {'informational', 'commercial'},
    'docs': {'informational', 'navigational'},
    'product': {'commercial', 'transactional'},
    'collection': {'commercial', 'transactional', 'informational'},
    'category': {'commercial', 'transactional', 'informational'},
    'pricing': {'commercial', 'transactional'},
    'service': {'commercial', 'transactional', 'local'},
    'landing': {'commercial', 'transactional', 'local'},
    'home': {'navigational', 'commercial', 'transactional', 'local', 'informational'},
    'profile': {'navigational', 'informational'},
    'about': {'navigational', 'informational'},
    'contact': {'navigational', 'local', 'transactional'},
}
COMMERCIAL_PAGE_TYPES = {'product', 'collection', 'category', 'pricing', 'service', 'landing'}
COMMERCIAL_SITE_TYPES = {'business', 'ecommerce', 'saas'}


def page_intents(page: PageBrief) -> frozenset[str]:
    return frozenset(PAGE_TYPE_INTENTS.get((page.page_type or 'other').lower(), ALL_INTENTS))


def is_commercial_page(page: PageBrief, business: BusinessProfile) -> bool:
    page_type = (page.page_type or '').lower()
    return page_type in COMMERCIAL_PAGE_TYPES or (page_type == 'home' and business.site_type in COMMERCIAL_SITE_TYPES)


def required_questions(with_serp: bool) -> list[str]:
    return [q for q in jq.QUESTIONS if with_serp or q != 'serp_intent']


def _n(answers, qid):
    a = answers.get(qid)
    return a.get('noul') if isinstance(a, dict) else None


def _choice(answers, qid):
    a = answers.get(qid)
    return a.get('choice') if isinstance(a, dict) else None


def _score(answers, qid):
    a = answers.get(qid)
    return a.get('score') if isinstance(a, dict) else None


def _fmt(qid, p):
    return f'{qid} {p:.2f}'


def _verdict(candidate, decision, *, factors, score, judgments, codes=(), reasons=(), payload_ref=None):
    return JudgeVerdict(keyword=candidate.keyword, page_id=candidate.page_id, decision=decision, composite_score=score,
                        factor_scores=factors, judgments=judgments, rejection_codes=list(codes), reasons=list(reasons),
                        jev_payload_ref=payload_ref)


def gate_verdict(candidate: KeywordCandidate, evidence: KeywordEvidence, hits: list[GateHit]) -> JudgeVerdict:
    """Row G1."""
    hard = [h for h in hits if h.hard]
    factors = factor_scores(evidence, None, tail=candidate.tail)
    return _verdict(candidate, Decision.REJECT, factors=factors, score=None, judgments={'mode': 'gates-only'},
                    codes=[h.code for h in hard], reasons=[f'{h.code.value}: {h.reason}' for h in hard])


def decide(candidate: KeywordCandidate, evidence: KeywordEvidence, page: PageBrief, business: BusinessProfile,
           hits: list[GateHit], answers: dict | None, *, mode: str, model: str | None = None,
           error: str | None = None, payload_ref: str | None = None) -> JudgeVerdict:
    """Rows E1, H1, R1-R10, V1-V5, A1 for one keyword. `answers` = {qid: answer} without the k<i>__ prefix."""
    soft = [h for h in hits if not h.hard]
    factors = factor_scores(evidence, answers, tail=candidate.tail)
    score, unknown = composite_score(factors)
    judgments = {'mode': mode, 'model': model, 'question_set': jq.QUESTION_SET_VERSION, 'unknown_factors': unknown,
                 **(answers or {})}
    common = dict(factors=factors, score=score, judgments=judgments, payload_ref=payload_ref)

    with_serp = jq.has_serp(evidence)
    if mode == 'jev':
        missing = [q for q in required_questions(with_serp) if q not in (answers or {})]
        if error or missing:   # E1
            detail = error or f'missing answers: {", ".join(missing)}'
            judgments['error'] = detail
            return _verdict(candidate, Decision.REVIEW, codes=[RejectionCode.JUDGE_ERROR],
                            reasons=[f'judge_error: Jev call failed, needs a rerun or human review ({detail})'], **common)
    else:                      # H1
        reasons = ['TypeSafe not configured (heuristic-no-jev): intent and relevance need human review']
        reasons += [f'{h.code.value}: {h.reason}' for h in soft]
        return _verdict(candidate, Decision.REVIEW, codes=[h.code for h in soft], reasons=reasons, **common)

    band = {q: jq.noul_band(_n(answers, q)) for q, spec in jq.QUESTIONS.items() if spec[0] == 'noul'}
    allowed = page_intents(page)
    page_type = page.page_type or 'unknown'
    user_intent, serp_intent = _choice(answers, 'user_intent'), _choice(answers, 'serp_intent')
    alignment, conversion = _score(answers, 'content_alignment'), _score(answers, 'conversion_potential')
    alignment_lowest = alignment is not None and alignment < jq.SCORE_LOWEST_MAX
    kind_noul = jq.KIND_NOULS.get(candidate.kind.value)

    rejects: list[tuple[RejectionCode, str]] = []
    if band['page_satisfies_intent'] == 'no':                                                        # R1
        rejects.append((RejectionCode.INTENT_MISMATCH,
                        f'searcher would not be satisfied on this {page_type} page '
                        f'({_fmt("page_satisfies_intent", _n(answers, "page_satisfies_intent"))} <= {jq.NOUL_NO})'))
    if with_serp and serp_intent not in allowed:                                                      # R2
        rejects.append((RejectionCode.INTENT_MISMATCH,
                        f'SERP intent "{serp_intent}" does not fit a {page_type} page (fits: {", ".join(sorted(allowed))})'))
    if not with_serp and user_intent not in allowed and band['intent_clear'] == 'yes':                # R3
        rejects.append((RejectionCode.INTENT_MISMATCH,
                        f'user intent "{user_intent}" does not fit a {page_type} page (no SERP data)'))
    if band['business_relevant'] == 'no':                                                             # R4
        rejects.append((RejectionCode.NOT_RELEVANT,
                        f'not something "{business.offering}" provides '
                        f'({_fmt("business_relevant", _n(answers, "business_relevant"))} <= {jq.NOUL_NO})'))
    if band['competitor_brand'] == 'yes':                                                             # R5
        rejects.append((RejectionCode.COMPETITOR_BRAND,
                        f'names another company\'s brand ({_fmt("competitor_brand", _n(answers, "competitor_brand"))} '
                        f'>= {jq.NOUL_YES})'))
    if band['resolves_user_purpose'] == 'no':                                                         # R6
        rejects.append((RejectionCode.UNSATISFIABLE,
                        f'page has no answer/offer for the need '
                        f'({_fmt("resolves_user_purpose", _n(answers, "resolves_user_purpose"))} <= {jq.NOUL_NO})'))
    if kind_noul and band[kind_noul] == 'no':                                                         # R7 / R8
        rejects.append((RejectionCode.UNSATISFIABLE,
                        f'{candidate.kind.value}: {_fmt(kind_noul, _n(answers, kind_noul))} <= {jq.NOUL_NO}'))
    if alignment_lowest and band['page_extendable'] == 'no':                                          # R9
        rejects.append((RejectionCode.POOR_CONTENT_ALIGNMENT,
                        f'page does not cover the topic (content_alignment {alignment:.2f}/3) and cannot be extended '
                        f'({_fmt("page_extendable", _n(answers, "page_extendable"))} <= {jq.NOUL_NO})'))
    if (is_commercial_page(page, business) and candidate.kind in (KeywordKind.PRIMARY, KeywordKind.SECONDARY)
            and conversion is not None and conversion < jq.SCORE_LOWEST_MAX):                        # R10
        rejects.append((RejectionCode.LOW_CONVERSION,
                        f'research-only search on a commercial {page_type} page (conversion_potential {conversion:.2f}/3)'))
    if rejects:
        codes = list(dict.fromkeys(code for code, _ in rejects))
        return _verdict(candidate, Decision.REJECT, codes=codes,
                        reasons=[f'{code.value}: {reason}' for code, reason in rejects], **common)

    review: list[str] = []
    unsettled = [q for q in jq.KEY_NOULS if band[q] == 'unsettled']
    if kind_noul and band[kind_noul] == 'unsettled':
        unsettled.append(kind_noul)
    if alignment_lowest and band['page_extendable'] == 'unsettled':
        unsettled.append('page_extendable')
    if unsettled:                                                                                     # V1
        review.append('unsettled: ' + ', '.join(_fmt(q, _n(answers, q)) for q in unsettled)
                      + f' (between {jq.NOUL_NO} and {jq.NOUL_YES})')
    if band['intent_clear'] == 'no':                                                                  # V2
        review.append(f'search purpose ambiguous ({_fmt("intent_clear", _n(answers, "intent_clear"))})')
    if not with_serp and user_intent not in allowed and band['intent_clear'] != 'yes':                # V3
        review.append(f'user intent "{user_intent}" may not fit a {page_type} page and there is no SERP to confirm')
    if with_serp and user_intent not in allowed and serp_intent in allowed:                           # V4
        review.append(f'user intent "{user_intent}" disagrees with SERP intent "{serp_intent}" for a {page_type} page')
    if review or soft:                                                                                # V5
        return _verdict(candidate, Decision.REVIEW, codes=[h.code for h in soft],
                        reasons=[f'review: {r}' for r in review] + [f'{h.code.value}: {h.reason}' for h in soft],
                        **common)

    note = f'accepted: composite {score}' + (f' (unknown, excluded: {", ".join(unknown)})' if unknown else '')
    return _verdict(candidate, Decision.ACCEPT, reasons=[note], **common)                             # A1


# ---------------------------------------------------------------- quota fill (Q1, Q2)
_ROLE_TARGET = {
    'primary': lambda t: t.primary, 'secondary': lambda t: t.secondary, 'question': lambda t: t.question,
    'conversational': lambda t: t.conversational, 'short_tail': lambda t: t.short_tail,
}


def _existing_role(record: KeywordRecord) -> str:
    if record.final_status in _ROLE_TARGET:
        return record.final_status
    kind = (record.verdict.assigned_kind if record.verdict and record.verdict.assigned_kind else record.candidate.kind)
    return kind.value


def _roles_for(candidate: KeywordCandidate, targets: ResearchTargets) -> list[str]:
    if candidate.tail is Tail.SHORT:
        if targets.short_tail <= 0:
            return []
        return ['primary', 'short_tail'] if candidate.kind is KeywordKind.PRIMARY else ['short_tail']
    if candidate.kind is KeywordKind.PRIMARY:
        return ['primary', 'secondary']
    return [candidate.kind.value]


def fill_quota(items: list[tuple[KeywordCandidate, JudgeVerdict]], targets: ResearchTargets,
               accepted: list[KeywordRecord], page_id: str) -> None:
    """Mutates verdicts in place: pre-quota ACCEPTs get assigned_kind, or become REJECT QUOTA_FULL / DUPLICATE."""
    existing = accepted_on_page(accepted, page_id)
    remaining = {role: fn(targets) for role, fn in _ROLE_TARGET.items()}
    for record in existing:
        role = _existing_role(record)
        remaining[role] = remaining.get(role, 0) - 1
    taken = {keyword_key(r.candidate.keyword): r.candidate.keyword for r in existing}

    ranked = sorted((pair for pair in items if pair[1].decision is Decision.ACCEPT),
                    key=lambda pair: (-(pair[1].composite_score or 0), pair[0].keyword))
    for candidate, verdict in ranked:
        key = keyword_key(candidate.keyword)
        if key in taken:                                                                              # Q2
            verdict.decision = Decision.REJECT
            verdict.rejection_codes.append(RejectionCode.DUPLICATE)
            verdict.reasons.append(f'duplicate_or_variant: same normalized terms as "{taken[key]}" '
                                   f'(scored higher or accepted earlier)')
            continue
        roles = _roles_for(candidate, targets)
        role = next((r for r in roles if remaining.get(r, 0) > 0), None)
        if role is None:                                                                              # Q1
            verdict.decision = Decision.REJECT
            verdict.rejection_codes.append(RejectionCode.QUOTA_FULL)
            if not roles:
                verdict.reasons.append('quota_full: short-tail quota is 0 (long-tail focus; raise targets.short_tail)')
            else:
                limits = ', '.join(f'{r} {_ROLE_TARGET[r](targets)}' for r in roles)
                verdict.reasons.append(f'quota_full: valid (composite {verdict.composite_score}) but page already has '
                                       f'enough ({limits})')
            continue
        remaining[role] -= 1
        taken[key] = candidate.keyword
        verdict.assigned_kind = KeywordKind.SECONDARY if role == 'secondary' else (
            KeywordKind.PRIMARY if role == 'primary' else candidate.kind)
        verdict.judgments['role'] = role
        verdict.reasons.append(f'role: {role}')
