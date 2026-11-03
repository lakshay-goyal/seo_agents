"""Fact-check and freshness report for one validated article (deterministic).

Numbers were already forced to cite a fresh source containing them (article_rules.check_facts). This report lists
every claim with its source URL and date, re-checks that each cited sentence is actually supported by the cited
passage (content-word overlap), and records freshness: run date, newest/oldest cited source, review-by date.

verdict: pass | needs_review (weak support or undated sources cited) - never silently 'pass' an unsupported claim.
"""

from __future__ import annotations

from datetime import date, timedelta
import re

from seo_agents.onpage.content.article_rules import CITATION, fresh
from seo_agents.onpage.validation import content_validators as v

SUPPORT_MIN = 0.25
REVIEW_AFTER_DAYS = 180
SENTENCE = re.compile(r'(?<=[.!?])\s+')


def article_texts(article):
    yield 'intro', article.get('intro') or ''
    for i, item in enumerate(article.get('key_takeaways') or []):
        yield f'key_takeaways[{i}]', item
    for i, s in enumerate(article.get('sections') or []):
        for j, b in enumerate(s.get('blocks') or []):
            for text in v.block_texts(b) if b.get('tag') != 'image_brief' else []:
                yield f'sections[{i}].blocks[{j}]', text
    for i, q in enumerate(article.get('faq') or []):
        yield f'faq[{i}]', q.get('answer') or ''


def fact_check(article, sources, run_date):
    by_id = {s['id']: s for s in sources}
    cited_sentences, weak = [], []
    for where, text in article_texts(article):
        for sentence in SENTENCE.split(v.strip_markup(text)):
            ids = CITATION.findall(sentence)
            if not ids:
                continue
            passage = ' '.join(by_id[i]['passage'] for i in ids if i in by_id)
            ratio, _ = v.support_ratio(CITATION.sub(' ', sentence), v.content_tokens(passage))
            row = {'where': where, 'sentence': re.sub(r'\s*\[S\d+\]', '', sentence).strip()[:300], 'sources': ids,
                   'support': round(ratio, 2), 'numbers': [t for t, _ in v.numbers(CITATION.sub(' ', sentence))]}
            cited_sentences.append(row)
            if ratio < SUPPORT_MIN:
                weak.append(row)
    used = [by_id[i] for i in article.get('sources_cited') or [] if i in by_id]
    dates = sorted(s['date'] for s in used if s.get('date'))
    undated = [s['id'] for s in used if not s.get('date')]
    stale = [s['id'] for s in used if s.get('date') and not fresh(s, run_date)]
    review_by = (date.fromisoformat(run_date[:10]) + timedelta(days=REVIEW_AFTER_DAYS)).isoformat()
    verdict = 'pass' if not weak and not undated and not stale else 'needs_review'
    return {
        'verdict': verdict,
        'numeric_claims': article.get('claims') or [],
        'cited_sentences': cited_sentences,
        'weak_support': weak,
        'sources': [{'id': s['id'], 'url': s['url'], 'title': s.get('title'), 'date': s.get('date'),
                     'fresh': fresh(s, run_date)} for s in used],
        'freshness': {'run_date': run_date, 'last_updated': run_date, 'review_by': review_by,
                      'newest_source': dates[-1] if dates else None, 'oldest_source': dates[0] if dates else None,
                      'undated_sources': undated, 'stale_sources': stale},
        'how_checked': 'Every number must be the current year, a small count, in the client\'s own content, or cited [S#] '
                       'to a source dated within 730 days whose passage contains it; cited sentences need >= '
                       f'{SUPPORT_MIN} content-word support from the cited passage.',
    }


class ArticleFactChecker:
    """Tool: article + sources -> claims, support, freshness and a pass | needs_review verdict."""

    check = staticmethod(fact_check)
