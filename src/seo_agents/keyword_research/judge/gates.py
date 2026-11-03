"""Deterministic hard filters that run BEFORE any Jev call (cheap, auditable, metric-only).

A hard gate hit -> Decision.REJECT without asking Jev. A soft flag (MISSING_DATA) -> the keyword is still judged by
Jev but its decision is capped at Decision.REVIEW. Unknown metrics (None) never trigger a gate.

Kind exemptions (documented business choices):
  * CONVERSATIONAL (GEO prompts) have no measurable search volume by nature: exempt from LOW_VOLUME and MISSING_DATA.
  * QUESTION / CONVERSATIONAL target answer boxes and AI answers, which live on zero-click SERPs: exempt from ZERO_CLICK.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..contracts import (BusinessProfile, KeywordCandidate, KeywordEvidence, KeywordKind, KeywordRecord, RejectionCode,
                         ResearchTargets, Tail)

DECLINING_LOW_VOLUME = 100          # declining trend is only fatal when volume is also below this
ZERO_CLICK_MAX = 0.25               # click_opportunity below this -> SERP features absorb the clicks
SERP_AUTHORITY_MAX = 0.7            # serp_authority_share >= this -> top-10 dominated by high-authority domains
LOW_AUTHORITY_LEVELS = {'low'}      # site authority levels for which SERP_DOMINATED applies

STOPWORDS = {'a', 'an', 'the', 'for', 'to', 'of', 'in', 'on', 'and', 'with', 'my', 'your', 'is', 'are', 'how', 'what',
             'do', 'does', 'i', 'can', 'best'}
_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


@dataclass
class GateHit:
    code: RejectionCode
    reason: str
    hard: bool = True               # False = soft flag, decision capped at REVIEW


# ---------------------------------------------------------------- normalization (duplicates / variants)
def _stem(token: str) -> str:
    """Light plural folding (shoes->shoe, boxes->box, agencies->agency, classes->class); 'business'/'status' kept."""
    if len(token) <= 3:
        return token
    if token.endswith('ies'):
        return token[:-3] + 'y'
    if token.endswith(('sses', 'xes', 'zes', 'ches', 'shes')):
        return token[:-2]
    if token.endswith('s') and not token.endswith(('ss', 'us', 'is')):
        return token[:-1]
    return token


def keyword_key(keyword: str) -> frozenset[str]:
    """Normalized token set: lowercase, stopwords dropped, plurals stemmed, order ignored."""
    tokens = [_stem(t.replace("'", '')) for t in _TOKEN.findall(keyword.lower())]
    meaningful = [t for t in tokens if t not in STOPWORDS]
    return frozenset(meaningful or tokens)


def is_variant(a: str, b: str) -> bool:
    return keyword_key(a) == keyword_key(b)


# ---------------------------------------------------------------- provenance in reasons
def cite(evidence: KeywordEvidence, name: str) -> str:
    metric = evidence.metrics.get(name)
    if metric is None or metric.provenance is None:
        return ''
    p = metric.provenance
    return f' ({p.provider}, {str(p.retrieved_at)[:10]})'


def _competitor_terms(business: BusinessProfile) -> list[str]:
    terms = [t.lower().strip() for t in business.excluded_terms if t and t.strip()]
    for domain in business.competitors:
        host = re.sub(r'^https?://', '', domain.lower()).split('/')[0]
        host = host[4:] if host.startswith('www.') else host
        label = host.split('.')[0]
        if len(label) >= 3:
            terms.append(label)
    own = business.brand.lower().strip()
    return [t for t in dict.fromkeys(terms) if t and t != own]


def _contains_term(keyword: str, term: str) -> bool:
    return re.search(r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])', keyword.lower()) is not None


def accepted_on_page(accepted: list[KeywordRecord], page_id: str) -> list[KeywordRecord]:
    out = []
    for record in accepted:
        owner = record.mapped_page_id or record.candidate.page_id
        if owner == page_id and record.final_status not in ('rejected', 'review'):
            out.append(record)
    return out


# ---------------------------------------------------------------- the gates
def run_gates(candidate: KeywordCandidate, evidence: KeywordEvidence, business: BusinessProfile,
              targets: ResearchTargets, accepted: list[KeywordRecord], *, site_authority: str = 'low') -> list[GateHit]:
    hits: list[GateHit] = []
    volume = evidence.value('search_volume')
    kd = evidence.value('keyword_difficulty')
    impressions = evidence.value('gsc_impressions')
    kind = candidate.kind

    if (targets.min_volume is not None and volume is not None and volume < targets.min_volume
            and kind is not KeywordKind.CONVERSATIONAL and not (impressions and impressions >= targets.min_volume)):
        hits.append(GateHit(RejectionCode.LOW_VOLUME,
                            f'search volume {volume}/month < min {targets.min_volume}{cite(evidence, "search_volume")}'))

    max_kd = targets.max_difficulty_short_tail if candidate.tail is Tail.SHORT else targets.max_difficulty
    if max_kd is not None and kd is not None and kd > max_kd:
        hits.append(GateHit(RejectionCode.HIGH_DIFFICULTY,
                            f'KD {kd:g} > max {max_kd} for {candidate.tail.value} tail{cite(evidence, "keyword_difficulty")}'))

    trend = evidence.value('trend_direction')
    if trend == 'declining' and volume is not None and volume < DECLINING_LOW_VOLUME:
        hits.append(GateHit(RejectionCode.DECLINING_TREND,
                            f'trend declining{cite(evidence, "trend_direction")} with only {volume}/month '
                            f'(< {DECLINING_LOW_VOLUME})'))

    for term in _competitor_terms(business):
        if _contains_term(candidate.keyword, term):
            hits.append(GateHit(RejectionCode.COMPETITOR_BRAND, f'contains competitor/excluded term "{term}"'))
            break

    key = keyword_key(candidate.keyword)
    for record in accepted_on_page(accepted, candidate.page_id):
        if record.candidate.keyword != candidate.keyword and keyword_key(record.candidate.keyword) == key:
            hits.append(GateHit(RejectionCode.DUPLICATE,
                                f'variant of already accepted "{record.candidate.keyword}" on this page'))
            break
        if record.candidate.keyword == candidate.keyword:
            hits.append(GateHit(RejectionCode.DUPLICATE, 'already accepted for this page'))
            break

    click = evidence.value('click_opportunity')
    if (click is not None and click < ZERO_CLICK_MAX
            and kind in (KeywordKind.PRIMARY, KeywordKind.SECONDARY)):
        features = ', '.join(evidence.serp.features[:4]) if evidence.serp and evidence.serp.features else 'SERP features'
        hits.append(GateHit(RejectionCode.ZERO_CLICK,
                            f'click opportunity {click:.2f} < {ZERO_CLICK_MAX} ({features}){cite(evidence, "click_opportunity")}'))

    share = evidence.value('serp_authority_share')
    if share is not None and share >= SERP_AUTHORITY_MAX and site_authority in LOW_AUTHORITY_LEVELS:
        hits.append(GateHit(RejectionCode.SERP_DOMINATED,
                            f'{share:.0%} of top-10 on high-authority domains (>= {SERP_AUTHORITY_MAX:.0%}) and site '
                            f'authority is {site_authority}{cite(evidence, "serp_authority_share")}'))

    if (volume is None and kd is None and not impressions and kind is not KeywordKind.CONVERSATIONAL
            and not any(h.hard for h in hits)):
        detail = '; provider errors: ' + '; '.join(evidence.errors[:2]) if evidence.errors else ''
        hits.append(GateHit(RejectionCode.MISSING_DATA,
                            f'volume, difficulty and GSC impressions all unknown{detail}', hard=False))
    return hits
