"""Content studio: new blog articles for the client site, researched against the competitors that rank.

    1. topic pool      user demand from keyword research + SERP + competitor sub-topics (article_planner.topic_pool)
    2. plan            ArticlePlanner (model): N briefs, each tied to pool topics, with an evergreen URL and a pillar page
    3. per article     CompetitorResearch.for_keyword(primary): live SERP top results -> fetch -> extract -> patterns,
                       title inspiration, dated fact passages
    4. write           ArticleWriter (model) + ArticleValidator (deterministic rules, repair loop)
    5. fact check      ArticleFactChecker: claims, support, freshness -> pass | needs_review
    6. render          Markdown (frontmatter, tables, Mermaid, images with alt, sources) + JSON-LD

Env: SEO_ONPAGE_ARTICLES = number of articles to plan (default 3; 0 turns the studio off).
"""

from __future__ import annotations

from collections import Counter
import os
import traceback

from seo_agents.onpage.content.article_fact_check import fact_check
from seo_agents.onpage.content.article_planner import ArticlePlanner, existing_pages, topic_pool
from seo_agents.onpage.content.article_renderer import json_ld, to_markdown
from seo_agents.onpage.content.article_writer import ArticleWriter
from seo_agents.onpage.page_context.page_context_builder import facts
from seo_agents.onpage.writer.content_writer import WriterFailed
from seo_agents.utils.step_logger import log

BLOG_FOLDERS = ('blog', 'posts', 'articles', 'guides', 'resources', 'insights', 'learn', 'news')


def url_prefix(paths):
    """The site's existing article folder (e.g. /blog), else /blog."""
    firsts = Counter(p.strip('/').split('/')[0] for p in paths if isinstance(p, str) and p.startswith('/') and p.strip('/'))
    for folder, _ in firsts.most_common():
        if folder in BLOG_FOLDERS:
            return '/' + folder
    return '/blog'


def market_of(meta):
    raw = ((meta or {}).get('keyword_map') or {}).get('market')
    if not isinstance(raw, dict):
        return None
    try:
        from dataclasses import fields
        from seo_agents.keyword_research.contracts import Market
        names = {f.name for f in fields(Market)}
        return Market(**{k: v for k, v in raw.items() if k in names})
    except Exception:  # noqa: BLE001
        return None


