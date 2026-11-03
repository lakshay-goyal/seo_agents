"""Check 14 (deterministic): thin content, near-duplicate pages, long paragraphs, readability, placeholder copy and
repeated boilerplate in the CURRENT page."""

from __future__ import annotations

import re

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.validation import content_validators as v
from seo_agents.onpage.validation.content_policy import LIMITS
from seo_agents.onpage.checks.check_helpers import page_type, result
from seo_agents.onpage.checks.base_check import OnpageCheck

# ====================================================================== 15. content quality (deterministic)
THIN_WORDS = {'home': 250, 'article': 600, 'about': 150, 'contact': 150, 'collection': 150}
THIN_DEFAULT = 300
PARAGRAPH_MAX_WORDS = (LIMITS.get('PARAGRAPH_MAX_WORDS') or (1, 70))[1]
FLESCH_MIN = 40
FLESCH_MIN_WORDS = 100
WORDS_PER_HEADING_MIN = 20
BOILERPLATE_REPEATS = 3
PLACEHOLDER = re.compile(r'(?i)\b(lorem ipsum|dolor sit amet|your text here|sample text|insert (?:text|content) here|'
                         r'placeholder text)\b')
SENTENCE_SPLIT = re.compile(r'(?<=[.!?])\s+')


def number(item):
    return item if isinstance(item, (int, float)) and not isinstance(item, bool) else 0


def syllables(word):
    word = re.sub(r'[^a-z]', '', word.lower())
    if not word:
        return 0
    groups = re.findall(r'[aeiouy]+', word)
    count = len(groups)
    if word.endswith('e') and not word.endswith(('le', 'ee')) and count > 1:
        count -= 1
    return max(1, count)


def flesch(text):
    """Flesch reading ease (English); None when there is too little text to score."""
    tokens = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]*", text or '')]
    sentences = [s for s in re.split(r'[.!?]+(?:\s|$)', text or '') if re.search(r'[A-Za-z]', s)]
    if len(tokens) < FLESCH_MIN_WORDS or not sentences:
        return None
    total = sum(syllables(w) for w in tokens)
    return round(206.835 - 1.015 * (len(tokens) / len(sentences)) - 84.6 * (total / len(tokens)), 1)


def paragraphs(ctx):
    out = []
    for section in ctx.get('sections') or []:
        for block in (section or {}).get('blocks') or []:
            if isinstance(block, dict) and block.get('tag') in ('p', None) and isinstance(block.get('text'), str) and block['text'].strip():
                out.append(block['text'].strip())
    return out


def prose_paragraphs(ctx):
    """Paragraph blocks only (tag p, or untagged text with sentence punctuation) - nav labels/buttons/cards excluded."""
    out = []
    for section in ctx.get('sections') or []:
        for block in (section or {}).get('blocks') or []:
            if not isinstance(block, dict) or not isinstance(block.get('text'), str) or not block['text'].strip():
                continue
            text = block['text'].strip()
            if block.get('tag') == 'p' or (block.get('tag') is None and re.search(r'[.!?](?:\s|$)', text)):
                out.append(text)
    return out


def texts(ctx):
    copy = [t for t in ctx.get('copy') or [] if isinstance(t, str) and t.strip()]
    return copy or paragraphs(ctx)


def duplicate_peers(ctx):
    """Peers of this page in a near-duplicate cluster from the site pass (matched by page id or URL)."""
    site = ctx.get('site') if isinstance(ctx.get('site'), dict) else {}
    page_id, url = ctx.get('page_id'), ctx.get('url')
    for cluster in site.get('duplicates') or []:
        if not isinstance(cluster, dict):
            continue
        ids = [i for i in cluster.get('page_ids') or [] if isinstance(i, str)]
        urls = [u for u in cluster.get('urls') or [] if isinstance(u, str)]
        if page_id in ids or url in urls or (ctx.get('absolute_url') and ctx['absolute_url'] in urls):
            peers = [u for u in urls if u not in (url, ctx.get('absolute_url'))] or [i for i in ids if i != page_id]
            return peers, cluster.get('method')
    return [], None


