"""Plan new blog articles from what users search and what competitors cover (model, 'article-planner').

Topic pool (every item has an id the plan must cite, so each article traces back to real user demand):
    gap        keyword research `content_gaps` of a page (topics competitors cover, the site does not)
    question   researched question keywords (AEO) / the intent check's user questions
    prompt     conversational prompts (GEO)
    paa        People Also Ask questions from the live SERP
    subtopic   sub-topics at least two competitor pages cover (competitor research)
    seed       business seed topics (keyword research business profile)
"""

from __future__ import annotations

from seo_agents.onpage.checks.check_helpers import intent_of
from seo_agents.onpage.content.article_rules import check_slug
from seo_agents.onpage.validation import content_validators as v

FORMATS = {'guide', 'how-to', 'listicle', 'comparison', 'explainer', 'checklist', 'case-study', 'glossary'}
INTENTS = {'informational', 'commercial', 'transactional'}
_STR = {'type': 'string'}
_STRS = {'type': 'array', 'items': _STR}
PLAN_SCHEMA = {'type': 'object', 'required': ['articles'], 'properties': {'articles': {'type': 'array', 'items': {
    'type': 'object',
    'required': ['topic_ids', 'primary_keyword', 'questions', 'search_intent', 'format', 'angle', 'working_title', 'slug'],
    'properties': {'topic_ids': _STRS, 'primary_keyword': _STR, 'secondary_keywords': _STRS, 'questions': _STRS,
                   'search_intent': _STR, 'format': _STR, 'angle': _STR, 'audience': _STR, 'working_title': _STR,
                   'slug': _STR, 'pillar_url': {'type': ['string', 'null']}}}}}}
POOL_LIMIT = 80


def topic_pool(contexts, results, insights, business):
    pool, seen = [], set()

    def add(text, kind, page=None):
        text = ' '.join(str(text or '').split())
        key = v.normalize(text)
        if len(text) < 4 or key in seen or len(pool) >= POOL_LIMIT:
            return
        seen.add(key)
        pool.append({'id': f't{len(pool) + 1}', 'text': text, 'kind': kind, 'page': page})

    for pid, ctx in contexts.items():
        plan = ctx.get('keyword_plan') or {}
        intent = intent_of((results or {}).get(pid) or {})
        for gap in plan.get('content_gaps') or intent.get('content_gaps') or []:
            add(gap, 'gap', ctx['url'])
        for q in plan.get('questions') or intent.get('user_questions') or []:
            add(q, 'question', ctx['url'])
        for q in plan.get('conversational') or intent.get('conversational') or []:
            add(q, 'prompt', ctx['url'])
        page_insight = (insights or {}).get(pid) or {}
        for q in page_insight.get('people_also_ask') or []:
            add(q, 'paa', ctx['url'])
        for topic in page_insight.get('common_subtopics') or []:
            add(topic['heading'], 'subtopic', ctx['url'])
    for seed in (business or {}).get('seed_topics') or []:
        add(seed, 'seed')
    return pool


def existing_pages(contexts, results):
    rows = []
    for pid, ctx in contexts.items():
        intent = intent_of((results or {}).get(pid) or {})
        rows.append({'url': ctx['url'], 'title': ctx.get('title'),
                     'primary': (ctx.get('keyword_plan') or {}).get('primary') or intent.get('primary_query')})
    return rows


def check_plan(answer, *, pool, pages, count, prefix, site_paths, links):
    v.require(answer, 'articles')
    items = answer['articles']
    if not isinstance(items, list) or not 1 <= len(items) <= count:
        raise ValueError(f'articles must list 1-{count} article briefs')
    by_id = {t['id']: t for t in pool}
    questions = {t['text']: t for t in pool if t['kind'] in ('question', 'paa', 'prompt')}
    taken, primaries, out = [], [], []
    for i, a in enumerate(items):
        name = f'articles[{i}]'
        if not isinstance(a, dict):
            raise ValueError(name + ' must be an object')
        ids = a.get('topic_ids')
        if not isinstance(ids, list) or not ids or any(t not in by_id for t in ids):
            raise ValueError(f'{name}.topic_ids must list pool ids (t1, t2, ...) the article serves')
        primary = ' '.join(str(a.get('primary_keyword') or '').lower().split())
        if not 2 <= len(primary.split()) <= 8:
            raise ValueError(f'{name}.primary_keyword must be a 2-8 word search phrase')
        topic_tokens = set().union(*(v.content_tokens(by_id[t]['text']) for t in ids))
        own = v.content_tokens(primary)
        if not own or len(own & topic_tokens) / len(own) < 0.5:
            raise ValueError(f'{name}.primary_keyword "{primary}" must come from its topics ({[by_id[t]["text"] for t in ids][:3]}): '
                             'at least half of its words must appear in them')
        for page in pages:
            if page.get('primary') and v.similar(primary, page['primary'], 0.7):
                raise ValueError(f'{name}.primary_keyword "{primary}" is already the target of {page["url"]}; pick a topic '
                                 'the site does not cover yet (no cannibalization)')
        if any(v.similar(primary, p, 0.6) for p in primaries):
            raise ValueError(f'{name}.primary_keyword "{primary}" overlaps another planned article')
        primaries.append(primary)
        qs = a.get('questions') or []
        if not isinstance(qs, list) or len(qs) > 6 or any(q not in questions for q in qs):
            raise ValueError(f'{name}.questions must copy up to 6 question/paa/prompt pool texts verbatim')
        if a.get('search_intent') not in INTENTS:
            raise ValueError(f'{name}.search_intent must be one of {sorted(INTENTS)}')
        if a.get('format') not in FORMATS:
            raise ValueError(f'{name}.format must be one of {sorted(FORMATS)}')
        path = check_slug(a.get('slug'), primary=primary, prefix=prefix, existing_paths=site_paths, taken=taken)
        taken.append(path)
        pillar = a.get('pillar_url')
        if pillar is not None and pillar not in links:
            raise ValueError(f'{name}.pillar_url must be one of links[].url or null')
        v.text_length(a.get('angle'), 10, 240, f'{name}.angle')
        v.text_length(a.get('working_title'), 15, 90, f'{name}.working_title')
        secondary = [' '.join(str(s).lower().split()) for s in a.get('secondary_keywords') or [] if str(s).strip()][:6]
        out.append({'primary_keyword': primary, 'secondary_keywords': secondary, 'questions': qs,
                    'topics': [by_id[t] for t in ids], 'search_intent': a['search_intent'], 'format': a['format'],
                    'angle': a['angle'].strip(), 'audience': str(a.get('audience') or '').strip()[:160],
                    'working_title': a['working_title'].strip(), 'slug': a['slug'], 'url': path, 'pillar_url': pillar})
    return {'articles': out}


class ArticlePlanner:
    """Tool (model): topic pool + site + business -> validated article briefs (skill lib/prompts/onpage/article-planner)."""

    skill = 'article-planner'

    def plan(self, writer, *, pool, pages, business, count, prefix, site_paths, links, run_date, lang):
        packet = {'business': business, 'existing_pages': pages, 'pool': pool, 'count': count, 'url_prefix': prefix,
                  'links': [{'url': u, 'title': t} for u, t in links.items()], 'run_date': run_date, 'lang': lang}
        answer, meta = writer.write(self.skill, packet, lambda a: check_plan(
            a, pool=pool, pages=pages, count=count, prefix=prefix, site_paths=site_paths, links=links), PLAN_SCHEMA)
        return answer['articles'], meta
