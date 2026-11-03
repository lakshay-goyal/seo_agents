"""Composite keyword score 0-100: a weighted sum of normalized 0-1 factors.

The weights are BUSINESS CHOICES for prioritising keywords on this site, not Google ranking factors. Change them here.
Unknown factors (None) are excluded and the remaining weights renormalized; an unknown is never treated as 0.

Factor definitions (each 0-1):
  relevance            mean(business_relevant Noul, content_alignment Score / 3)
  conversion           conversion_potential Score / 3
  ranking_feasibility  mean(1 - KD/100, 1 - serp_authority_share)     (whichever are known)
  intent_match         mean(page_satisfies_intent, resolves_user_purpose, specific_need for long tail)
  demand               log10(volume + 1) / log10(DEMAND_CAP + 1), capped at 1
  trend                TREND_VALUES[trend_direction]
  gsc_opportunity      impressions at position 5-30 = page-2 / low-page-1 opportunity (log-normalized)
"""

from __future__ import annotations

import math

from ..contracts import KeywordEvidence, Tail

WEIGHTS = {
    'relevance': 0.25,
    'conversion': 0.20,
    'ranking_feasibility': 0.20,
    'intent_match': 0.15,
    'demand': 0.10,
    'trend': 0.05,
    'gsc_opportunity': 0.05,
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9

DEMAND_CAP = 10000                  # volume at which demand saturates to 1.0
GSC_IMPRESSIONS_CAP = 1000
GSC_OPPORTUNITY_POSITIONS = (5, 30)
GSC_ALREADY_TOP = 0.3               # position < 5: already ranks well, little extra opportunity
GSC_TOO_DEEP = 0.2                  # position > 30: weak signal
TREND_VALUES = {'rising': 1.0, 'stable': 0.7, 'seasonal': 0.6, 'declining': 0.2}
SCORE_LEVELS = 3                    # Score questions have 4 levels: 0..3


def _mean(values):
    known = [v for v in values if v is not None]
    return sum(known) / len(known) if known else None


def _clip(value):
    return None if value is None else max(0.0, min(1.0, float(value)))


def _noul(answers, qid):
    answer = answers.get(qid)
    return answer.get('noul') if isinstance(answer, dict) else None


def _score(answers, qid):
    answer = answers.get(qid)
    if not isinstance(answer, dict) or answer.get('score') is None:
        return None
    return answer['score'] / SCORE_LEVELS


def factor_scores(evidence: KeywordEvidence, answers: dict | None, *, tail: Tail = Tail.LONG) -> dict[str, float | None]:
    """answers: {qid: normalized answer} for ONE keyword (qid without the k<i>__ prefix); None in heuristic mode."""
    answers = answers or {}
    kd = evidence.value('keyword_difficulty')
    share = evidence.value('serp_authority_share')
    volume = evidence.value('search_volume')
    trend = evidence.value('trend_direction')
    impressions = evidence.value('gsc_impressions')
    position = evidence.value('gsc_position')

    gsc = None
    if impressions is not None and position is not None:
        low, high = GSC_OPPORTUNITY_POSITIONS
        if low <= position <= high:
            gsc = math.log10(impressions + 1) / math.log10(GSC_IMPRESSIONS_CAP + 1)
        else:
            gsc = GSC_ALREADY_TOP if position < low else GSC_TOO_DEEP

    intent_parts = [_noul(answers, 'page_satisfies_intent'), _noul(answers, 'resolves_user_purpose')]
    if tail is Tail.LONG:
        intent_parts.append(_noul(answers, 'specific_need'))

    return {
        'relevance': _clip(_mean([_noul(answers, 'business_relevant'), _score(answers, 'content_alignment')])),
        'conversion': _clip(_score(answers, 'conversion_potential')),
        'ranking_feasibility': _clip(_mean([None if kd is None else 1 - kd / 100, None if share is None else 1 - share])),
        'intent_match': _clip(_mean(intent_parts)),
        'demand': _clip(None if volume is None else math.log10(volume + 1) / math.log10(DEMAND_CAP + 1)),
        'trend': TREND_VALUES.get(trend) if trend is not None else None,
        'gsc_opportunity': _clip(gsc),
    }


def composite_score(factors: dict[str, float | None]) -> tuple[float | None, list[str]]:
    """Weighted sum over KNOWN factors with renormalized weights. Returns (score 0-100 | None, unknown factor names)."""
    unknown = [name for name in WEIGHTS if factors.get(name) is None]
    known_weight = sum(w for name, w in WEIGHTS.items() if factors.get(name) is not None)
    if known_weight <= 0:
        return None, unknown
    total = sum(WEIGHTS[name] * factors[name] for name in WEIGHTS if factors.get(name) is not None)
    return round(100 * total / known_weight, 1), unknown