def check_content_quality(ctx, prior=None, writer=None):
    prior = prior or {}
    advice = []
    note = lambda topic, text, impact='medium', evidence=None: advice.append(  # noqa: E731
        ops.advice(ctx, 'content_quality', topic, text, impact=impact, evidence=evidence))
    rendering = ctx.get('rendering') if isinstance(ctx.get('rendering'), dict) else {}
    words = int(max(number(ctx.get('main_words')), number(rendering.get('rendered_words'))))
    kind = page_type(ctx, prior)
    threshold = THIN_WORDS.get(kind, THIN_DEFAULT)
    shell_empty = ctx.get('head_scope') == 'shared-shell' and words == 0

    # ---- thin content (an empty shared shell is a prerender problem owned by the technical check)
    if not shell_empty and words < threshold:
        note('content.thin', f'{words} words of main content; a {kind} page usually needs ~{threshold}+ words to fully answer '
             'its query. Add substance (specifics, examples, answers), not filler.', 'high' if words < threshold / 2 else 'medium',
             {'words': words, 'threshold': threshold, 'page_type': kind})

    # ---- near-duplicate cluster
    peers, method = duplicate_peers(ctx)
    if peers:
        note('content.duplicate', f'This page is a near-duplicate of {len(peers)} other page(s); differentiate the content or '
             'consolidate with a canonical/301.', 'high', {'peers': peers[:10], 'method': method})

    # ---- long paragraphs
    long_paragraphs = [p for p in paragraphs(ctx) if len(v.words(p)) > PARAGRAPH_MAX_WORDS]
    if long_paragraphs:
        note('content.long_paragraphs', f'{len(long_paragraphs)} paragraph(s) exceed {PARAGRAPH_MAX_WORDS} words; split them so '
             'each makes one point (easier to scan and to quote).', 'low',
             {'samples': [{'words': len(v.words(p)), 'start': p[:120]} for p in long_paragraphs[:5]]})

    # ---- readability (English only)
    body = ' '.join(prose_paragraphs(ctx))  # flesch() returns None below FLESCH_MIN_WORDS (100) words
    score = flesch(body) if str(ctx.get('lang') or 'en').lower().startswith('en') else None
    if score is not None and score < FLESCH_MIN:
        note('content.readability', f'Flesch reading ease is {score} (hard to read; aim for 50+): shorter sentences and plainer '
             'words.', 'low', {'flesch': score})

    # ---- placeholder copy
    found = sorted({m.group(0).lower() for t in texts(ctx) + paragraphs(ctx) for m in PLACEHOLDER.finditer(t)})
    if found:
        note('content.placeholder', 'Placeholder copy is live on the page (' + ', '.join(found[:5]) + '); replace it before it '
             'gets indexed.', 'high', {'matches': found[:10]})

    # ---- text-to-heading ratio
    headings = [h for h in ctx.get('heading_records') or [] if isinstance(h, dict) and h.get('level')]
    if len(headings) >= 4 and words and words / len(headings) < WORDS_PER_HEADING_MIN:
        note('content.heading_ratio', f'{len(headings)} headings for {words} words (~{round(words / len(headings))} words each); '
             'sections are mostly labels with little explanatory text.', 'low', {'headings': len(headings), 'words': words})

    # ---- repeated boilerplate sentences
    counts, sample = {}, {}
    for text in texts(ctx):
        for sentence in SENTENCE_SPLIT.split(text):
            key = v.normalize(sentence)
            if len(key.split()) >= 5:
                counts[key] = counts.get(key, 0) + 1
                sample.setdefault(key, sentence.strip()[:120])
    repeated = {sample[k]: n for k, n in counts.items() if n > BOILERPLATE_REPEATS}
    if repeated:
        note('content.boilerplate', f'{len(repeated)} sentence(s) repeat more than {BOILERPLATE_REPEATS} times on the page; '
             'repeated boilerplate dilutes the page\'s unique content.', 'low', {'repeated': dict(list(repeated.items())[:5])})

    data = {'words': words, 'threshold': threshold, 'page_type': kind, 'flesch': score, 'long_paragraphs': len(long_paragraphs),
            'duplicate_peers': peers[:10], 'shell_empty': shell_empty}
    return result(data=data, advice=advice)


class ContentQualityCheck(OnpageCheck):
    """Check: Thin content, near-duplicates, long paragraphs, readability, placeholder copy, boilerplate (deterministic)."""

    name = 'content_quality'
    skill = 'content-quality'
    uses_model = False
    depends_on = ()

    def run(self, ctx, prior, writer):
        return check_content_quality(ctx, prior or {}, writer)
