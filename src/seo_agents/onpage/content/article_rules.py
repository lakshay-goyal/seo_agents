"""Deterministic rules every generated article must pass (each failure raises ValueError with repair feedback).

URL      lowercase ASCII words joined by hyphens, 2-6 words, <= 60 chars, no repeated word, no repeat of the folder
         word (/blog/blog-...), no numeric ids, no year (evergreen URL that survives updates), not an existing URL,
         and it names the topic (shares a content word with the primary keyword).
Title    SEO <title> 30-60 chars with the primary keyword and at least one click-through hook (number, current year,
         brackets, question, benefit after a colon, or a power word); a year must be the current one.
Alt      5-125 chars, >= 3 words, describes the image (no "image of", no file names, no generic words), unique.
Lists    2-10 items, plain text items (no typed "-", "*", "1." markers), 1-40 words each, consistent end punctuation,
         no duplicates.
Tables   >= 2 columns, every row as wide as the header, no empty header cell.
Diagram  Mermaid with a known diagram type on the first line and >= 2 lines.
Facts    every number is the run year, a small count (<= 12), in the client's own corpus, or cited `[S#]` in the same
         text unit to a fresh dated source whose passage contains that number.
Overlap  no 12-word phrase copied from a competitor page (generic 10-word phrases matched too often live).
"""

from __future__ import annotations

from datetime import date
import re

from seo_agents.onpage.checks.check_helpers import WEAK_ALT
from seo_agents.onpage.validation import content_validators as v

ARTICLE_LIMITS = {
    'TITLE_CHARS': (30, 60), 'H1_CHARS': (20, 75), 'DESCRIPTION_CHARS': (120, 160), 'INTRO_WORDS': (40, 70),
    'H2_COUNT': (4, 12), 'WORDS': (900, 3500), 'TAKEAWAYS': (3, 5), 'TAGS': (3, 6), 'FAQ_ITEMS': (0, 6),
    'FAQ_ANSWER_WORDS': (25, 70), 'LIST_ITEMS': (2, 10), 'LIST_ITEM_WORDS': (1, 40), 'SLUG_WORDS': (2, 6),
    'SLUG_CHARS': (5, 60), 'ALT_CHARS': (5, 125), 'INTERNAL_LINKS': (3, 12), 'LINK_FROM': (1, 3),
    'SOURCE_MAX_AGE_DAYS': (0, 730), 'SMALL_NUMBER': (0, 12), 'COPY_SHINGLE_WORDS': (12, 12),
}
SLUG_STOPWORDS = {'a', 'an', 'the', 'and', 'or', 'of', 'to', 'in', 'for', 'on', 'with', 'your', 'our', 'is', 'are', 'how',
                  'what', 'why', 'best', 'guide'}
CTR_POWER = ('how to', 'guide', 'checklist', 'step-by-step', 'step by step', 'examples', 'template', 'mistakes', ' vs ',
             'explained', 'complete', 'proven', 'free', 'quick', 'easy', 'simple', 'tips', 'ways', 'why', 'what is',
             'tutorial', 'beginner', 'comparison', 'strategies', 'tools', 'best', 'top', 'faster', 'without')
MERMAID_TYPES = re.compile(r'^(?:flowchart|graph)\s+(?:TD|TB|LR|RL|BT)\b|^(?:sequenceDiagram|stateDiagram(?:-v2)?|classDiagram|'
                           r'erDiagram|journey|gantt|pie|mindmap|timeline|quadrantChart|gitGraph)\b')
LIST_MARKER = re.compile(r'^\s*(?:[-*•·▪►✓✔]|\d+[.)]|[a-z][.)])\s+')
ALT_LEAD = re.compile(r'^(?:an?\s+)?(?:image|picture|photo|graphic|screenshot|illustration)\s+(?:of|showing)\b', re.I)
CITATION = re.compile(r'\[(S\d+)\]')
IMAGE_FILE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)+\.(?:webp|png|jpe?g|avif|svg)$')
YEAR = re.compile(r'^(?:19|20)\d{2}$')


