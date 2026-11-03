"""Competitor content research: which pages rank, what they contain, which patterns they share, which facts
(with their source and date) a writer may cite.

    research.for_page(ctx)               existing client page: its researched competitor URLs (+ SERP for its primary)
    research.for_keyword(keyword, ...)   new article: live SERP top results (+ fallback URLs)
    -> {'keyword', 'serp': {results, people_also_ask, error}, 'pages': [summary], 'patterns': {...},
        'titles': [...], 'sources': [{id, url, title, date, passage}], 'texts': [...] (memory only), 'errors': [...]}

Fetched pages are cached per URL for the whole run, so a competitor shared by several pages/articles is fetched once.
"""

from __future__ import annotations

import os
import re
import threading
from urllib.parse import urlparse

from seo_agents.onpage.content import competitor_analyzer as analyzer
from seo_agents.onpage.content.competitor_fetcher import CompetitorFetcher, SerpSource
from seo_agents.onpage.content.html_extractor import extract
from seo_agents.onpage.validation import content_validators as v

PASSAGE_WORDS = (12, 70)


def domain(url):
    host = urlparse(url or '').netloc.lower()
    return host[4:] if host.startswith('www.') else host


def passages(page, limit=8):
    """Short fact-bearing passages of one competitor page (numbers first), each with its source URL and date."""
    rows = []
    for block in page.get('blocks') or []:
        text = block['text']
        count = len(text.split())
        if not PASSAGE_WORDS[0] <= count <= PASSAGE_WORDS[1] * 2:
            continue
        if count > PASSAGE_WORDS[1]:
            text = ' '.join(text.split()[:PASSAGE_WORDS[1]])
        rows.append((0 if v.numbers(text) else 1, len(rows), text))
    rows.sort()
    return [text for _, _, text in rows[:limit]]


class CompetitorResearch:
    """Tool: fetch + extract + analyze competitor content (robots-aware, cached). Network calls go through
    `fetcher` (CompetitorFetcher) and `serp` (SerpSource), both injectable."""

    def __init__(self, fetcher=None, serp=None, *, max_pages=None, run_date=None):
        self.fetcher = fetcher or CompetitorFetcher()
        self.serp = serp or SerpSource()
        self.max_pages = int(max_pages or os.getenv('SEO_CONTENT_COMPETITORS', '5'))
        self.run_date = run_date
        self._pages = {}
        self._lock = threading.Lock()

    def page(self, url):
        with self._lock:
            if url in self._pages:
                return self._pages[url]
        fetched = self.fetcher.fetch(url)
        if fetched.get('error'):
            record = {'url': url, 'error': fetched['error']}
        else:
            record = extract(fetched.get('html') or '', fetched.get('final_url') or url, last_modified=fetched.get('last_modified'))
            record['url'] = url
            record['fetched_at'] = fetched.get('fetched_at')
        with self._lock:
            self._pages[url] = record
        return record

    def collect(self, urls, *, keyword=None, serp=None, own_domain=None, run_date=None):
        own = domain(own_domain) if own_domain else None
        wanted, seen_domains = [], set()
        for url in urls:
            d = domain(url)
            if not isinstance(url, str) or not url.startswith('http') or (own and d == own) or d in seen_domains:
                continue
            seen_domains.add(d)  # one page per competitor domain: patterns across sites, not one site's template
            wanted.append(url)
            if len(wanted) >= self.max_pages:
                break
        pages = [self.page(u) for u in wanted]
        ok = [p for p in pages if not p.get('error')]
        run_date = run_date or self.run_date
        titles = [r.get('title') for r in (serp or {}).get('results') or [] if r.get('title')]
        sources, texts = [], []
        for page in ok:
            date = page.get('modified') or page.get('published')
            for passage in passages(page):
                sources.append({'id': f'S{len(sources) + 1}', 'url': page['url'], 'title': page.get('title'), 'date': date,
                                'passage': passage})
            texts.append(' '.join(b['text'] for b in page.get('blocks') or []))
        return {
            'keyword': keyword,
            'serp': {k: (serp or {}).get(k) for k in ('results', 'people_also_ask', 'features', 'error')} if serp else None,
            'pages': [analyzer.outline_summary(p) if not p.get('error') else {'url': p['url'], 'error': p['error']} for p in pages],
            'patterns': analyzer.analyze(ok, run_date or '1970-01-01', titles=titles),
            'titles': list(dict.fromkeys([p.get('title') for p in ok if p.get('title')] + titles))[:15],
            'sources': sources[:30],
            'texts': texts,
            'errors': [f"{p['url']}: {p['error']}" for p in pages if p.get('error')],
        }

    def for_page(self, ctx, market=None):
        plan = ctx.get('keyword_plan') or {}
        urls = list(plan.get('competitor_urls') or [])
        serp = None
        if plan.get('primary'):
            serp = self.serp.top(plan['primary'], market, own_domain=ctx.get('origin'))
            urls += [r['url'] for r in serp.get('results') or [] if not r.get('aggregator')]
        if not urls:
            return None
        return self.collect(urls, keyword=plan.get('primary'), serp=serp, own_domain=ctx.get('origin'), run_date=ctx.get('run_date'))

    def for_keyword(self, keyword, *, market=None, fallback_urls=(), own_domain=None, run_date=None):
        serp = self.serp.top(keyword, market, own_domain=own_domain)
        urls = [r['url'] for r in serp.get('results') or [] if not r.get('aggregator')] + list(fallback_urls or [])
        return self.collect(urls, keyword=keyword, serp=serp, own_domain=own_domain, run_date=run_date)

    def fetched_pages(self):
        """Every competitor page fetched this run, without its body text (for the output report)."""
        return [analyzer.outline_summary(p) | {'fetched_at': p.get('fetched_at')} if not p.get('error')
                else {'url': p['url'], 'error': p['error']} for p in self._pages.values()]


def page_insights(research):
    """The slice of a research result the page-plan writer sees (structure + patterns, never competitor text)."""
    if not research:
        return None
    pattern = research['patterns']
    return {'pages_analyzed': pattern['pages'], 'word_count': pattern['word_count'], 'h2_median': pattern['h2']['median'],
            'formats': pattern['formats'], 'common_subtopics': pattern['common_subtopics'][:10],
            'question_headings': pattern['question_headings'][:8], 'titles': research['titles'][:8],
            'title_patterns': pattern['title_patterns'],
            'people_also_ask': ((research.get('serp') or {}).get('people_also_ask') or [])[:6]}


def clean_keyword(text):
    return re.sub(r'\s+', ' ', str(text or '')).strip().lower()
