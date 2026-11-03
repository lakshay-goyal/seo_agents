"""Find the patterns competitor pages share (deterministic): depth, structure, formats, sub-topics, title and URL
habits, freshness. The output tells the writer what the ranking pages do, so the client's article can match the
format searchers expect and then go further (gaps), without copying any text.

    analyze(pages, run_date) -> {
        'pages': n, 'word_count': {min, median, max}, 'h2': {median}, 'h3': {median},
        'formats': {table, ordered_list, unordered_list, images, video, faq, howto, code, quote: share 0-1},
        'images_median', 'image_alt_coverage',
        'common_subtopics': [{'heading', 'pages', 'variants'}],   # H2/H3 themes on >= 2 pages
        'question_headings': [...],
        'title_patterns': {number, year, brackets, question, colon, how_to, listicle, guide: share; length_median},
        'slug': {'words_median', 'with_year', 'with_digits_id'},
        'freshness': {'dated', 'newest', 'median_age_days', 'stale_share'},
        'schema_types': {type: pages},
        'benchmarks': {'target_words', 'min_h2', 'needs_table', 'needs_faq', 'needs_images'},
    }
"""

from __future__ import annotations

from datetime import date
import re
from statistics import median

from seo_agents.onpage.validation import content_validators as v

POWER_WORDS = ('guide', 'checklist', 'how to', 'best', 'step-by-step', 'step by step', 'examples', 'template', 'mistakes',
               ' vs ', 'explained', 'complete', 'ultimate', 'proven', 'free', 'quick', 'easy', 'simple', 'tips', 'ways',
               'why', 'what is', 'tutorial', 'beginner', 'comparison', 'checklist', 'strategies', 'tools')
YEAR = re.compile(r'\b(19|20)\d{2}\b')
STALE_DAYS = 730


def share(values):
    values = list(values)
    return round(sum(1 for x in values if x) / len(values), 2) if values else 0.0


def med(values):
    values = [x for x in values if isinstance(x, (int, float))]
    return round(median(values)) if values else None


def title_features(title):
    t = (title or '').lower()
    return {'number': bool(re.search(r'\b\d+\b', YEAR.sub('', t))), 'year': bool(YEAR.search(t)),
            'brackets': bool(re.search(r'[\[(].+[\])]', t)), 'question': t.rstrip().endswith('?'),
            'colon': bool(re.search(r'\s[:|–—-]\s|:\s', t)), 'how_to': 'how to' in t,
            'listicle': bool(re.match(r'^\s*\d+\s', t)) or bool(re.search(r'\b(top|best)\s+\d+', t)),
            'guide': any(w in t for w in ('guide', 'tutorial', 'explained', 'complete'))}


def age_days(iso, run_date):
    try:
        return (date.fromisoformat(run_date[:10]) - date.fromisoformat(iso[:10])).days
    except (TypeError, ValueError):
        return None


def subtopics(pages, min_pages=2, threshold=0.5):
    """Group H2/H3 headings of different pages whose content tokens overlap (Jaccard >= threshold)."""
    groups = []   # [{'tokens': set, 'heading': str, 'pages': set, 'variants': [..]}]
    for index, page in enumerate(pages):
        for h in page.get('outline') or []:
            tokens = set(v.content_tokens(h['text']))
            if not tokens:
                continue
            best = None
            for g in groups:
                score = len(tokens & g['tokens']) / len(tokens | g['tokens'])
                if score >= threshold and (best is None or score > best[0]):
                    best = (score, g)
            if best:
                g = best[1]
                g['pages'].add(index)
                if len(g['variants']) < 4 and h['text'] not in g['variants']:
                    g['variants'].append(h['text'])
            else:
                groups.append({'tokens': tokens, 'heading': h['text'], 'pages': {index}, 'variants': [h['text']]})
    found = [g for g in groups if len(g['pages']) >= min(min_pages, len(pages))]
    found.sort(key=lambda g: (-len(g['pages']), g['heading']))
    return [{'heading': g['heading'], 'pages': len(g['pages']), 'variants': g['variants']} for g in found[:25]]