def render_article_limits(text):
    """Replace `{{A.NAME}}` with "low-high" and `{{A.NAME.max}}` / `{{A.NAME.min}}` with one bound (article skills)."""
    for name, (low, high) in ARTICLE_LIMITS.items():
        text = text.replace('{{A.' + name + '.max}}', str(high)).replace('{{A.' + name + '.min}}', str(low))
        text = text.replace('{{A.' + name + '}}', f'{low}-{high}')
    return text


# ---------------------------------------------------------------- URL
def slug_tokens(slug):
    return [t for t in (slug or '').split('-') if t]


def check_slug(slug, *, primary, prefix, existing_paths=(), taken=()):
    """Return the full URL path (prefix + slug) when the slug passes every URL rule."""
    if not isinstance(slug, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', slug or ''):
        raise ValueError(f'slug "{slug}" must be lowercase ASCII words joined by single hyphens (no spaces, underscores, '
                         'slashes or symbols)')
    low, high = ARTICLE_LIMITS['SLUG_CHARS']
    if not low <= len(slug) <= high:
        raise ValueError(f'slug "{slug}" is {len(slug)} characters; keep it {low}-{high}')
    tokens = slug_tokens(slug)
    low, high = ARTICLE_LIMITS['SLUG_WORDS']
    if not low <= len(tokens) <= high:
        raise ValueError(f'slug "{slug}" has {len(tokens)} words; use {low}-{high} words that name the topic')
    if len(set(tokens)) != len(tokens):
        raise ValueError(f'slug "{slug}" repeats a word; every word may appear once')
    folders = [s for s in (prefix or '').strip('/').split('/') if s]
    clash = [t for t in tokens if t in folders]
    if clash:
        raise ValueError(f'slug "{slug}" repeats the folder word "{clash[0]}" already in {prefix}')
    if any(YEAR.match(t) for t in tokens):
        raise ValueError(f'slug "{slug}" contains a year; keep URLs evergreen so the article can be updated in place')
    if any(re.fullmatch(r'\d{3,}|[0-9a-f]{8,}', t) and not t.isalpha() for t in tokens):
        raise ValueError(f'slug "{slug}" contains an id-like token; use words that describe the content')
    if tokens[0] in SLUG_STOPWORDS - {'how', 'what', 'why', 'best'} or tokens[-1] in SLUG_STOPWORDS:
        raise ValueError(f'slug "{slug}" starts or ends with a filler word; drop it')
    if sum(1 for t in tokens if t in SLUG_STOPWORDS) > 2:
        raise ValueError(f'slug "{slug}" has too many filler words; keep the topic words')
    topic = v.content_tokens(primary or '')
    named = any(t == k or (min(len(t), len(k)) >= 4 and (t.startswith(k) or k.startswith(t)))
                for t in (v.stem(x) for x in tokens) for k in topic)
    if topic and not named:
        raise ValueError(f'slug "{slug}" does not name the topic: include a word from the primary keyword "{primary}"')
    path = '/' + '/'.join(folders + [slug])
    if path in set(existing_paths) or path.rstrip('/') in {p.rstrip('/') for p in existing_paths}:
        raise ValueError(f'URL {path} already exists on the site; choose a different slug')
    if path in set(taken):
        raise ValueError(f'URL {path} is already used by another proposed article')
    return path


# ---------------------------------------------------------------- titles
def ctr_hooks(title, run_year):
    t = ' ' + (title or '').lower() + ' '
    hooks = []
    numbers = [n for n in re.findall(r'\b\d+\b', t) if n != str(run_year)]
    if numbers:
        hooks.append('number')
    if str(run_year) in t:
        hooks.append('year')
    if re.search(r'[\[(].+[\])]', t):
        hooks.append('brackets')
    if t.strip().endswith('?'):
        hooks.append('question')
    if re.search(r':\s|\s[–—|-]\s', t):
        hooks.append('colon')
    hooks += ['power:' + w.strip() for w in CTR_POWER if w in t][:3]
    return hooks


def check_title(title, *, primary, run_year, others=(), competitor_titles=(), name='title', limits='TITLE_CHARS'):
    title = v.text_length(title, *ARTICLE_LIMITS[limits], name)
    if primary and not v.contains_query(title, primary):
        raise ValueError(f'{name} "{title}" must contain the primary keyword "{primary}" (or its words)')
    for year in re.findall(r'\b(?:19|20)\d{2}\b', title):
        if int(year) != run_year:
            raise ValueError(f'{name} mentions {year}; only the current year {run_year} keeps a title fresh')
    lowered = title.lower()
    if lowered in {o.lower() for o in others if o}:
        raise ValueError(f'{name} duplicates an existing page title')
    if any(v.similar(title, c, 0.9) for c in competitor_titles if c):
        raise ValueError(f'{name} is (nearly) a competitor title; write your own angle')
    return title


def check_ctr_title(title, run_year):
    hooks = ctr_hooks(title, run_year)
    if not hooks:
        raise ValueError(f'title "{title}" has no click-through hook: add a number, the current year {run_year}, a bracket '
                         '(e.g. "[Checklist]"), a question, a benefit after a colon, or a power word such as '
                         '"guide", "examples", "step-by-step"')
    return hooks


# ---------------------------------------------------------------- media
def check_alt(alt, name, *, primary=None, seen=None):
    alt = v.text_length(alt, *ARTICLE_LIMITS['ALT_CHARS'], name)
    if WEAK_ALT.match(alt) or ALT_LEAD.match(alt):
        raise ValueError(f'{name} "{alt}" is generic; describe what the image shows (no "image of", no file name)')
    if len(v.words(alt)) < 3:
        raise ValueError(f'{name} "{alt}" is too short; describe the subject and what it shows in 3+ words')
    if primary and v.phrase_count(alt, primary) > 1:
        raise ValueError(f'{name} repeats the primary keyword; use it at most once')
    if seen is not None:
        if alt.lower() in seen:
            raise ValueError(f'{name} reuses another image\'s alt text; every image needs its own description')
        seen.add(alt.lower())
    return alt


def check_mermaid(code, name):
    if not isinstance(code, str) or not code.strip():
        raise ValueError(name + '.code must be Mermaid source')
    lines = [l for l in code.strip().splitlines() if l.strip()]
    if '```' in code:
        raise ValueError(name + '.code must be raw Mermaid without ``` fences')
    if not MERMAID_TYPES.match(lines[0].strip()) or len(lines) < 2:
        raise ValueError(name + '.code must start with a Mermaid diagram type (e.g. "flowchart LR") and have at least 2 lines')
    return code.strip()


# ---------------------------------------------------------------- lists / tables
def check_list(items, name):
    low, high = ARTICLE_LIMITS['LIST_ITEMS']
    if not isinstance(items, list) or not low <= len(items) <= high:
        raise ValueError(f'{name} needs {low}-{high} items')
    cleaned = []
    for i, item in enumerate(items):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f'{name}[{i}] must be non-empty text')
        if LIST_MARKER.match(v.strip_markup(item)):
            raise ValueError(f'{name}[{i}] starts with a typed bullet/number marker; give the item text only '
                             '(the renderer adds the marker)')
        if '\n' in item.strip():
            raise ValueError(f'{name}[{i}] contains a line break; one item = one line')
        v.word_count(item, *ARTICLE_LIMITS['LIST_ITEM_WORDS'], f'{name}[{i}]')
        cleaned.append(item.strip())
    ends = {item.endswith('.') for item in cleaned}
    if len(ends) > 1:
        raise ValueError(f'{name}: end every item with a period or none of them (consistent bullet formatting)')
    if len({v.normalize(i) for i in cleaned}) != len(cleaned):
        raise ValueError(f'{name} has duplicate items')
    return cleaned


