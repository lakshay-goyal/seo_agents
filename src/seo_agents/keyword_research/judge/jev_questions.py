"""Every Jev (TypeSafe System One) question, option, criterion and threshold used by the keyword judge. AUDIT HERE.

Principles (TypeSafe skill):
  * One atomic judgment per question, no "or" inside a question. Code (decision.py) makes the decision.
  * Questions point at state by backtick path (`candidates.k0.keyword`, `page.title`, `business.offering`).
  * Numbers are passed as named buckets (Jev is weak at numeric comparison); arithmetic stays in code.
  * Every Choice has a null partner: `user_intent` <-> `intent_clear`; `serp_intent` is only asked when a SERP exists.
  * The generator's rationale is NOT sent (it argues for the keyword and would steer the answer).
  * All questions are asked speculatively for every keyword; decision.py reads only what applies to the kind.

Request layout: one state = {business, page, candidates: {k0: {...}, k1: {...}}}; question ids are `k<i>__<qid>`.
Default is one keyword per request (keywords_per_request=1): the state stays small and focused, which the jaggedness
guide recommends, and a failed call only affects one keyword. Raise it for throughput; MAX_STATE_TOKENS caps a chunk.
"""

from __future__ import annotations

from ..contracts import BusinessProfile, KeywordCandidate, KeywordEvidence, PageBrief

QUESTION_SET_VERSION = 'kw-judge-questions/1'

# ---------------------------------------------------------------- thresholds (Noul / Score readings)
NOUL_YES = 0.7     # >= YES  -> settled yes
NOUL_NO = 0.3      # <= NO   -> settled no;   NO < p < YES -> unsettled -> Decision.REVIEW
SCORE_LOWEST_MAX = 0.5   # Score expectation below this = the lowest level dominates ("does not cover" / "research only")

# ---------------------------------------------------------------- context limits (Jev 1.14: 64k/request, 32k/question)
PAGE_COPY_CHAR_LIMIT = 4000     # roughly what a person reads above and just below the fold to judge a page
PAGE_HEADINGS_LIMIT = 20
HEADING_CHAR_LIMIT = 120
SERP_RESULTS_LIMIT = 10
PEOPLE_ALSO_ASK_LIMIT = 5
MAX_STATE_TOKENS = 24000        # chunk budget (chars / 4 estimate), well under the 32k per-question context

# ---------------------------------------------------------------- options
INTENT_OPTIONS = {
    'informational': 'The searcher wants to learn or understand something; they are not choosing a product yet.',
    'commercial': 'The searcher is comparing products, services or providers before deciding.',
    'transactional': 'The searcher wants to buy, sign up, book, download or contact now.',
    'navigational': 'The searcher wants to reach one specific website, brand or page.',
    'local': 'The searcher wants a place, provider or service near a specific location.',
}

JOURNEY_OPTIONS = {
    'awareness': 'The searcher is discovering a problem or topic and has not looked at solutions yet.',
    'consideration': 'The searcher knows the kind of solution they need and is weighing options.',
    'decision': 'The searcher is ready to pick a provider or product and act.',
    'retention': 'The searcher is already a customer and needs help using what they have.',
}

CONTENT_ALIGNMENT_LEVELS = [
    '`page` does not cover the topic of the search at all.',
    '`page` mentions the topic in passing, without explaining it.',
    '`page` covers the topic partially, as one section among others.',
    '`page` covers the topic as its main subject.',
]

CONVERSION_LEVELS = [
    'The searcher is only researching; there is nothing to buy, book or contact yet.',
    'The searcher is comparing options and may become a customer later.',
    'The searcher is evaluating a specific product or provider like the one in `business`.',
    'The searcher is ready to buy, sign up, book or contact a provider now.',
]

