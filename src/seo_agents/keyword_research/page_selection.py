"""Exploration JSON (seo_reports/) -> one contracts.PageBrief per selectable page, plus the site packet the business
profiler reads. Reuses the on-page loader and context builder so both stages see the same page."""

from __future__ import annotations

import re

from seo_agents.keyword_research.contracts import PageBrief
from seo_agents.keyword_research.keyword_text import domain_of
from seo_agents.onpage.page_context.page_context_builder import PageContextBuilder, page_brief
from seo_agents.onpage.reports import exploration_reports as m
from seo_agents.onpage.reports.exploration_reports import ExplorationReportsLoader

TITLE_SPLIT = re.compile(r'\s+[|—–-]\s+|\s*\|\s*')


def selectable(page):
    """Real pages only: no route templates, no non-page rows, no 4xx/5xx responses (same rule as the on-page stage)."""
    status = (page.get('http') or {}).get('http_status')
    return page.get('instance_status') != 'template' and page.get('kind', 'page') == 'page' \
        and not (isinstance(status, int) and status >= 400)


def page_type_guess(url):
    """home | article | listing | product | category | contact | about | docs | other, from the URL path only."""
    path = (url or '/').split('?')[0].rstrip('/') or '/'
    parts = [p for p in path.split('/') if p]
    if path == '/':
        return 'home'
    first = parts[0].lower()
    if first in {'blog', 'posts', 'post', 'articles', 'article', 'news', 'guides', 'insights'}:
        return 'article' if len(parts) > 1 else 'listing'
    if first in {'product', 'products', 'p', 'item', 'shop'} and len(parts) > 1:
        return 'product'
    if first in {'category', 'categories', 'collections', 'c', 'shop'}:
        return 'category'
    if first in {'contact', 'contact-us'}:
        return 'contact'
    if first in {'about', 'about-us', 'team'}:
        return 'about'
    if first in {'docs', 'documentation', 'help', 'kb'}:
        return 'docs'
    return 'other'


def exploration_terms(ctx, limit=12):
    """Exploration keyword hints (top n-grams of the page, then site-wide candidates), as plain strings."""
    data = ctx.get('exploration_keywords') or {}
    out = []
    for item in list(data.get('top_terms') or []) + list(data.get('site_candidates') or []):
        if isinstance(item, dict):
            item = item.get('keyword') or item.get('term') or item.get('text')
        text = m.clean(item) if isinstance(item, str) else None
        if text and text.lower() not in {o.lower() for o in out}:
            out.append(text)
    return out[:limit]


def brief_from_context(ctx, *, page_type=None):
    view = page_brief(ctx)
    return PageBrief(page_id=ctx['page_id'], url=ctx['url'], absolute_url=ctx.get('absolute_url'),
                     page_type=page_type or page_type_guess(ctx['url']), title=view['title'], description=view['description'],
                     headings=[h['text'] for h in view['headings'] if h.get('text')], copy=view['copy'],
                     images=view['images'], lang=ctx.get('lang') or 'en', existing_queries=exploration_terms(ctx))


def seed_queries(brief, limit=6):
    """Seed queries for SERP / competitor discovery: title segments, the H1, exploration terms."""
    seeds = []
    for segment in TITLE_SPLIT.split(brief.title or ''):
        seeds.append(segment)
    seeds += brief.headings[:1] + list(brief.existing_queries)
    out = []
    for seed in seeds:
        text = re.sub(r'\s+', ' ', seed or '').strip().lower()
        if len(text) > 2 and text not in out:
            out.append(text)
    return out[:limit]


def site_packet(reports, contexts, briefs, origin):
    """Compact site description for generator.profile_business (exploration JSON only, no locators)."""
    any_ctx = next(iter(contexts.values()), {})
    entity = (reports.exploration or {}).get('entity') or {}
    return {
        'origin': origin, 'domain': domain_of(origin),
        'brand_candidates': any_ctx.get('brand_candidates') or [],
        'site_type_guess': any_ctx.get('site_type'),
        'entity': entity if isinstance(entity, dict) else {},
        'site_keyword_candidates': (reports.repo_context.get('summary') or {}).get('primary_keyword_candidates') or [],
        'pages': [{'page_id': b.page_id, 'url': b.url, 'page_type': b.page_type, 'title': b.title,
                   'description': b.description, 'headings': b.headings[:8], 'copy': b.copy[:6]} for b in briefs.values()],
    }


class PageSelector:
    """Tool: seo_reports/ -> (reports, origin, {page_id: PageBrief}, site packet) for the keyword stage."""

    def select(self, reports_dir, *, only=None, limit=25, site_url=None):
        loader = ExplorationReportsLoader()
        reports = loader.load(reports_dir)
        origin = loader.site_origin(reports, site_url)
        only = set(only or [])
        ids = [r['id'] for r in reports.page_rows() if r['id'] in reports.pages and selectable(reports.pages[r['id']])
               and (not only or r['id'] in only or r.get('url') in only)][:limit]
        builder = PageContextBuilder()
        contexts = {pid: builder.build(reports, pid, origin=origin, site_url=site_url) for pid in ids}
        briefs = {pid: brief_from_context(ctx) for pid, ctx in contexts.items()}
        origin = next((c.get('origin') for c in contexts.values() if c.get('origin')), origin)
        return reports, origin, briefs, site_packet(reports, contexts, briefs, origin)
