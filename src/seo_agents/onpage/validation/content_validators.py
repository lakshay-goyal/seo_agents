"""Deterministic guards every generated text must pass. A failure raises ValueError with repair feedback."""

from __future__ import annotations

import html
import re

from seo_agents.onpage.validation.content_policy import LIMITS

STOPWORDS = set('''a an and are as at be by for from has have i i'm in is it its my of on or our that the their this to
was we were what when where which who why will with you your me how can do does about into than then also not but
so if all any more most other some such only own same too very just over under again further once here there'''.split())
GENERIC_ANCHORS = {'click here', 'here', 'read more', 'more', 'learn more', 'link', 'this', 'this link', 'go', 'details',
                   'see more', 'view', 'view more', 'continue', 'page'}
BLOCK_TAGS = {'p', 'ul', 'ol', 'table', 'dl', 'figure', 'blockquote', 'section', 'h3', 'h4'}
VERIFY = re.compile(r'\[VERIFY:[^\]]*\]')
INLINE_LINK = re.compile(r'<a\s+href="([^"]+)"\s*>(.*?)</a>', re.I | re.S)
NUMBER = re.compile(r'(?<![\w/#.-])(?:[$€£₹]\s?)?\d[\d,]*(?:\.\d+)?\s?(?:%|\+|k|m|x)?(?![\w])', re.I)
SELF_REFERENCE = re.compile(r'\b(this page|on this page|click here|see above|below you will find|welcome to)\b', re.I)


def strip_markup(text):
    if not isinstance(text, str):
        return ''
    return html.unescape(re.sub(r'<[^>]+>', ' ', text))


def words(text):
    return re.findall(r"[a-z0-9][a-z0-9'+.#-]*", strip_markup(text).lower())


def normalize(text):
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9%$.+ ]+', ' ', strip_markup(text).lower())).strip()


# ------------------------------------------------------------------ basic shape
def require(answer, *keys):
    if not isinstance(answer, dict):
        raise ValueError('expected a JSON object')
    missing = [k for k in keys if k not in answer]
    if missing:
        raise ValueError('missing keys: ' + ', '.join(missing))


def text_length(text, low, high, name):
    if not isinstance(text, str) or not text.strip():
        raise ValueError(name + ' must be a non-empty string')
    length = len(text.strip())
    if not low <= length <= high:
        raise ValueError(f'{name} is {length} characters; must be {low}-{high}. Rewrite to fit.')
    return text.strip()


def word_count(text, low, high, name):
    count = len(words(text))
    if not low <= count <= high:
        raise ValueError(f'{name} is {count} words; must be {low}-{high}.')


def string_list(value, low, high, name):
    if not isinstance(value, list) or not low <= len(value) <= high or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(f'{name} must be a list of {low}-{high} non-empty strings')
    return [v.strip() for v in value]


def one_of(value, allowed, name):
    if value not in allowed:
        raise ValueError(name + ' must be one of ' + '|'.join(sorted(allowed)))
    return value


# ------------------------------------------------------------------ keyword stuffing (P4)
def phrase_count(text, phrase):
    if not phrase:
        return 0
    return len(re.findall(r'(?<![a-z0-9])' + re.escape(normalize(phrase)) + r'(?![a-z0-9])', normalize(text)))


def density(text):
    tokens = [w for w in words(text) if w not in STOPWORDS and len(w) > 2]
    if not tokens:
        return {}
    counts = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
    return {token: round(100.0 * count / len(tokens), 2) for token, count in counts.items()}