# ---------------------------------------------------------------- question catalogue
# Each entry: (type, instructions template, criteria). `{k}` is replaced by the keyword's state key (k0, k1, ...).
QUESTIONS = {
    'user_intent': ('choice', 'What does a person searching `candidates.{k}.keyword` most likely want?', INTENT_OPTIONS),
    'intent_clear': (
        'noul', 'Does `candidates.{k}.keyword` show what the searcher wants to do?',
        {'true': 'The words make the searcher\'s goal recognizable, even if it is broad.',
         'false': 'The words are ambiguous: several unrelated goals fit them equally well.'}),
    'serp_intent': (
        'choice',
        'Judging only from the titles and result types in `candidates.{k}.serp.results`, what kind of need does the '
        'search engine think a person searching `candidates.{k}.keyword` has?', INTENT_OPTIONS),
    'page_satisfies_intent': (
        'noul', 'Would a person searching `candidates.{k}.keyword` be satisfied landing on `page`?',
        {'true': 'The page is the kind of page they were looking for and they would not go back to the results.',
         'false': 'They would go back to the results to find a different kind of page.'}),
    'business_relevant': (
        'noul', 'Does `candidates.{k}.keyword` describe something that `business.offering` provides?',
        {'true': 'The search is about what the business offers, its subject area, or a problem it solves.',
         'false': 'The search is about something the business does not offer or work on.'}),
    'content_alignment': (
        'score', 'How much does `page` cover the topic of the search `candidates.{k}.keyword`?', CONTENT_ALIGNMENT_LEVELS),
    'page_extendable': (
        'noul', 'Could `page` add a section about `candidates.{k}.keyword` without changing what the page is about?',
        {'true': 'A short added section would fit naturally with the page\'s existing subject.',
         'false': 'Covering it would need a different page or would change the page\'s subject.'}),
    'resolves_user_purpose': (
        'noul', 'Does `page` contain an answer or offer that resolves the need behind `candidates.{k}.keyword`?'),
    'conversion_potential': (
        'score', 'How close to becoming a customer of `business` is a person searching `candidates.{k}.keyword`?',
        CONVERSION_LEVELS),
    'specific_need': (
        'noul', 'Does `candidates.{k}.keyword` express one specific need?',
        {'true': 'A narrow, concrete need, e.g. a task, a problem, a product with attributes.',
         'false': 'A broad topic that covers many different needs.'}),
    'question_answerable': (
        'noul', 'Could `page` answer `candidates.{k}.keyword` in a short, direct passage?'),
    'ai_answer_citable': (
        'noul', 'Would an AI assistant answering `candidates.{k}.keyword` plausibly cite a page like `page`?'),
    'competitor_brand': (
        'noul', 'Does `candidates.{k}.keyword` name a brand or product of a company other than `business.brand`?'),
    'journey_stage': ('choice', 'At which stage of buying is a person searching `candidates.{k}.keyword`?', JOURNEY_OPTIONS),
}

# Nouls that must be settled (outside the NO..YES band) for an ACCEPT; read by decision.py.
KEY_NOULS = ('intent_clear', 'page_satisfies_intent', 'business_relevant', 'resolves_user_purpose', 'competitor_brand')
KIND_NOULS = {'question': 'question_answerable', 'conversational': 'ai_answer_citable'}


# ---------------------------------------------------------------- metric buckets (numbers -> words for Jev)
def volume_bucket(volume):
    if volume is None:
        return 'unknown'
    for limit, label in ((10, 'almost none (<10/month)'), (100, 'low (10-99/month)'), (1000, 'moderate (100-999/month)'),
                         (10000, 'high (1k-10k/month)')):
        if volume < limit:
            return label
    return 'very high (10k+/month)'


def difficulty_bucket(kd):
    if kd is None:
        return 'unknown'
    for limit, label in ((15, 'very easy'), (30, 'easy'), (50, 'medium'), (70, 'hard')):
        if kd < limit:
            return label
    return 'very hard'


# ---------------------------------------------------------------- state builders
def _trim(text, limit):
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def business_state(business: BusinessProfile) -> dict:
    return {'brand': business.brand, 'offering': business.offering, 'site_type': business.site_type,
            'audience': business.audience[:6], 'value_props': business.value_props[:6]}


def page_state(page: PageBrief) -> dict:
    copy, used = [], 0
    for paragraph in page.copy:
        paragraph = _trim(paragraph, PAGE_COPY_CHAR_LIMIT)
        if used + len(paragraph) > PAGE_COPY_CHAR_LIMIT:
            remaining = PAGE_COPY_CHAR_LIMIT - used
            if remaining > 80:
                copy.append(_trim(paragraph, remaining))
            break
        copy.append(paragraph)
        used += len(paragraph)
    return {'url': page.absolute_url or page.url, 'page_type': page.page_type or 'unknown', 'title': page.title or '',
            'description': page.description or '',
            'headings': [_trim(h, HEADING_CHAR_LIMIT) for h in page.headings[:PAGE_HEADINGS_LIMIT]], 'copy_excerpt': copy}