def check_table(block, name):
    header, rows = block.get('header'), block.get('rows')
    if not isinstance(header, list) or len(header) < 2 or not all(isinstance(h, str) and h.strip() for h in header):
        raise ValueError(f'{name}: table needs a header of >= 2 non-empty columns')
    if not isinstance(rows, list) or len(rows) < 2:
        raise ValueError(f'{name}: table needs at least 2 rows')
    for r, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != len(header) or not all(isinstance(c, str) for c in row):
            raise ValueError(f'{name}.rows[{r}] must have exactly {len(header)} text cells')
        if any('|' in c or '\n' in c for c in row):
            raise ValueError(f'{name}.rows[{r}]: cells cannot contain "|" or line breaks')
    return block


# ---------------------------------------------------------------- facts
def fresh(source, run_date):
    if not source.get('date'):
        return False
    try:
        age = (date.fromisoformat(run_date[:10]) - date.fromisoformat(source['date'][:10])).days
    except (TypeError, ValueError):
        return False
    return 0 <= age <= ARTICLE_LIMITS['SOURCE_MAX_AGE_DAYS'][1]


def check_facts(text, name, *, corpus, sources, run_date):
    """Numbers need a citation to a fresh source containing them (or the client corpus / run year / small counts).
    Returns the claims found: [{'number', 'source'}]."""
    cited = CITATION.findall(text or '')
    by_id = {s['id']: s for s in sources}
    unknown = [c for c in cited if c not in by_id]
    if unknown:
        raise ValueError(f'{name} cites {unknown[:3]}, which are not in sources[]')
    plain = CITATION.sub(' ', text or '')
    known = v.corpus_numbers(corpus) | {run_date[:4]}
    claims = []
    for token, digits in v.numbers(plain) + v.spelled_numbers(plain):
        bare = token.strip().lower()
        plain_count = bool(re.fullmatch(r'\d+', bare)) or bare.replace('-', ' ').split()[0] in v.UNITS
        small = plain_count and digits.isdigit() and int(digits) <= ARTICLE_LIMITS['SMALL_NUMBER'][1]
        if small or digits in known:
            continue
        if YEAR.match(digits) and int(digits) > int(run_date[:4]):
            raise ValueError(f'{name} mentions the future year {digits}')
        backing = [by_id[c] for c in cited if digits in v.corpus_numbers(by_id[c]['passage'])]
        if not backing:
            raise ValueError(f'{name}: "{token}" is not backed by a cited source. Cite the [S#] whose passage states it '
                             '(in the same paragraph/item/cell), or remove the number. Never invent statistics.')
        # a past year ("published in 2013") is a historical fact: any dated source backs it; statistics need fresh ones
        good = [s for s in backing if fresh(s, run_date) or (YEAR.match(digits) and s.get('date'))]
        if not good:
            raise ValueError(f'{name}: "{token}" comes from {backing[0]["id"]} dated {backing[0].get("date") or "unknown"}; '
                             f'numbers need a dated source from the last {ARTICLE_LIMITS["SOURCE_MAX_AGE_DAYS"][1]} days. '
                             'Use a fresher source or drop the number.')
        claims.append({'number': token, 'source': good[0]['id']})
    return claims


def shingles(text, n):
    tokens = v.words(text)
    return {' '.join(tokens[i:i + n]) for i in range(max(0, len(tokens) - n + 1))}


def check_overlap(text, competitor_shingles, name):
    n = ARTICLE_LIMITS['COPY_SHINGLE_WORDS'][0]
    # titles of works are quoted, not copied: drop "...", “...” and <em>...</em> spans before comparing
    plain = re.sub(r'<em>.*?</em>|"[^"]{3,160}"|“[^”]{3,160}”', ' ', CITATION.sub(' ', text or ''))
    copied = shingles(plain, n) & competitor_shingles
    if copied:
        raise ValueError(f'{name} copies a competitor phrase ("{sorted(copied)[0]}"); write it in your own words')


class ArticleRules:
    """Tool: deterministic URL / title / alt / list / table / diagram / fact / overlap rules for articles."""

    check_slug = staticmethod(check_slug)
    check_title = staticmethod(check_title)
    check_alt = staticmethod(check_alt)
    check_list = staticmethod(check_list)
    check_facts = staticmethod(check_facts)
