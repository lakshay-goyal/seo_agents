"""Deterministic validators for generator output (business profile + keyword candidates).

The numeric limits live in `LIMITS` and are rendered into the prompts ({{NAME}} placeholders), so the prompt and the
validator can never disagree. A validator either returns clean objects or raises ValueError with a message the repair
loop sends back to the model.

Candidate policy:
  * hard failure (raise -> repair turn): answer shape, any metric field / numeric value, an invalid page intent,
    a needed kind left uncovered, or more than MAX_INVALID_RATIO of the candidates invalid;
  * otherwise invalid candidates are dropped (listed in `CandidateBatch.dropped`) and the valid ones returned.
    Repeats (already accepted/rejected keywords, duplicates) never count toward the invalid ratio.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import re
import unicodedata

from seo_agents.keyword_research.contracts import (BusinessProfile, Intent, KeywordCandidate, KeywordKind, Market,
                                                   PageBrief, Source, Tail)

LIMITS = {
    'LONG_TAIL_MIN_WORDS': 3,          # Tail.LONG = >= 3 words
    'SHORT_TAIL_MAX_WORDS': 2,         # Tail.SHORT = 1-2 words (only when needs['short_tail'] > 0)
    'MAX_WORDS': 10,                   # primary / secondary / question
    'CONVERSATIONAL_MAX_WORDS': 16,    # GEO prompts are full sentences
    'RATIONALE_MAX_CHARS': 240,
    'MAX_INVALID_RATIO': 0.25,
    'OFFERING_MAX_CHARS': 300,
    'PROFILE_LIST_MAX': 12,
}

KINDS = [k.value for k in KeywordKind]
INTENTS = [i.value for i in Intent]
TAILS = [t.value for t in Tail]
NEED_KEYS = KINDS + ['short_tail']
SITE_TYPES = ('personal', 'business', 'ecommerce', 'publisher', 'docs', 'saas', 'other')

# Metrics come from providers only; the model must never output them.
METRIC_FIELDS = {'volume', 'search_volume', 'monthly_volume', 'searches', 'difficulty', 'keyword_difficulty', 'kd',
                 'cpc', 'competition', 'ads_competition', 'traffic', 'clicks', 'impressions', 'ctr', 'position', 'rank',
                 'score', 'opportunity'}
CANDIDATE_FIELDS = {'keyword', 'kind', 'tail', 'intent', 'rationale', 'adapted_from'}

# Which candidate intents a page with a given intent can satisfy (the page decides, not the keyword).
COMPATIBLE_INTENTS = {
    'informational': {'informational', 'commercial'},
    'commercial': {'commercial', 'informational', 'transactional'},
    'transactional': {'transactional', 'commercial'},
    'navigational': {'navigational', 'informational', 'commercial'},
    'local': {'local', 'commercial', 'transactional'},
}
# Page intents that are plausible for a page type (unknown / generic page types allow every intent).
PAGE_TYPE_INTENTS = {
    'article': {'informational', 'commercial'},
    'blog': {'informational', 'commercial'},
    'post': {'informational', 'commercial'},
    'docs': {'informational'},
    'guide': {'informational', 'commercial'},
    'product': {'transactional', 'commercial'},
    'pricing': {'transactional', 'commercial'},
    'checkout': {'transactional'},
    'contact': {'navigational', 'local', 'transactional'},
    'location': {'local', 'navigational', 'transactional'},
}

QUESTION_WORDS = {
    'en': {'who', 'what', 'when', 'where', 'why', 'how', 'which', 'whose', 'whom', 'can', 'could', 'is', 'are', 'was',
           'were', 'do', 'does', 'did', 'should', 'will', 'would', 'has', 'have', 'may', 'shall'},
    'es': {'qué', 'que', 'cómo', 'como', 'cuál', 'cuáles', 'cuándo', 'dónde', 'por', 'quién', 'cuánto', 'cuánta', 'es', 'puedo'},
    'de': {'was', 'wie', 'warum', 'wann', 'wo', 'wer', 'welche', 'welcher', 'welches', 'kann', 'ist', 'sind', 'gibt'},
    'fr': {'quoi', 'que', 'qu', 'comment', 'pourquoi', 'quand', 'où', 'qui', 'quel', 'quelle', 'quels', 'quelles', 'est'},
    'pt': {'o', 'que', 'como', 'qual', 'quais', 'quando', 'onde', 'por', 'quem', 'quanto', 'é', 'posso'},
    'it': {'cosa', 'che', 'come', 'perché', 'quando', 'dove', 'chi', 'quale', 'quali', 'quanto', 'è', 'posso'},
}
# Expected Unicode script(s) per page language (anything not listed is a Latin-script language).
LANG_SCRIPTS = {
    'ru': {'CYRILLIC'}, 'uk': {'CYRILLIC'}, 'bg': {'CYRILLIC'}, 'sr': {'CYRILLIC', 'LATIN'}, 'mk': {'CYRILLIC'},
    'be': {'CYRILLIC'}, 'kk': {'CYRILLIC'}, 'el': {'GREEK'}, 'ar': {'ARABIC'}, 'fa': {'ARABIC'}, 'ur': {'ARABIC'},
    'he': {'HEBREW'}, 'zh': {'CJK'}, 'ja': {'CJK', 'HIRAGANA', 'KATAKANA'}, 'ko': {'HANGUL'},
    'hi': {'DEVANAGARI'}, 'mr': {'DEVANAGARI'}, 'ne': {'DEVANAGARI'}, 'th': {'THAI'}, 'bn': {'BENGALI'},
    'ta': {'TAMIL'}, 'te': {'TELUGU'},
}
URL_PATTERN = re.compile(r'https?://|www\.|\b[\w-]+\.(?:com|net|org|io|co|dev|app|ai|uk|de|in|us|shop|store|xyz)\b')
PUNCT_EDGES = '\'"`.,;:!?¿¡()[]{}<>|/\\-–—_*#@'


# ---------------------------------------------------------------- normalization helpers
def normalize_keyword(text):
    """NFKC, lowercase, straight quotes, single spaces, no edge punctuation ('How to X?' -> 'how to x')."""
    text = unicodedata.normalize('NFKC', str(text or '')).replace('’', "'").replace('‘', "'")
    text = re.sub(r'\s+', ' ', text.lower()).strip()
    return text.strip(PUNCT_EDGES + ' ').strip()


def word_count(keyword):
    return len(keyword.split())


def base_lang(lang):
    return (lang or 'en').split('-')[0].split('_')[0].lower() or 'en'


def letter_scripts(text):
    scripts = set()
    for char in text:
        if char.isalpha():
            try:
                scripts.add(unicodedata.name(char).split()[0])
            except ValueError:
                continue
    return scripts


def matches_language(keyword, lang):
    """Script check: Latin-script languages use only Latin letters; others need at least one native-script letter."""
    expected = LANG_SCRIPTS.get(base_lang(lang))
    scripts = letter_scripts(keyword)
    if not scripts:
        return False
    if expected is None:
        return scripts <= {'LATIN'}
    return bool(scripts & expected) and scripts <= expected | {'LATIN'}


def reads_as_question(raw, keyword, lang):
    if '?' in str(raw) or str(raw).lstrip().startswith('¿'):
        return True
    first = keyword.split(' ', 1)[0].replace("'", ' ').split(' ')[0]
    words = QUESTION_WORDS.get(base_lang(lang), set()) | QUESTION_WORDS['en']
    return first in words


def domain_label(domain):
    """'https://www.hub-spot.com/x' -> 'hub spot' (brand-ish label of a competitor domain)."""
    host = re.sub(r'^https?://', '', str(domain or '').lower()).split('/')[0]
    host = re.sub(r'^www\d?\.', '', host)
    label = host.split('.')[0] if '.' in host else host
    return label.replace('-', ' ').strip()


def contains_phrase(keyword, phrase):
    phrase = normalize_keyword(phrase)
    if not phrase:
        return False
    return re.search(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', keyword) is not None or \
        (' ' in phrase and phrase.replace(' ', '') in keyword.split())


def competitor_terms(business, competitor_keywords=()):
    """Normalized competitor brand terms: excluded_terms, competitor domains and competitor-keyword source domains."""
    own = normalize_keyword(business.brand) if business else ''
    terms = set()
    for term in (business.excluded_terms if business else []):
        terms.add(normalize_keyword(term))
    domains = list(business.competitors if business else []) + [c.competitor_url for c in competitor_keywords or []
                                                                if getattr(c, 'competitor_url', None)]
    for domain in domains:
        label = domain_label(domain)
        if len(label) >= 3:
            terms.add(normalize_keyword(label))
    return {t for t in terms if t and t != own and t not in own}


# ---------------------------------------------------------------- metric guard
def metric_errors(obj, where='answer'):
    """Any metric-named key or numeric value is an error (the model never outputs metrics)."""
    errors = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            name = str(key).lower()
            if name in METRIC_FIELDS or any(name.startswith(m + '_') or name.endswith('_' + m) for m in METRIC_FIELDS):
                errors.append(f'{where}: metric field "{key}" is not allowed (metrics come from providers)')
            elif isinstance(value, (int, float)) and not isinstance(value, bool) and where != 'answer':
                errors.append(f'{where}: numeric value in "{key}" is not allowed')
            elif isinstance(value, (dict, list)):
                errors.extend(metric_errors(value, where + '.' + str(key)))
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            errors.extend(metric_errors(item, f'{where}[{index}]'))
    return errors


# ---------------------------------------------------------------- business profile
def str_list(answer, key, limit=LIMITS['PROFILE_LIST_MAX']):
    value = answer.get(key) or []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f'"{key}" must be a list of strings')
    seen, out = set(), []
    for item in value:
        item = re.sub(r'\s+', ' ', item).strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out[:limit]


def packet_text(site_packet):
    return json.dumps(site_packet, ensure_ascii=False, default=str).casefold()


def validate_business_profile(answer, site_packet, market=None):
    """Answer dict -> BusinessProfile grounded in the site packet (brand and competitors must appear in it)."""
    if not isinstance(answer, dict):
        raise ValueError('answer must be a JSON object')
    errors = metric_errors(answer)
    text = packet_text(site_packet)
    brand = re.sub(r'\s+', ' ', str(answer.get('brand') or '')).strip()
    if not brand:
        errors.append('"brand" is required')
    elif brand.casefold() not in text:
        errors.append(f'brand "{brand}" does not appear in the packet; copy it exactly as written there')
    site_type = str(answer.get('site_type') or '').strip().lower()
    if site_type not in SITE_TYPES:
        errors.append(f'"site_type" must be one of {", ".join(SITE_TYPES)}')
    offering = re.sub(r'\s+', ' ', str(answer.get('offering') or '')).strip()
    if not offering:
        errors.append('"offering" is required (one sentence)')
    elif len(offering) > LIMITS['OFFERING_MAX_CHARS']:
        errors.append(f'"offering" must be <= {LIMITS["OFFERING_MAX_CHARS"]} chars')
    lists = {}
    for key in ('audience', 'value_props', 'seed_topics', 'competitors', 'excluded_terms'):
        try:
            lists[key] = str_list(answer, key)
        except ValueError as error:
            errors.append(str(error))
            lists[key] = []
    for competitor in lists['competitors']:
        if competitor.casefold() not in text and domain_label(competitor) not in text:
            errors.append(f'competitor "{competitor}" is not mentioned in the packet; list only competitors named there')
    if not lists['seed_topics']:
        errors.append('"seed_topics" needs at least one topic taken from the packet')
    if errors:
        raise ValueError('; '.join(errors[:10]))
    excluded = list(lists['excluded_terms'])
    for competitor in lists['competitors']:
        label = domain_label(competitor)
        if label and label.lower() not in {e.lower() for e in excluded} and label.lower() != brand.lower():
            excluded.append(label)
    return BusinessProfile(brand=brand, site_type=site_type, offering=offering, audience=lists['audience'],
                           value_props=lists['value_props'], seed_topics=lists['seed_topics'],
                           competitors=lists['competitors'], excluded_terms=excluded, market=market or Market())


# ---------------------------------------------------------------- candidates
@dataclass
class CandidateBatch:
    candidates: list[KeywordCandidate]
    page_intent: str
    dropped: list[str] = field(default_factory=list)   # per-candidate errors of dropped candidates


def clean_needs(needs):
    return {key: max(0, int((needs or {}).get(key) or 0)) for key in NEED_KEYS}


def allowed_page_intents(page_type):
    return PAGE_TYPE_INTENTS.get(str(page_type or '').strip().lower(), set(INTENTS))


def candidate_error(item, *, page, needs, blocked, seen, competitor_brands, adaptable, page_intent):
    """(normalized keyword, error or None) for one raw candidate."""
    if not isinstance(item, dict):
        return None, 'candidate must be an object'
    raw = item.get('keyword')
    if not isinstance(raw, str) or not raw.strip():
        return None, 'keyword must be a non-empty string'
    keyword = normalize_keyword(raw)
    label = f'"{keyword}"'
    extra = set(item) - CANDIDATE_FIELDS
    if extra:
        return keyword, f'{label}: unknown fields {sorted(extra)}'
    kind, tail, intent = item.get('kind'), item.get('tail'), item.get('intent')
    if kind not in KINDS:
        return keyword, f'{label}: kind must be one of {KINDS}'
    if tail not in TAILS:
        return keyword, f'{label}: tail must be one of {TAILS}'
    if intent not in INTENTS:
        return keyword, f'{label}: intent must be one of {INTENTS}'
    words = word_count(keyword)
    max_words = LIMITS['CONVERSATIONAL_MAX_WORDS'] if kind == 'conversational' else LIMITS['MAX_WORDS']
    if words > max_words:
        return keyword, f'{label}: {words} words, max {max_words} for {kind}'
    if words <= LIMITS['SHORT_TAIL_MAX_WORDS']:
        if needs['short_tail'] <= 0:
            return keyword, f'{label}: {words} words is short tail, which was not requested; use >= {LIMITS["LONG_TAIL_MIN_WORDS"]} words'
        if kind not in ('primary', 'secondary'):
            return keyword, f'{label}: short tail is only allowed for primary/secondary'
        if tail != 'short':
            return keyword, f'{label}: {words} words must have tail "short"'
    elif tail != 'long':
        return keyword, f'{label}: {words} words must have tail "long"'
    if needs.get(kind, 0) <= 0 and not (tail == 'short' and needs['short_tail'] > 0):
        return keyword, f'{label}: kind "{kind}" is not needed this iteration (needs: {needs})'
    if keyword in blocked:
        return keyword, f'{label}: already accepted or rejected; never re-propose it'
    if keyword in seen:
        return keyword, f'{label}: duplicate'
    if URL_PATTERN.search(keyword) or URL_PATTERN.search(raw.lower()):
        return keyword, f'{label}: URLs and domains are not keywords'
    brand = next((term for term in competitor_brands if contains_phrase(keyword, term)), None)
    if brand:
        return keyword, f'{label}: contains competitor brand "{brand}"'
    if not matches_language(keyword, page.lang):
        return keyword, f'{label}: not in the page language ({page.lang})'
    if kind == 'question' and not reads_as_question(raw, keyword, page.lang):
        return keyword, f'{label}: a question keyword must read as a question (who/what/how/why/which/can/is ...)'
    if intent not in COMPATIBLE_INTENTS[page_intent]:
        return keyword, (f'{label}: intent "{intent}" cannot be satisfied by a {page_intent} page '
                         f'(allowed: {sorted(COMPATIBLE_INTENTS[page_intent])})')
    rationale = item.get('rationale')
    if not isinstance(rationale, str) or not rationale.strip():
        return keyword, f'{label}: rationale is required'
    if len(rationale.strip()) > LIMITS['RATIONALE_MAX_CHARS']:
        return keyword, f'{label}: rationale must be <= {LIMITS["RATIONALE_MAX_CHARS"]} chars'
    adapted = item.get('adapted_from')
    if adapted not in (None, ''):
        if not isinstance(adapted, str) or normalize_keyword(adapted) not in adaptable:
            return keyword, f'{label}: adapted_from must be one of competitor_keywords (or null)'
    return keyword, None


def validate_candidates(answer, *, page: PageBrief, business: BusinessProfile, needs, competitor_keywords=(),
                        blocked=(), limit=25, iteration=0):
    """Answer {'page_intent', 'candidates': [...]} -> CandidateBatch; raises ValueError for the repair loop."""
    if not isinstance(answer, dict):
        raise ValueError('answer must be a JSON object')
    needs = clean_needs(needs)
    errors = metric_errors(answer)
    page_intent = answer.get('page_intent')
    if page_intent not in INTENTS:
        errors.append(f'"page_intent" must be one of {INTENTS}')
    elif page_intent not in allowed_page_intents(page.page_type):
        errors.append(f'"page_intent" {page_intent} does not fit a {page.page_type} page '
                      f'(allowed: {sorted(allowed_page_intents(page.page_type))})')
    items = answer.get('candidates')
    if not isinstance(items, list) or not items:
        errors.append('"candidates" must be a non-empty list')
    if errors:
        raise ValueError('; '.join(errors[:10]))

    blocked = {normalize_keyword(k) for k in blocked}
    adaptable = {normalize_keyword(c.keyword): c for c in competitor_keywords or []}
    brands = competitor_terms(business, competitor_keywords)
    seen, valid, dropped, repeats = set(), [], [], 0
    for item in items:
        keyword, error = candidate_error(item, page=page, needs=needs, blocked=blocked, seen=seen,
                                         competitor_brands=brands, adaptable=adaptable, page_intent=page_intent)
        if error:
            dropped.append(error)
            # Repeats (already accepted/rejected, duplicates) are dropped without forcing a repair turn;
            # coverage below still forces one when they leave a needed kind empty.
            repeats += keyword in blocked or keyword in seen
            continue
        seen.add(keyword)
        adapted = adaptable.get(normalize_keyword(item.get('adapted_from') or ''))
        valid.append(KeywordCandidate(
            keyword=keyword, page_id=page.page_id, kind=KeywordKind(item['kind']),
            source=Source.COMPETITOR_REVERSE if adapted else Source.LLM_SEED, tail=Tail(item['tail']),
            hypothesized_intent=Intent(item['intent']), rationale=item['rationale'].strip(), iteration=iteration,
            competitor_url=adapted.competitor_url if adapted else None))

    problems = []
    invalid = len(dropped) - repeats
    if invalid > math.floor(len(items) * LIMITS['MAX_INVALID_RATIO']):
        problems.append(f'{invalid} of {len(items)} candidates are invalid')
    wanted = sum(needs[k] for k in KINDS)
    for kind in KINDS:
        if needs[kind] <= 0:
            continue
        have = sum(1 for c in valid if c.kind.value == kind)
        required = needs[kind] if limit >= wanted else 1
        if have < required:
            problems.append(f'need {required} valid "{kind}" candidates, got {have}')
    if needs['short_tail'] > 0 and not any(c.tail is Tail.SHORT for c in valid):
        problems.append('short tail was requested but no valid short-tail candidate was given')
    if problems:
        raise ValueError('; '.join(problems + dropped[:8]))
    return CandidateBatch(candidates=valid[:max(1, limit)], page_intent=page_intent, dropped=dropped)