def keyword_state(candidate: KeywordCandidate, evidence: KeywordEvidence | None) -> dict:
    ev = evidence or KeywordEvidence(keyword=candidate.keyword)
    provider_intent = ev.value('provider_intent', 'unknown')
    state = {
        'keyword': candidate.keyword,
        'kind': candidate.kind.value,
        'tail': candidate.tail.value,
        'facts': {
            'monthly_searches': volume_bucket(ev.value('search_volume')),
            'ranking_difficulty': difficulty_bucket(ev.value('keyword_difficulty')),
            'intent_label_from_data_provider': getattr(provider_intent, 'value', provider_intent),
            'search_trend': ev.value('trend_direction', 'unknown'),
        },
    }
    if ev.serp is not None and ev.serp.results:
        state['serp'] = {
            'results': [{'title': r.title, 'result_type': r.result_type, 'site': r.domain,
                         'page_kind': r.page_type_guess or 'unknown'}
                        for r in sorted(ev.serp.results, key=lambda r: r.position)[:SERP_RESULTS_LIMIT]],
            'features': list(ev.serp.features),
            'people_also_ask': list(ev.serp.people_also_ask[:PEOPLE_ALSO_ASK_LIMIT]),
        }
    return state


def has_serp(evidence: KeywordEvidence | None) -> bool:
    return bool(evidence and evidence.serp and evidence.serp.results)


def question_id(key: str, qid: str) -> str:
    return f'{key}__{qid}'


def keyword_questions(key: str, *, with_serp: bool) -> dict[str, dict]:
    """All questions for one keyword (speculative fan-out). Returns plain dicts {type, instructions, criteria}."""
    out = {}
    for qid, spec in QUESTIONS.items():
        if qid == 'serp_intent' and not with_serp:
            continue
        kind, template = spec[0], spec[1]
        question = {'type': kind, 'instructions': template.format(k=key)}
        if len(spec) > 2:
            criteria = spec[2]
            question['criteria'] = list(criteria) if isinstance(criteria, list) else dict(criteria)
        out[question_id(key, qid)] = question
    return out


def build_request(business: BusinessProfile, page: PageBrief,
                  items: list[tuple[KeywordCandidate, KeywordEvidence | None]]) -> tuple[dict, dict, dict[str, str]]:
    """One request for a chunk of keywords sharing the same business + page state.

    Returns (state, questions, key_to_keyword)."""
    candidates, questions, keys = {}, {}, {}
    for index, (candidate, evidence) in enumerate(items):
        key = f'k{index}'
        keys[key] = candidate.keyword
        candidates[key] = keyword_state(candidate, evidence)
        questions.update(keyword_questions(key, with_serp=has_serp(evidence)))
    state = {'business': business_state(business), 'page': page_state(page), 'candidates': candidates}
    return state, questions, keys


def estimate_tokens(obj) -> int:
    import json
    return len(json.dumps(obj, ensure_ascii=False)) // 4


def chunk_items(items, keywords_per_request: int, base_state_tokens: int):
    """Split candidates into chunks of at most `keywords_per_request` that stay within MAX_STATE_TOKENS."""
    chunk, tokens = [], base_state_tokens
    for item in items:
        item_tokens = estimate_tokens(keyword_state(*item)) + 400   # + ~14 short questions
        if chunk and (len(chunk) >= keywords_per_request or tokens + item_tokens > MAX_STATE_TOKENS):
            yield chunk
            chunk, tokens = [], base_state_tokens
        chunk.append(item)
        tokens += item_tokens
    if chunk:
        yield chunk


# ---------------------------------------------------------------- same-intent (mapping / dedup) question
def same_intent_request(pairs: list[tuple[str, str]]) -> tuple[dict, dict, dict[str, tuple[str, str]]]:
    state = {'pairs': {f'p{i}': {'a': a, 'b': b} for i, (a, b) in enumerate(pairs)}}
    questions = {
        f'p{i}__same_intent': {
            'type': 'noul',
            'instructions': f'Would one page satisfy both searches `pairs.p{i}.a` and `pairs.p{i}.b`?',
            'criteria': {'true': 'A person making either search would be happy with the same page.',
                         'false': 'The two searches need different pages.'},
        }
        for i in range(len(pairs))
    }
    return state, questions, {f'p{i}': pair for i, pair in enumerate(pairs)}


# ---------------------------------------------------------------- reading answers (no probability math beyond thresholds)
def noul_band(p) -> str | None:
    """'yes' | 'no' | 'unsettled' | None (not asked / missing)."""
    if p is None:
        return None
    if p >= NOUL_YES:
        return 'yes'
    if p <= NOUL_NO:
        return 'no'
    return 'unsettled'