def no_stuffing(text, primary, *, name='text', short=False, protected=()):
    """short=True for title/description/heading: primary phrase at most once."""
    count = phrase_count(text, primary)
    total = len(words(text))
    limit = 1 if short else max(3, total // 100 + 1)
    if count > limit:
        raise ValueError(f'{name} repeats "{primary}" {count} times (max {limit}); use synonyms/entities instead.')
    if not short and total >= 150:
        allowed = {w for p in protected for w in words(p)}
        dense = sorted(((t, p) for t, p in density(text).items() if p > 3.5 and t not in allowed), key=lambda x: -x[1])
        if dense:
            listed = ', '.join(f'"{t}" {p}%' for t, p in dense)
            raise ValueError(f'{name}: term density above 3.5% for {listed}. Cut repeated sentences or merge them; '
                             'keep the client\'s own terms (do not swap them for synonyms). Proper names are exempt.')
    if re.search(r'(\b[\w-]+\b)(?:\s*,\s*\1\b){2,}', text or '', re.I):
        raise ValueError(name + ' contains a repeated keyword list')


# ------------------------------------------------------------------ fact grounding (P6)
def numbers(text):
    plain = VERIFY.sub(' ', strip_markup(text))
    found = []
    for match in NUMBER.finditer(plain):
        token = match.group(0).strip()
        digits = re.sub(r'[^\d.]', '', token).strip('.')
        if digits:
            found.append((token, digits))
    return found


UNITS = {'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
         'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14, 'fifteen': 15, 'sixteen': 16, 'seventeen': 17,
         'eighteen': 18, 'nineteen': 19, 'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50, 'sixty': 60,
         'seventy': 70, 'eighty': 80, 'ninety': 90, 'hundred': 100, 'thousand': 1000, 'million': 1000000,
         'dozen': 12, 'dozens': 12, 'hundreds': 100, 'thousands': 1000, 'millions': 1000000}
# "one" is excluded on purpose: it is a pronoun far more often than a count ("one of the ...").
SPELLED = re.compile(r'\b(' + '|'.join(sorted(UNITS, key=len, reverse=True)) + r')(?:[- ](' +
                     '|'.join(k for k in UNITS if UNITS[k] < 10) + r'))?\b', re.I)


def spelled_numbers(text):
    """Spelled-out counts ("two years", "twenty-five projects") as (token, digits) pairs."""
    plain = VERIFY.sub(' ', strip_markup(text))
    found = []
    for match in SPELLED.finditer(plain):
        value = UNITS[match.group(1).lower()] + (UNITS[match.group(2).lower()] if match.group(2) else 0)
        found.append((match.group(0), str(value)))
    return found


def corpus_numbers(corpus):
    digits = set(re.sub(r'[^\d.]', '', m.group(0)).strip('.') for m in NUMBER.finditer(corpus or ''))
    return digits | {d for _, d in spelled_numbers(corpus or '')}


def ungrounded_numbers(text, corpus, allowed=()):
    """Digits AND spelled-out numbers must appear in the visible-text corpus (audit §10: "two years" passed before)."""
    known = corpus_numbers(corpus) | {str(a) for a in allowed}
    corpus_words = set(words(corpus))
    bad = [token for token, digits in numbers(text) if digits not in known]
    bad += [token for token, digits in spelled_numbers(text)
            if digits not in known and token.lower() not in corpus_words]
    return bad


def grounded(text, corpus, name='text', allowed=()):
    missing = ungrounded_numbers(text, corpus, allowed)
    if missing:
        raise ValueError(f'{name} contains numbers not found in the packet facts: {missing[:6]}. '
                         'Remove them or write [VERIFY: ...] instead (no invented facts).')


# ------------------------------------------------------------------ links / assets / tags (P3, P9)
def inline_links(text):
    return INLINE_LINK.findall(text or '')


def check_anchor(anchor, name='anchor'):
    plain = strip_markup(anchor).strip().lower()
    if not plain or plain in GENERIC_ANCHORS or plain.startswith('http'):
        raise ValueError(f'{name} "{anchor}" is generic or empty; describe the target in 2-6 words')
    if len(plain.split()) > 10:
        raise ValueError(f'{name} "{anchor}" is too long (max 10 words)')


def check_links(text, allowed_urls, name):
    for href, anchor in inline_links(text):
        if href.startswith('/') and href.split('#')[0] not in allowed_urls:
            raise ValueError(f'{name} links to {href}, which is not a known site URL: {sorted(allowed_urls)[:12]}')
        check_anchor(anchor, name + ' anchor')
    if re.search(r'<(?!/?(a|strong|em|code)\b)[a-z]', text or '', re.I):
        raise ValueError(name + ' may only contain inline <a href>, <strong>, <em>, <code> markup')


def block_texts(block):
    """Every visible string inside one typed block."""
    tag = block.get('tag')
    if tag in ('ul', 'ol'):
        return [i for i in block.get('items') or [] if isinstance(i, str)]
    if tag == 'table':
        rows = [c for row in block.get('rows') or [] for c in row if isinstance(c, str)]
        return [block.get('caption') or ''] + [h for h in block.get('header') or [] if isinstance(h, str)] + rows
    if tag == 'dl':
        return [x for item in block.get('items') or [] for x in (item.get('term', ''), item.get('definition', ''))
                if isinstance(item, dict)]
    if tag == 'figure':
        return [block.get('alt') or '', block.get('caption') or '']
    return [block.get('text') or '']


def check_block(block, *, assets, urls, corpus, name, allowed_numbers=()):
    if not isinstance(block, dict):
        raise ValueError(name + ' must be an object')
    tag = block.get('tag')
    if tag not in BLOCK_TAGS:
        raise ValueError(f'{name}.tag "{tag}" must be one of ' + ', '.join(sorted(BLOCK_TAGS)))
    if tag in ('ul', 'ol'):
        items = block.get('items')
        if not isinstance(items, list) or len(items) < 2 or not all(isinstance(i, str) and i.strip() for i in items):
            raise ValueError(f'{name}: {tag} needs >= 2 non-empty string items')
    elif tag == 'table':
        header, rows = block.get('header'), block.get('rows')
        if not isinstance(header, list) or len(header) < 2 or not isinstance(rows, list) or not rows:
            raise ValueError(f'{name}: table needs header (>=2 columns) and rows')
        if any(not isinstance(r, list) or len(r) != len(header) for r in rows):
            raise ValueError(f'{name}: every table row must have {len(header)} cells')
    elif tag == 'dl':
        items = block.get('items')
        if not isinstance(items, list) or not items or not all(isinstance(i, dict) and i.get('term') and i.get('definition') for i in items):
            raise ValueError(f'{name}: dl needs items [{{term, definition}}]')
    elif tag == 'figure':
        if block.get('src') not in assets:
            raise ValueError(f'{name}: figure src "{block.get("src")}" is not an available asset: {sorted(assets)[:10]}')
        text_length(block.get('alt') or '', 5, 125, name + '.alt')
    elif not isinstance(block.get('text'), str) or not block['text'].strip():
        raise ValueError(f'{name}: {tag} needs non-empty text')
    elif tag == 'p' and len(words(block['text'])) > LIMITS['PARAGRAPH_MAX_WORDS'][1]:
        raise ValueError(f'{name}: paragraph is {len(words(block["text"]))} words; split it '
                         f'(max {LIMITS["PARAGRAPH_MAX_WORDS"][1]})')
    for index, text in enumerate(block_texts(block)):
        check_links(text, urls, f'{name}[{index}]')
        grounded(text, corpus, f'{name}[{index}]', allowed_numbers)
    return block


def heading_hierarchy(levels):
    previous = 1
    for level in levels:
        if level > previous + 1:
            raise ValueError(f'heading level jumps from H{previous} to H{level}; do not skip levels')
        previous = level


# ------------------------------------------------------------------ entity repetition (audit §8: name 34x in 943 words)
def entity_count(text, entity):
    if not entity:
        return 0
    return phrase_count(text, entity)


def entity_cap(units, entity, name='text'):
    """units: [(label, text)]. Full entity name at most once per unit and ~once per 150 words overall."""
    if not entity or len(words(entity)) < 2:
        return
    per_unit = LIMITS['ENTITY_PER_SECTION'][1]
    total_words = 0
    total = 0
    for label, text in units:
        count = entity_count(text, entity)
        total += count
        total_words += len(words(text))
        if count > per_unit:
            raise ValueError(f'{name}: {label} names "{entity}" {count} times (max {per_unit} per section). '
                             'After the first mention use a pronoun or the first name.')
    allowed = max(2, total_words // LIMITS['ENTITY_WORDS_PER_MENTION'][1] + 1)
    if total > allowed:
        raise ValueError(f'{name}: "{entity}" appears {total} times in {total_words} words (max {allowed}). '
                         'Use pronouns after the first mention in each section.')


# ------------------------------------------------------------------ client's own terms (audit §10: "web" -> "browser")
SYNONYMS = {
    'web': {'browser', 'internet', 'online'}, 'app': {'application'}, 'apps': {'applications'},
    'engineer': {'developer', 'programmer'}, 'developer': {'engineer', 'programmer'},
    'build': {'craft', 'create'}, 'building': {'crafting', 'creating'}, 'builds': {'crafts', 'creates'},
    'projects': {'works', 'creations'}, 'blog': {'journal'}, 'posts': {'articles', 'entries'},
    'books': {'titles', 'reads'}, 'shop': {'store'}, 'store': {'shop'}, 'products': {'items', 'goods'},
    'customers': {'clients'}, 'clients': {'customers'},
}


def keeps_client_terms(text, corpus, name='text'):
    """Reject a synonym the client never uses when the client's own word for it is on the page."""
    have = set(words(corpus))
    new = set(words(text))
    for own, alternatives in SYNONYMS.items():
        if own in have:
            swapped = sorted(a for a in alternatives if a in new and a not in have)
            if swapped and own not in new:
                raise ValueError(f'{name} replaces the client\'s term "{own}" with "{swapped[0]}"; keep "{own}".')


def similar(a, b, threshold=0.75):
    """Token Jaccard similarity of two short texts (FAQ question vs heading)."""
    left = {w for w in words(a) if w not in STOPWORDS}
    right = {w for w in words(b) if w not in STOPWORDS}
    if not left or not right:
        return normalize(a) == normalize(b)
    return len(left & right) / len(left | right) >= threshold


class ContentValidators:
    """Tool: validators every generated text must pass (grounding, length, stuffing, links, entity cap, client terms)."""

    strip_markup = staticmethod(strip_markup)
    words = staticmethod(words)
    normalize = staticmethod(normalize)
    require = staticmethod(require)
    text_length = staticmethod(text_length)
    word_count = staticmethod(word_count)
    string_list = staticmethod(string_list)
    one_of = staticmethod(one_of)
    phrase_count = staticmethod(phrase_count)
    density = staticmethod(density)
    no_stuffing = staticmethod(no_stuffing)
    numbers = staticmethod(numbers)
    spelled_numbers = staticmethod(spelled_numbers)
    corpus_numbers = staticmethod(corpus_numbers)
    ungrounded_numbers = staticmethod(ungrounded_numbers)
    grounded = staticmethod(grounded)
    inline_links = staticmethod(inline_links)
    check_anchor = staticmethod(check_anchor)
    check_links = staticmethod(check_links)
    block_texts = staticmethod(block_texts)
    check_block = staticmethod(check_block)
    heading_hierarchy = staticmethod(heading_hierarchy)
    entity_count = staticmethod(entity_count)
    entity_cap = staticmethod(entity_cap)
    keeps_client_terms = staticmethod(keeps_client_terms)
    similar = staticmethod(similar)