def analyze(pages, run_date, *, titles=()):
    """pages: extracted competitor records (html_extractor.extract); titles: extra SERP titles for title patterns."""
    pages = [p for p in pages or [] if isinstance(p, dict) and not p.get('error')]
    all_titles = [p.get('title') for p in pages if p.get('title')] + [t for t in titles if t]
    features = [title_features(t) for t in all_titles]
    words = [p.get('word_count') for p in pages]
    counts = [p.get('counts') or {} for p in pages]
    dates = [p.get('modified') or p.get('published') for p in pages]
    ages = [a for a in (age_days(d, run_date) for d in dates if d) if a is not None and a >= 0]
    schema = {}
    for p in pages:
        for kind in p.get('schema_types') or []:
            schema[kind] = schema.get(kind, 0) + 1
    word_median = med(words)
    formats = {'table': share(c.get('table') for c in counts), 'ordered_list': share(c.get('ol') for c in counts),
               'unordered_list': share(c.get('ul') for c in counts), 'images': share(c.get('images') for c in counts),
               'video': share(c.get('video') for c in counts), 'faq': share(p.get('faq') for p in pages),
               'howto': share(p.get('howto') for p in pages), 'code': share(c.get('code') for c in counts),
               'quote': share(c.get('blockquote') for c in counts)}
    coverage = [p.get('image_alt_coverage') for p in pages if p.get('image_alt_coverage') is not None]
    return {
        'pages': len(pages),
        'word_count': {'min': min([w for w in words if w] or [0]), 'median': word_median, 'max': max([w for w in words if w] or [0])},
        'h2': {'median': med(c.get('h2') for c in counts)}, 'h3': {'median': med(c.get('h3') for c in counts)},
        'formats': formats,
        'images_median': med(c.get('images') for c in counts),
        'image_alt_coverage': round(sum(coverage) / len(coverage), 2) if coverage else None,
        'internal_links_median': med(c.get('internal_links') for c in counts),
        'common_subtopics': subtopics(pages),
        'question_headings': list(dict.fromkeys(q for p in pages for q in p.get('question_headings') or []))[:20],
        'title_patterns': {**{k: share(f[k] for f in features) for k in (features[0] if features else title_features(''))},
                           'length_median': med(len(t) for t in all_titles),
                           'power_words': sorted({w.strip() for t in all_titles for w in POWER_WORDS if w in f' {t.lower()} '})},
        'slug': {'words_median': med(len(p.get('slug_words') or []) for p in pages),
                 'with_year': share(any(YEAR.fullmatch(w) for w in p.get('slug_words') or []) for p in pages),
                 'with_digits_id': share(any(re.fullmatch(r'\d{4,}', w) and not YEAR.fullmatch(w)
                                             for w in p.get('slug_words') or []) for p in pages)},
        'freshness': {'dated': share(dates), 'newest': max([d for d in dates if d] or [None], key=lambda d: d or ''),
                      'median_age_days': med(ages), 'stale_share': share(a > STALE_DAYS for a in ages)},
        'schema_types': dict(sorted(schema.items(), key=lambda kv: -kv[1])),
        'benchmarks': {'target_words': word_median, 'min_h2': med(c.get('h2') for c in counts),
                       'needs_table': formats['table'] >= 0.3, 'needs_faq': formats['faq'] >= 0.3,
                       'needs_images': formats['images'] >= 0.5},
    }


def outline_summary(page, limit=20):
    """What the writer may see of one competitor: structure and stats, never body text."""
    return {'url': page.get('url'), 'title': page.get('title'), 'h1': page.get('h1'),
            'description': page.get('description'),
            'outline': [f"H{h['level']}: {h['text']}" for h in (page.get('outline') or [])[:limit]],
            'word_count': page.get('word_count'), 'published': page.get('published'), 'modified': page.get('modified'),
            'formats': {k: (page.get('counts') or {}).get(k) for k in ('table', 'ol', 'ul', 'images', 'video', 'code')},
            'faq': page.get('faq'), 'schema_types': page.get('schema_types')}


class CompetitorPatternAnalyzer:
    """Tool: competitor page records -> shared patterns + benchmarks (deterministic)."""

    analyze = staticmethod(analyze)
    outline_summary = staticmethod(outline_summary)