class ArticleStudio:
    """Tool: plan, research, write, fact-check and render new articles (see module docstring)."""

    def __init__(self, research, *, count=None, planner=None, article_writer=None):
        self.research = research
        self.count = int(count if count is not None else os.getenv('SEO_ONPAGE_ARTICLES', '3'))
        self.planner = planner or ArticlePlanner()
        self.article_writer = article_writer or ArticleWriter()

    def run(self, writer, *, contexts, results, insights, meta, run_date, origin):
        if self.count <= 0:
            return {'status': 'skipped', 'reason': 'SEO_ONPAGE_ARTICLES=0', 'articles': []}
        if not contexts:
            return {'status': 'skipped', 'reason': 'no pages selected', 'articles': []}
        business = dict((meta or {}).get('business') or {})
        first = next(iter(contexts.values()))
        brand = business.get('brand') or (first.get('brand_candidates') or [None])[0]
        business.setdefault('brand', brand)
        pool = topic_pool(contexts, results, insights, business)
        if not pool:
            return {'status': 'skipped', 'reason': 'no user questions, content gaps or competitor topics to write about',
                    'articles': []}
        links = {}
        for ctx in contexts.values():
            for u in ctx.get('site_urls') or []:
                links.setdefault(u['url'], u.get('title'))
        prefix = url_prefix(links)
        pages = existing_pages(contexts, results)
        lang = first.get('lang') or 'en'
        market = market_of(meta)
        try:
            briefs, plan_meta = self.planner.plan(writer, pool=pool, pages=pages, business=business, count=self.count,
                                                  prefix=prefix, site_paths=list(links), links=links, run_date=run_date, lang=lang)
        except WriterFailed as error:
            return {'status': 'failed', 'stage': 'plan', 'errors': error.errors, 'pool': pool, 'articles': []}
        except Exception as error:  # noqa: BLE001
            return {'status': 'failed', 'stage': 'plan', 'errors': [f'{type(error).__name__}: {str(error)[:300]}'], 'pool': pool,
                    'articles': []}
        corpus = ' \n '.join(ctx.get('corpus') or '' for ctx in contexts.values())
        assets = {}
        for ctx in contexts.values():
            for a in ctx.get('assets') or []:
                if a.get('type', 'image') == 'image' and a.get('src'):
                    assets.setdefault(a['src'], a)
        by_url = {ctx['url']: ctx for ctx in contexts.values()}
        home = next((c for c in contexts.values() if c.get('is_home')), first)
        titles = [c.get('title') for c in contexts.values() if c.get('title')]
        articles = []
        for brief in briefs:
            articles.append(self.one(writer, brief, by_url=by_url, home=home, links=links, assets=assets, corpus=corpus,
                                     business=business, brand=brand, run_date=run_date, origin=origin, market=market,
                                     titles=titles, lang=lang))
            done = articles[-1].get('article') or {}
            titles += [t for t in (done.get('title'), done.get('h1')) if t]
        ok = sum(1 for a in articles if a['status'] == 'done')
        log(f'content studio: {ok}/{len(articles)} articles written')
        return {'status': 'done' if ok else 'failed', 'plan': {'meta': plan_meta, 'url_prefix': prefix, 'briefs': briefs},
                'pool': pool, 'articles': articles}

    def one(self, writer, brief, *, by_url, home, links, assets, corpus, business, brand, run_date, origin, market, titles, lang):
        pillar = by_url.get(brief.get('pillar_url')) or {}
        topic_pages = [by_url.get(t.get('page')) or {} for t in brief.get('topics') or []]
        fallback = []
        for ctx in [pillar] + topic_pages:
            fallback += list((ctx.get('keyword_plan') or {}).get('competitor_urls') or [])
        try:
            research = self.research.for_keyword(brief['primary_keyword'], market=market, fallback_urls=fallback,
                                                 own_domain=origin, run_date=run_date)
        except Exception as error:  # noqa: BLE001
            research = {'keyword': brief['primary_keyword'], 'pages': [], 'patterns': {}, 'titles': [], 'sources': [],
                        'texts': [], 'errors': [f'{type(error).__name__}: {str(error)[:200]}']}
        client_facts = [f['text'] for f in facts(home)][:25]
        if pillar and pillar is not home:
            client_facts += [f['text'] for f in facts(pillar)][:20]
        public = {k: v for k, v in research.items() if k != 'texts'}
        try:
            article, meta = self.article_writer.write(
                writer, brief, research=research, links=links, assets=assets, corpus=corpus, client_facts=client_facts,
                business={k: business.get(k) for k in ('brand', 'site_type', 'offering', 'audience', 'value_props')},
                run_date=run_date, other_titles=titles, brand=brand, lang=lang)
        except WriterFailed as error:
            return {'status': 'failed', 'brief': brief, 'errors': error.errors, 'research': public}
        except Exception as error:  # noqa: BLE001
            return {'status': 'failed', 'brief': brief, 'research': public,
                    'errors': [f'{type(error).__name__}: {str(error)[:300]}', traceback.format_exc(limit=3)[-500:]]}
        article['primary_keyword'] = brief['primary_keyword']
        article['last_updated'] = run_date
        sources = research.get('sources') or []
        return {'status': 'done', 'brief': brief, 'article': article, 'meta': meta,
                'fact_check': fact_check(article, sources, run_date),
                'markdown': to_markdown(article, sources, run_date=run_date, brand=brand),
                'json_ld': json_ld(article, origin=origin, run_date=run_date, brand=brand),
                'research': public}
