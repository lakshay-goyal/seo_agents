"""On-page checklist: 3-4 model calls per page plus deterministic checks, then a scope gate + consolidation pass.

Model calls (audit §10 "Collapse 10 LLM calls -> 3-4"):
  intent      (small) intent, queries, entities, site/page type
  page_plan   (mid)   title, meta, H1, outline, direct answer, section rewrites, optional FAQ, fact->slot map
  media_links (small) image alts + anchor rewrites + new internal links
  advice      (small, optional SEO_ONPAGE_ADVICE=0 to skip) E-E-A-T / AEO gaps as advice only
Deterministic: keyword plan, image technical issues, accessible names, technical (canonical/robots/lang/viewport/
redirect/prerender/soft-404), JSON-LD @id graph incl. FAQ mirroring, social tags, fact-check, QA (gate + dedup + split).

Every function reads only the page context (exploration JSON) and earlier results and returns
{status, data, changes, advice, client_inputs, site_changes, errors, meta}. Nothing here touches client source.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

from seo_agents.onpage import ops
from seo_agents.onpage import render
from seo_agents.onpage import validators as v
from seo_agents.onpage.context import LOGOISH, facts, page_brief
from seo_agents.onpage.model import clean, value
from seo_agents.onpage.policy import LIMITS, PAGE_TYPES, SITE_TYPES
from seo_agents.onpage.recipes import REDIRECT_WHERE, SOFT_404_WHERE

INTENTS = {'informational', 'commercial', 'transactional', 'navigational', 'local'}
STAGES = {'awareness', 'consideration', 'decision', 'retention'}
ROLES = {'pillar', 'cluster', 'standalone'}
ADVICE_TOPICS = {'eeat.authorship', 'eeat.experience', 'eeat.expertise', 'eeat.authority', 'eeat.trust', 'eeat.citations',
                 'aeo.definition', 'aeo.summary', 'aeo.entity_consistency', 'aeo.questions', 'content.depth', 'content.freshness'}
WEAK_ALT = re.compile(r'(?i)^(?:image|img|photo|picture|screenshot|banner|icon|logo|untitled|[\w-]+\.(?:png|jpe?g|webp|gif|svg|avif))$')


def result(status='done', data=None, changes=None, errors=None, meta=None, advice=None, client_inputs=None, site_changes=None):
    return {'status': status, 'data': data or {}, 'changes': changes or [], 'advice': advice or [],
            'client_inputs': client_inputs or [], 'site_changes': site_changes or [], 'errors': errors or [], 'meta': meta or {}}


def intent_of(prior):
    return (prior.get('intent') or {}).get('data') or {}


def plan_of(prior):
    return (prior.get('page_plan') or {}).get('data') or {}


def brand(ctx, prior):
    return intent_of(prior).get('brand_name') or (ctx['brand_candidates'] or [None])[0]


def site_type(ctx, prior):
    found = intent_of(prior).get('site_type')
    return found if found in SITE_TYPES and ctx['site_type'] in ('other', None) else (ctx['site_type'] or found or 'other')


def page_type(ctx, prior):
    found = intent_of(prior).get('page_type')
    if found in PAGE_TYPES:
        return found
    url = ctx['url'].lower()
    if ctx['is_home']:
        return 'home'
    if re.search(r'/(?:blog|posts?|articles?|news)/[^/]+', url):
        return 'article'
    if re.search(r'/about', url):
        return 'about'
    if re.search(r'/contact', url):
        return 'contact'
    return 'collection' if re.search(r'/(?:blog|posts|projects|work|bookshelf|shop|products)/?$', url) else 'other'


def site_url_set(ctx):
    resources = {l['href'] for l in ctx['links'] if isinstance(l.get('href'), str) and l['href'].startswith('/')
                 and (l.get('status') is None or l['status'] < 400)}
    return {u['url'] for u in ctx['site_urls']} | resources


def numbers_allowed(ctx):
    return {ctx['run_date'][:4]}


def first_main_edit(ctx, *, heading=False):
    """Edit of the first page-specific (non-chrome, non-data-template) heading or block."""
    if heading:
        for h in ctx['heading_records']:
            if h['edit'] and h['in_main'] and not h['data_driven']:
                return h['edit']
    for s in ctx['sections']:
        for b in s['blocks']:
            if b.get('edit') and (b['edit'].get('kind') not in ('json', 'yaml', 'markdown', 'i18n')):
                return b['edit']
    return None


def add_target(ctx, field):
    """Head insertion point from exploration (`add_target`) - a target, never a bare `line: 1` placeholder."""
    target = ops.target_of(ctx.get('add_target'), kind='add', placement='before </head>')
    if target is not None and target.get('scope') == 'unknown' and target.get('file'):
        target['scope'] = 'shell' if ops.recipes.is_shell_file(target['file']) else 'page'
    return target


# ====================================================================== 1. search intent (small)
INTENT_SCHEMA = {'type': 'object', 'required': ['primary_topic', 'intent', 'journey_stage', 'primary_query', 'secondary_queries',
                                                 'entities', 'user_questions', 'cluster', 'brand_name', 'site_type', 'page_type']}


def check_intent(ctx, prior, writer):
    urls = site_url_set(ctx)
    packet = {'page': page_brief(ctx), 'framework': ctx['fingerprint'], 'site_type_hint': ctx['site_type'],
              'site': {'brand_candidates': ctx['brand_candidates'], 'urls': [{'url': u['url'], 'title': u['title']} for u in ctx['site_urls']]},
              'exploration_keywords': ctx['exploration_keywords']}

    def validate(a):
        v.require(a, 'primary_topic', 'intent', 'journey_stage', 'primary_query', 'secondary_queries', 'entities',
                  'user_questions', 'cluster', 'brand_name')
        v.one_of(a['intent'], INTENTS, 'intent')
        v.one_of(a['journey_stage'], STAGES, 'journey_stage')
        v.text_length(a['primary_query'], 3, 70, 'primary_query')
        a['primary_query'] = a['primary_query'].lower().strip()
        a['secondary_queries'] = v.string_list(a['secondary_queries'], 0, 6, 'secondary_queries')
        a['user_questions'] = v.string_list(a['user_questions'], 0, 6, 'user_questions')
        a['entities'] = v.string_list(a['entities'], 1, 20, 'entities')
        v.text_length(a['brand_name'], 2, 80, 'brand_name')
        a['site_type'] = a.get('site_type') if a.get('site_type') in SITE_TYPES else ctx['site_type']
        a['page_type'] = a.get('page_type') if a.get('page_type') in PAGE_TYPES else None
        cluster = a['cluster'] if isinstance(a['cluster'], dict) else {}
        v.one_of(cluster.get('role'), ROLES, 'cluster.role')
        cluster['pillar_url'] = cluster.get('pillar_url') if cluster.get('pillar_url') in urls else None
        cluster['related_urls'] = [u for u in cluster.get('related_urls') or [] if u in urls and u != ctx['url']]
        a['cluster'] = cluster
        # Queries are model hypotheses, not measured search demand (audit §8 topic-intent).
        a['query_source'] = 'model-hypothesis'
        return a

    answer, meta = writer.write('search-intent', packet, validate, INTENT_SCHEMA)
    return result(data=answer, meta=meta)


# ====================================================================== 2. keyword plan (deterministic)
def contains_query(text, query):
    if not text or not query:
        return False
    if v.phrase_count(text, query):
        return True
    tokens = [w for w in v.words(query) if w not in v.STOPWORDS]
    have = set(v.words(text))
    return bool(tokens) and all(t in have for t in tokens)


def check_keyword_plan(ctx, prior, writer=None):
    intent = intent_of(prior)
    primary = intent.get('primary_query')
    copy = ' '.join(ctx['copy'])
    first_100 = ' '.join(v.words(copy)[:100])
    h1 = [h['text'] for h in ctx['headings'] if h['level'] == 1]
    h2 = [h['text'] for h in ctx['headings'] if h['level'] == 2]
    placements = {
        'title': contains_query(ctx['title'], primary), 'h1': any(contains_query(t, primary) for t in h1),
        'first_100_words': contains_query(first_100, primary), 'h2': any(contains_query(t, primary) for t in h2),
        'meta_description': contains_query(ctx['description'], primary),
        'image_alt': any(contains_query(i.get('alt'), primary) for i in ctx['images']),
    }
    dens = v.density(copy)
    queries = [primary] + list(intent.get('secondary_queries') or [])
    data = {'primary': primary, 'secondary': intent.get('secondary_queries') or [], 'entities': intent.get('entities') or [],
            'source': intent.get('query_source'), 'placements_current': placements,
            'placements_required': ['title', 'h1', 'first_100_words', 'meta_description'],
            'query_counts_current': {q: v.phrase_count(copy, q) for q in queries if q},
            'top_density_current': dict(sorted(dens.items(), key=lambda kv: -kv[1])[:10]),
            'stuffing_flags': [f'{t} {p}%' for t, p in dens.items() if p > LIMITS['MAX_TERM_DENSITY_PCT'][1] and len(v.words(copy)) >= 150],
            'limits': {k: LIMITS[k] for k in ('PRIMARY_PHRASE_PER_100_WORDS', 'MAX_TERM_DENSITY_PCT')}}
    return result(data=data)


# ====================================================================== 3. page plan (mid, one call)
PLAN_SCHEMA = {'type': 'object', 'required': ['title', 'description', 'h1', 'outline', 'direct_answer', 'sections', 'faq', 'fact_slots']}


def shingles(text, n=8):
    tokens = v.words(text)
    return {' '.join(tokens[i:i + n]) for i in range(max(0, len(tokens) - n + 1))}


def faq_allowed(ctx, prior):
    intent = intent_of(prior).get('intent')
    if intent == 'navigational' or site_type(ctx, prior) == 'personal':
        return LIMITS['FAQ_ITEMS_NAVIGATIONAL'][1]
    return LIMITS['FAQ_ITEMS'][1]


def check_page_plan(ctx, prior, writer):
    intent = intent_of(prior)
    primary = intent.get('primary_query')
    name = brand(ctx, prior)
    others = [t for t in ctx['other_titles'] if t]
    headings = [{'id': f'h{i + 1}', 'level': h['level'], 'text': h['text'], 'editable': not h['data_driven'] and bool(h['edit'])}
                for i, h in enumerate(ctx['heading_records']) if h['text']]
    by_hid = {h['id']: (h, ctx['heading_records'][int(h['id'][1:]) - 1]) for h in headings}
    sections = [{'id': f's{i + 1}', 'heading': s['heading'], 'data_driven': s['data_driven'],
                 'blocks': [{'tag': b['tag'], 'text': b['text']} for b in s['blocks']][:12]}
                for i, s in enumerate(ctx['sections'])]
    by_sid = {s['id']: ctx['sections'][int(s['id'][1:]) - 1] for s in sections}
    fact_rows = facts(ctx)
    fact_ids = {f['id'] for f in fact_rows}
    max_faq = faq_allowed(ctx, prior)
    urls = site_url_set(ctx)
    assets = {a['src'] for a in ctx['assets']}
    current_h2 = [h['text'] for h in ctx['headings'] if h['level'] == 2]
    protected = [name or ''] + list(intent.get('entities') or []) + [h['text'] for h in ctx['headings']]
    packet = {
        'page': {'url': ctx['url'], 'lang': ctx['lang'], 'is_home': ctx['is_home'], 'title': ctx['title'], 'description': ctx['description'],
                 'site_type': site_type(ctx, prior), 'page_type': page_type(ctx, prior)},
        'intent': {k: intent.get(k) for k in ('primary_query', 'secondary_queries', 'user_questions', 'entities', 'intent', 'journey_stage')},
        'brand': name, 'other_titles': others, 'headings': headings, 'sections': sections, 'facts': fact_rows,
        'assets': [{'src': a['src'], 'alt': a['alt']} for a in ctx['assets']][:30],
        'links': [{'url': u['url'], 'title': u['title']} for u in ctx['site_urls'] if u['url'] != ctx['url']],
        'max_faq': max_faq, 'stack': {'framework': ctx['framework'], 'head_scope': ctx['head_scope']},
    }

    def validate(a):
        v.require(a, 'title', 'description', 'h1', 'outline', 'direct_answer', 'sections', 'faq', 'fact_slots')
        a['title'] = v.text_length(a['title'], *LIMITS['TITLE_CHARS'], 'title')
        if a['title'].lower() in {t.lower() for t in others}:
            raise ValueError('title duplicates another page title')
        a['description'] = v.text_length(a['description'], *LIMITS['DESCRIPTION_CHARS'], 'description')
        if '"' in a['description']:
            raise ValueError('remove double quotes from the description')
        a['h1'] = v.text_length(a['h1'], *LIMITS['H1_CHARS'], 'h1')
        for key in ('title', 'description', 'h1'):
            v.no_stuffing(a[key], primary, name=key, short=True)
            v.grounded(a[key], ctx['corpus'], key)
            v.keeps_client_terms(a[key], ctx['corpus'], key)
        outline = a['outline'] if isinstance(a['outline'], list) else None
        if outline is None:
            raise ValueError('outline must be a list (empty keeps the current headings)')
        levels, texts = [], []
        for i, item in enumerate(outline):
            if not isinstance(item, dict) or item.get('level') not in (2, 3):
                raise ValueError(f'outline[{i}].level must be 2 or 3')
            item['text'] = v.text_length(item.get('text'), *LIMITS['HEADING_CHARS'], f'outline[{i}].text')
            v.grounded(item['text'], ctx['corpus'], f'outline[{i}]')
            v.keeps_client_terms(item['text'], ctx['corpus'], f'outline[{i}]')
            rid = item.get('replaces_id')
            if rid is not None:
                if rid not in by_hid:
                    raise ValueError(f'outline[{i}].replaces_id "{rid}" is not a heading id')
                if not by_hid[rid][0]['editable']:
                    raise ValueError(f'outline[{i}] rewrites heading {rid} "{by_hid[rid][0]["text"]}", which is generated from data/a '
                                     'template shared by other items; leave it (set replaces_id null for new headings only)')
            levels.append(item['level'])
            texts.append(v.normalize(item['text']))
        if len(set(texts)) != len(texts):
            raise ValueError('outline has duplicate headings')
        h2 = [o for o in outline if o['level'] == 2]
        if outline and not LIMITS['H2_COUNT'][0] <= len(h2) <= LIMITS['H2_COUNT'][1]:
            raise ValueError(f'outline has {len(h2)} H2s; need {LIMITS["H2_COUNT"][0]}-{LIMITS["H2_COUNT"][1]}')
        if outline:
            v.heading_hierarchy([1] + levels)
        if sum(1 for o in h2 if v.phrase_count(o['text'], primary)) > 1:
            raise ValueError('primary query appears in more than one H2; use variants')
        v.word_count(a['direct_answer'], *LIMITS['DIRECT_ANSWER_WORDS'], 'direct_answer')
        v.grounded(a['direct_answer'], ctx['corpus'], 'direct_answer', numbers_allowed(ctx))
        v.check_links(a['direct_answer'], urls, 'direct_answer')
        v.keeps_client_terms(a['direct_answer'], ctx['corpus'], 'direct_answer')
        units = [('direct_answer', a['direct_answer'])]
        if not isinstance(a['sections'], list):
            raise ValueError('sections must be a list (empty = no section rewrites)')
        for i, s in enumerate(a['sections']):
            if not isinstance(s, dict) or not isinstance(s.get('blocks'), list) or not s['blocks']:
                raise ValueError(f'sections[{i}] needs heading and non-empty blocks')
            rid = s.get('replaces_id')
            if rid is not None and rid not in by_sid:
                raise ValueError(f'sections[{i}].replaces_id "{rid}" is not a section id')
            if rid is not None and by_sid[rid]['data_driven']:
                raise ValueError(f'sections[{i}] rewrites section {rid}, which renders a data list (.map over JSON); '
                                 'do not replace it with static prose - add an intro paragraph as a new section or leave it')
            s['heading'] = v.text_length(s.get('heading'), *LIMITS['HEADING_CHARS'], f'sections[{i}].heading')
            for j, b in enumerate(s['blocks']):
                v.check_block(b, assets=assets, urls=urls, corpus=ctx['corpus'], name=f'sections[{i}].blocks[{j}]',
                              allowed_numbers=numbers_allowed(ctx))
            text = ' '.join(t for b in s['blocks'] for t in v.block_texts(b))
            v.keeps_client_terms(text, ctx['corpus'], f'sections[{i}]')
            units.append((f'sections[{i}]', text))
        faq = a['faq'] if isinstance(a['faq'], list) else None
        if faq is None or len(faq) > max_faq:
            raise ValueError(f'faq must be a list of at most {max_faq} items (empty is fine)')
        headings_now = current_h2 + [o['text'] for o in h2] + [s['heading'] for s in a['sections']]
        for i, q in enumerate(faq):
            if not isinstance(q, dict):
                raise ValueError(f'faq[{i}] must be an object')
            q['question'] = v.text_length(q.get('question'), 10, 120, f'faq[{i}].question')
            if not q['question'].endswith('?'):
                raise ValueError(f'faq[{i}].question must end with "?"')
            clash = next((h for h in headings_now if v.similar(q['question'], h)), None)
            if clash:
                raise ValueError(f'faq[{i}] repeats the heading "{clash}"; FAQ is only for questions the page headings do not cover')
            v.word_count(q.get('answer') or '', *LIMITS['FAQ_ANSWER_WORDS'], f'faq[{i}].answer')
            if v.SELF_REFERENCE.search(q['answer']):
                raise ValueError(f'faq[{i}].answer must be self-contained')
            v.grounded(q['answer'], ctx['corpus'], f'faq[{i}].answer', numbers_allowed(ctx))
            v.check_links(q['answer'], urls, f'faq[{i}].answer')
            units.append((f'faq[{i}]', q['answer']))
        slots = a['fact_slots'] if isinstance(a['fact_slots'], dict) else None
        if slots is None:
            raise ValueError('fact_slots must be an object {fact_id: slot}')
        unknown = [k for k in slots if k not in fact_ids]
        if unknown:
            raise ValueError('fact_slots uses unknown fact ids: ' + ', '.join(unknown[:5]))
        seen = {}
        for label, text in units:
            for sh in shingles(text):
                if sh in seen and seen[sh] != label:
                    raise ValueError(f'{label} repeats "{sh}" from {seen[sh]}; state each fact once (use fact_slots)')
                seen.setdefault(sh, label)
        v.entity_cap(units, name, 'page plan')
        body = ' '.join(t for _, t in units)
        v.no_stuffing(body, primary, name='body', protected=protected)
        return a

    answer, meta = writer.write('page-plan', packet, validate, PLAN_SCHEMA)
    return result(data=answer, changes=plan_changes(ctx, prior, answer, by_hid, by_sid), meta=meta)


def plan_changes(ctx, prior, a, by_hid, by_sid):
    head = ctx['head']
    changes = []
    title_target = ops.target_of(head.get('title')) or add_target(ctx, 'head.title')
    changes.append(ops.make_change(ctx, 'page_plan', 'head.title', ctx['title'], a['title'], target=title_target,
                                   op='set_text' if ctx['title'] else 'insert_node', confidence=0.85,
                                   html=f'<title>{render.inline(a["title"])}</title>', rationale='Unique, intent-matching title'))
    desc_target = ops.target_of(head.get('description'), attr='content') or add_target(ctx, 'head.description')
    changes.append(ops.make_change(ctx, 'page_plan', 'head.description', ctx['description'], a['description'], target=desc_target,
                                   op='set_attr' if ctx['description'] else 'insert_node', confidence=0.85,
                                   html=f'<meta name="description" content="{render.inline(a["description"])}">',
                                   rationale='Answers the query in one line and gives a reason to click'))
    current_h1 = next((h for h in ctx['heading_records'] if h['level'] == 1 and h['text']), None)
    if current_h1:
        h1_target, h1_op = ops.target_of(current_h1['edit'], confidence=current_h1['confidence']), 'set_text'
    else:
        anchor = first_main_edit(ctx, heading=True) or first_main_edit(ctx)
        h1_target, h1_op = ops.target_of(anchor, placement='before'), 'insert_node'
    if not current_h1 or v.normalize(current_h1['text']) != v.normalize(a['h1']):
        changes.append(ops.make_change(ctx, 'page_plan', 'headings.h1', current_h1['text'] if current_h1 else None, a['h1'],
                                       target=h1_target, op=h1_op, confidence=0.8, html=f'<h1>{render.inline(a["h1"])}</h1>',
                                       rationale='One H1 per route, inside the route\'s own content (never in the shared shell/header)'))
    for item in a['outline']:
        rid = item.get('replaces_id')
        if not rid:
            continue
        heading, record = by_hid[rid]
        if v.normalize(heading['text']) == v.normalize(item['text']):
            continue
        changes.append(ops.make_change(ctx, 'page_plan', f'headings.h{item["level"]}', heading['text'], item['text'],
                                       target=ops.target_of(record['edit'], confidence=record['confidence']), op='set_text',
                                       confidence=0.75, html=f'<h{item["level"]}>{render.inline(item["text"])}</h{item["level"]}>',
                                       rationale=item.get('purpose') or 'Heading that maps the section to the search intent'))
    anchor_after_h1 = (h1_target or {}).copy() if h1_target else None
    if anchor_after_h1:
        anchor_after_h1['placement'] = 'after the H1'
        anchor_after_h1['key'] = (anchor_after_h1.get('key') or '') + '|after-h1'
    changes.append(ops.make_change(ctx, 'page_plan', 'content.direct_answer', ctx['copy'][0] if ctx['copy'] else None, a['direct_answer'],
                                   target=anchor_after_h1, op='insert_node', confidence=0.75,
                                   html=f'<p>{render.inline(a["direct_answer"])}</p>',
                                   rationale='Answer-first opening that fully answers the primary query'))
    last_block = next((b['edit'] for s in reversed(ctx['sections']) for b in reversed(s['blocks']) if b.get('edit')
                       and not s['data_driven']), None)
    for index, s in enumerate(a['sections']):
        rid = s.get('replaces_id')
        current = by_sid.get(rid) if rid else None
        if current:
            edits = [b['edit'] for b in current['blocks'] if b.get('edit')]
            target = ops.target_of(current.get('edit') or (edits[0] if edits else None))
            change = ops.make_change(ctx, 'page_plan', f'content.section[{index}]',
                                     {'heading': current['heading'], 'blocks': [{'tag': b['tag'], 'text': b['text']} for b in current['blocks']]},
                                     {'heading': s['heading'], 'blocks': s['blocks']}, target=target, op='set_text', confidence=0.7,
                                     html=render.section(s['heading'], s.get('level', 2), s['blocks']),
                                     rationale=s.get('purpose') or 'Structured, answer-first section',
                                     extra={'replaces_blocks': edits})
        else:
            target = ops.target_of(last_block, placement=f'after (new section {index + 1})') if last_block else None
            change = ops.make_change(ctx, 'page_plan', f'content.section[{index}]', None, {'heading': s['heading'], 'blocks': s['blocks']},
                                     target=target, op='insert_node', confidence=0.65,
                                     html=render.section(s['heading'], s.get('level', 2), s['blocks']),
                                     rationale=s.get('purpose') or 'Covers a question the page does not answer yet')
        changes.append(change)
    if a['faq']:
        target = ops.target_of(last_block, placement='end of main content (FAQ)') if last_block else None
        changes.append(ops.make_change(ctx, 'page_plan', 'content.faq', None, a['faq'], target=target, op='insert_node', confidence=0.65,
                                       html=render.faq(a['faq']), rationale='Visible FAQ for follow-up questions the headings do not cover '
                                                                             '(mirrored verbatim in FAQPage JSON-LD)'))
    return changes


# ====================================================================== 4. image alts + anchors + new links (small)
LINKS_SCHEMA = {'type': 'object', 'required': ['alts', 'anchors', 'new_links']}


def alt_status(image):
    alt = image.get('alt')
    if image.get('alt_missing') or alt is None:
        return 'missing'
    if not clean(alt):
        return 'empty'
    if WEAK_ALT.match(clean(alt)) or len(clean(alt)) < 5:
        return 'generic'
    edit = image.get('alt_edit') or {}
    if edit.get('composed_from') or edit.get('kind') == 'computed':
        return 'composed'
    return 'literal'


def check_media_links(ctx, prior, writer):
    intent = intent_of(prior)
    images, seen = [], set()
    for image in ctx['images']:
        if not image.get('src') or image.get('visible') is False or image['src'] in seen:
            continue
        seen.add(image['src'])
        status = alt_status(image)
        if status in ('missing', 'generic', 'composed') or (status == 'empty' and image.get('above_fold')):
            images.append((f'img{len(images) + 1}', image, status))
    links = [l for l in ctx['links'] if l['renders'] and l.get('href') and l.get('kind') in ('internal', None, 'relative')
             and (l['anchor'].lower() in v.GENERIC_ANCHORS or (l.get('in_main') and len(l['anchor'].split()) <= 1
                                                               and l.get('section') not in ('nav', 'header', 'footer')))]
    already = {l['href'] for l in ctx['links']}
    candidates = [u for u in ctx['site_urls'] if u['url'] != ctx['url'] and u['url'] not in already]
    if not images and not links and not candidates:
        return result(status='skipped', data={'reason': 'no weak alts, no generic anchors and no unlinked site URLs'})
    plan = plan_of(prior)
    section_ids = {f's{i + 1}': s for i, s in enumerate(ctx['sections']) if not s['data_driven']}
    packet = {'images': [{'id': iid, 'src': i['src'], 'alt': i.get('alt'), 'alt_status': st, 'data_driven': st == 'composed' or
                          (i.get('alt_edit') or {}).get('kind') == 'json'} for iid, i, st in images],
              'links': [{'id': l['id'], 'href': l['href'], 'anchor': l['anchor'], 'section': l.get('section')} for l in links],
              'site_urls': [{'url': u['url'], 'title': u['title']} for u in candidates][:20],
              'sections': [{'id': sid, 'heading': s['heading']} for sid, s in section_ids.items()],
              'intent': {k: intent.get(k) for k in ('primary_query', 'entities')}, 'brand': brand(ctx, prior),
              'h1': plan.get('h1'), 'stack': {'framework': ctx['framework'], 'head_scope': ctx['head_scope']}}
    image_ids = {iid: (i, st) for iid, i, st in images}
    link_ids = {l['id']: l for l in links}
    candidate_urls = {u['url'] for u in candidates}

    def validate(a):
        v.require(a, 'alts', 'anchors', 'new_links')
        for i, item in enumerate(a['alts']):
            if not isinstance(item, dict) or item.get('id') not in image_ids:
                raise ValueError(f'alts[{i}].id must be one of the image ids')
            if item.get('decorative'):
                item['alt'] = ''
                continue
            item['alt'] = v.text_length(item.get('alt'), *LIMITS['ALT_CHARS'], f'alts[{i}].alt')
            if re.match(r'(?i)(image|picture|photo) of\b', item['alt']):
                raise ValueError(f'alts[{i}].alt must not start with "image of"')
            v.grounded(item['alt'], ctx['corpus'], f'alts[{i}].alt')
            v.keeps_client_terms(item['alt'], ctx['corpus'], f'alts[{i}].alt')
        for i, item in enumerate(a['anchors']):
            if not isinstance(item, dict) or item.get('id') not in link_ids:
                raise ValueError(f'anchors[{i}].id must be one of the link ids')
            v.check_anchor(item.get('anchor'), f'anchors[{i}].anchor')
            low, high = LIMITS['NAV_ANCHOR_WORDS'] if link_ids[item['id']].get('section') in ('nav', 'header', 'footer') else LIMITS['ANCHOR_WORDS']
            if not low <= len(v.words(item['anchor'])) <= high:
                raise ValueError(f'anchors[{i}].anchor must be {low}-{high} words')
            v.grounded(item['anchor'], ctx['corpus'], f'anchors[{i}]')
        if len(a['new_links']) > LIMITS['NEW_LINKS'][1]:
            raise ValueError(f'at most {LIMITS["NEW_LINKS"][1]} new links')
        for i, n in enumerate(a['new_links']):
            if not isinstance(n, dict) or n.get('href') not in candidate_urls:
                raise ValueError(f'new_links[{i}].href must be one of site_urls')
            v.check_anchor(n.get('anchor'), f'new_links[{i}].anchor')
            if n.get('section_id') not in section_ids:
                raise ValueError(f'new_links[{i}].section_id must be one of the section ids')
        return a

    answer, meta = writer.write('media-links', packet, validate, LINKS_SCHEMA)
    changes = []
    templates_done = set()
    for item in answer['alts']:
        image, status = image_ids[item['id']]
        edit = image.get('alt_edit') or image.get('edit') or {}
        if (edit.get('kind') == 'json' and edit.get('pointer')) or status == 'composed':
            pointer = (edit.get('pointer') or '').rstrip('/')
            if status == 'composed' or not pointer.endswith('/alt'):
                pointer = pointer + '/alt'
            target = ops.target_of({**edit, 'pointer': pointer, 'kind': 'json', 'attr': None}, confidence=0.9)
            if target:
                target['scope'] = 'data'
            changes.append(ops.make_change(ctx, 'media_links', 'images.alt', image.get('alt'), item['alt'], target=target, op='set_json',
                                           confidence=0.8, rationale='Per-item alt stored with the data item', extra={'src': image['src']}))
            template = edit.get('template')
            if isinstance(template, dict) and template.get('file'):
                key = (template['file'], template.get('line'))
                if key not in templates_done:
                    templates_done.add(key)
                    t_target = ops.target_of({**template, 'kind': 'literal', 'attr': 'alt'}, confidence=0.85)
                    if t_target:
                        t_target['scope'] = 'template'
                    changes.append(ops.make_change(ctx, 'media_links', 'images.alt_template', 'alt composed from the item title',
                                                   'alt={item.alt} (fallback to the current composed text)', target=t_target,
                                                   op='set_attr', confidence=0.8,
                                                   rationale='One template edit so every rendered item uses its own alt field'))
        else:
            changes.append(ops.make_change(ctx, 'media_links', 'images.alt', image.get('alt'), item['alt'],
                                           target=ops.target_of(edit, attr='alt', confidence=0.9 if edit.get('kind') == 'literal' else None),
                                           op='set_attr', confidence=0.8, rationale='Descriptive alt text (what the image shows and why)',
                                           extra={'src': image['src']}))
    for item in answer['anchors']:
        link = link_ids[item['id']]
        if v.normalize(item['anchor']) == v.normalize(link['anchor']):
            continue
        edit = link.get('anchor_edit') or link.get('edit')
        changes.append(ops.make_change(ctx, 'media_links', 'links.anchor', link['anchor'], item['anchor'],
                                       target=ops.target_of(edit, confidence=link.get('confidence')), op='set_text', confidence=0.7,
                                       rationale=item.get('reason') or 'Descriptive anchor text', extra={'href': link['href'], 'link_id': link['id']}))
    for n in answer['new_links']:
        section = section_ids[n['section_id']]
        edit = next((b['edit'] for b in reversed(section['blocks']) if b.get('edit')), section.get('edit'))
        changes.append(ops.make_change(ctx, 'media_links', 'links.new', None, {'href': n['href'], 'anchor': n['anchor']},
                                       target=ops.target_of(edit, placement=f'inside "{section["heading"]}"'), op='insert_node', confidence=0.6,
                                       html=f'<a href="{n["href"]}">{render.inline(n["anchor"])}</a>',
                                       rationale=n.get('reason') or 'Contextual internal link to a related page'))
    return result(data=answer, changes=changes, meta=meta)


# ====================================================================== 5. E-E-A-T / AEO advice (small, optional)
ADVICE_SCHEMA = {'type': 'object', 'required': ['advice', 'missing_facts']}


def check_advice(ctx, prior, writer):
    if os.getenv('SEO_ONPAGE_ADVICE', '1') == '0':
        return result(status='skipped', data={'reason': 'SEO_ONPAGE_ADVICE=0'})
    kind, ptype = site_type(ctx, prior), page_type(ctx, prior)
    packet = {'eeat_observed': ctx['eeat_observed'], 'site_type': kind, 'page_type': ptype, 'brand': brand(ctx, prior),
              'facts': facts(ctx)[:60], 'profiles': ctx['profiles'], 'topics': sorted(ADVICE_TOPICS),
              'plan': {k: plan_of(prior).get(k) for k in ('h1', 'direct_answer')},
              'stack': {'framework': ctx['framework'], 'head_scope': ctx['head_scope']}}
    personal = kind == 'personal' and ptype != 'article'

    def validate(a):
        v.require(a, 'advice', 'missing_facts')
        if not isinstance(a['advice'], list) or len(a['advice']) > 6:
            raise ValueError('advice must be a list of at most 6 items')
        for i, item in enumerate(a['advice']):
            v.one_of((item or {}).get('topic'), ADVICE_TOPICS, f'advice[{i}].topic')
            v.text_length(item.get('text'), 10, 300, f'advice[{i}].text')
            v.grounded(item['text'], ctx['corpus'], f'advice[{i}].text', numbers_allowed(ctx))
            if personal and re.search(r'(?i)author box|last[- ]updated|byline', item['text']):
                raise ValueError(f'advice[{i}]: a personal {ptype} page needs no author box / "last updated" line')
        a['missing_facts'] = v.string_list(a['missing_facts'], 0, 6, 'missing_facts')
        return a

    answer, meta = writer.write('eeat-aeo-advice', packet, validate, ADVICE_SCHEMA)
    advice = [ops.advice(ctx, 'advice', item['topic'], item['text'], impact='low') for item in answer['advice']]
    inputs = [ops.client_input(ctx, needed) for needed in answer['missing_facts']]
    return result(data=answer, advice=advice, client_inputs=inputs, meta=meta)


# ====================================================================== 6. image technical (deterministic)
def image_target(image):
    edit = image.get('alt_edit') or {}
    template = edit.get('template') or image.get('template')
    if isinstance(template, dict) and template.get('file'):
        target = ops.target_of({**template, 'kind': 'literal'}, confidence=0.85)
        if target:
            target['scope'] = 'template'
        return target
    src_edit = image.get('edit') or {}
    if src_edit.get('kind') == 'literal':
        return ops.target_of(src_edit, confidence=image.get('confidence') or 0.85)
    return None


def check_media_technical(ctx, prior=None, writer=None):
    changes, heavy = [], []
    done = set()
    for image in ctx['images']:
        if not image.get('src'):
            continue
        natural, rendered = image.get('natural') or [], image.get('rendered') or []
        target = image_target(image)
        key = (target or {}).get('key') or image['src']
        if (not image.get('width') or not image.get('height')) and len(natural) == 2 and (key, 'dims') not in done:
            done.add((key, 'dims'))
            shared = (target or {}).get('scope') == 'template'
            changes.append(ops.make_change(ctx, 'media_technical', 'images.dimensions', None,
                                           {'width': '{item.width}' if shared else natural[0], 'height': '{item.height}' if shared else natural[1],
                                            'intrinsic': natural},
                                           target=dict(target, attr='width,height') if target else None, op='set_attr', confidence=0.85,
                                           rationale='width/height (or aspect-ratio) prevent layout shift (CLS)', extra={'src': image['src']}))
        if image.get('above_fold') and image.get('loading') == 'lazy' and (key, 'lazy') not in done:
            done.add((key, 'lazy'))
            if (target or {}).get('scope') == 'template':
                changes.append(ops.make_change(ctx, 'media_technical', 'images.loading', 'lazy', 'loading={index === 0 ? "eager" : "lazy"}',
                                               target=dict(target, attr='loading'), op='set_attr', confidence=0.75,
                                               rationale='Only the first (above-the-fold, likely LCP) item should load eagerly'))
            else:
                changes.append(ops.make_change(ctx, 'media_technical', 'images.loading', 'lazy', {'loading': 'eager', 'fetchpriority': 'high'},
                                               target=dict(target, attr='loading') if target else None, op='set_attr', confidence=0.8,
                                               rationale='Above-the-fold image is lazy-loaded; this delays LCP'))
        size = image.get('transfer_bytes') or image.get('size_bytes')
        ratio_1x = round(natural[0] / rendered[0], 2) if len(natural) == 2 and len(rendered) == 2 and rendered[0] else None
        ratio_2x = round(ratio_1x / 2, 2) if ratio_1x else None
        fmt = (image.get('format') or '').lower()
        if (size and size > 200_000) or (ratio_2x and ratio_2x > 1.5) or (fmt in ('png', 'jpg', 'jpeg', 'gif') and not image.get('srcset')):
            heavy.append({'src': image['src'], 'bytes': size, 'format': fmt or None, 'natural': natural or None, 'rendered': rendered or None,
                          'overserve_1x': ratio_1x, 'overserve_2x_dpr': ratio_2x,
                          'suggest': {'max_width': int(rendered[0] * 2) if rendered else None, 'format': 'webp/avif',
                                      'srcset': not image.get('srcset')}})
    advice = []
    if heavy:
        advice.append(ops.advice(ctx, 'media_technical', 'images.weight',
                                 f'{len(heavy)} image(s) are heavier than displayed: resize to ~2x the rendered width, serve WebP/AVIF '
                                 'and add srcset/sizes (ratios measured at 1x and 2x DPR).', impact='medium', evidence=heavy))
    return result(data={'heavy_images': heavy}, changes=changes, advice=advice)


# ====================================================================== 7. accessible names (deterministic)
KNOWN_NAMES = {'github.com': 'GitHub', 'gitlab.com': 'GitLab', 'linkedin.com': 'LinkedIn', 'x.com': 'X (Twitter)',
               'twitter.com': 'X (Twitter)', 'instagram.com': 'Instagram', 'facebook.com': 'Facebook', 'youtube.com': 'YouTube',
               'medium.com': 'Medium', 'dev.to': 'DEV', 'cal.com': 'Book a call', 'calendly.com': 'Book a call'}


def link_name(href):
    if href.startswith('mailto:'):
        return 'Email'
    if href.startswith('tel:'):
        return 'Phone'
    host = (urlsplit(href).hostname or '').lower().removeprefix('www.')
    return KNOWN_NAMES.get(host) or (host.split('.')[0].title() if host else 'Link to ' + href)


def check_accessibility(ctx, prior=None, writer=None):
    changes, advice = [], []
    for link in ctx['links']:
        href = link.get('href') or ''
        if not link['renders']:
            continue
        if href in ('#', '') or href.lower().startswith('javascript:'):
            advice.append(ops.advice(ctx, 'accessibility', 'links.js_only', f'Link "{link["accessible_name"] or href}" has no crawlable '
                                     'href (#/javascript:); use a real URL so crawlers can follow it', impact='medium',
                                     evidence={'link_id': link['id']}))
            continue
        if link['accessible_name']:
            continue  # visible text, aria-label, img alt or title already name it (no false positives)
        edit = link.get('anchor_edit') or link.get('edit')
        changes.append(ops.make_change(ctx, 'accessibility', 'links.accessible_name', None, link_name(href),
                                       target=ops.target_of(edit, attr='aria-label', confidence=link.get('confidence')), op='set_attr',
                                       confidence=0.85, rationale='Icon-only link has no accessible name or anchor context',
                                       extra={'href': href, 'link_id': link['id']}))
        status = link.get('status')
        if isinstance(status, int) and status >= 400 and status not in (401, 403, 429, 999):
            changes.append(ops.make_change(ctx, 'accessibility', 'links.broken', href, '[VERIFY: working URL]',
                                           target=ops.target_of(link.get('edit')), op='set_attr', confidence=0.7,
                                           rationale=f'Link returns HTTP {status}', extra={'link_id': link['id']}))
    for link in ctx['links']:
        status = link.get('status')
        if link['renders'] and link['accessible_name'] and isinstance(status, int) and status >= 400 and status not in (401, 403, 429, 999):
            changes.append(ops.make_change(ctx, 'accessibility', 'links.broken', link['href'], '[VERIFY: working URL]',
                                           target=ops.target_of(link.get('edit')), op='set_attr', confidence=0.7,
                                           rationale=f'Link returns HTTP {status}', extra={'link_id': link['id']}))
    return result(changes=changes, advice=advice)


# ====================================================================== 8. technical (deterministic)
def platform_change(field, current, value, *, where, hosting, rationale, change_id, impact='high'):
    change = ops.make_change(None, 'technical', field, current, value, target=ops.platform_target(where, hosting), op='platform_config',
                             confidence=0.9, rationale=rationale, impact=impact, change_id_=change_id)
    change['scope'] = 'site'
    return change


def check_technical(ctx, prior=None, writer=None):
    changes, advice, site_changes = [], [], []
    head = ctx['head']
    canonical = value(head.get('canonical'))
    expected_canonical = ctx['absolute_url'] or ctx['url']
    if not canonical or canonical.rstrip('/') != expected_canonical.rstrip('/'):
        target = ops.target_of(head.get('canonical'), attr='href') or add_target(ctx, 'technical.canonical')
        changes.append(ops.make_change(ctx, 'technical', 'technical.canonical', canonical, expected_canonical, target=target,
                                       op='set_attr' if canonical else 'insert_node', confidence=0.9,
                                       html=f'<link rel="canonical" href="{expected_canonical}">',
                                       rationale='Self-referencing absolute canonical for THIS route on the canonical host'))
    robots = value(head.get('robots')) or ''
    x_robots = ((ctx['http'].get('headers') or {}).get('x-robots-tag')) or ''
    if re.search(r'\bnoindex\b', (robots + ' ' + x_robots).lower()):
        changes.append(ops.make_change(ctx, 'technical', 'technical.robots', robots or x_robots, 'index, follow',
                                       target=ops.target_of(head.get('robots'), attr='content'), op='set_attr', confidence=0.8,
                                       rationale='A page meant to rank is noindexed'))
    if not value(head.get('lang')):
        changes.append(ops.make_change(ctx, 'technical', 'technical.lang', None, ctx['lang'], target=add_target(ctx, 'technical.lang'),
                                       op='set_attr', confidence=0.8, rationale='Declare the content language on <html>'))
    viewport = value(head.get('viewport'))
    if not viewport or 'width=device-width' not in viewport:
        changes.append(ops.make_change(ctx, 'technical', 'technical.viewport', viewport, 'width=device-width, initial-scale=1',
                                       target=ops.target_of(head.get('viewport'), attr='content') or add_target(ctx, 'technical.viewport'),
                                       op='set_attr' if viewport else 'insert_node', confidence=0.85, rationale='Mobile-friendly viewport'))
    chain = ctx['http'].get('redirect_chain') or []
    temporary = [r for r in chain if r.get('http_status') in (302, 303, 307)]
    if temporary:
        host = ctx['hosting']
        site_changes.append(platform_change(
            'platform.redirect', [f"{r['url']} -> {r.get('http_status')} -> {r.get('location')}" for r in temporary],
            [f"{r['url']} -> 308 -> {r.get('location')}" for r in temporary],
            where=REDIRECT_WHERE.get(host, 'hosting / web server redirect rules (make it permanent: 301 or 308)'), hosting=host,
            rationale='Host redirect is temporary; a permanent redirect consolidates signals on the canonical host',
            change_id='chg_redirect_permanent'))
    rendered = ctx['rendering'].get('rendered_words') or ctx['main_words']
    raw = ctx['rendering'].get('raw_html_words')
    if raw is None and 'CSR' in (ctx['raw_source'].get('rendering_modes') or []):
        raw = ctx['raw_source'].get('word_count')
    csr = ctx['head_scope'] == 'shared-shell' or (rendered and raw is not None and raw < 0.5 * rendered)
    if csr:
        recipe = ctx['recipe']
        site_changes.append(platform_change(
            'platform.prerender', {'raw_html_words': raw, 'rendered_words': rendered, 'head_scope': ctx['head_scope']},
            'Serve each route\'s own title, description, canonical, H1, main content and JSON-LD in the initial HTML',
            where=recipe.get('precondition') or recipe.get('mechanism') or 'prerender/SSG or SSR', hosting=ctx['hosting'],
            change_id='chg_prerender',
            rationale='Every route is served the same client-side shell; crawlers that do not run JavaScript (social previews, '
                      'most AI crawlers) see one identical, almost empty page for all URLs. Per-route head changes depend on this.'))
        advice.append(ops.advice(ctx, 'technical', 'technical.prerender',
                                 f'Initial HTML has {raw if raw is not None else "~0"} words vs {rendered} after JavaScript, and the head is shared by '
                                 f'every route. Enable prerendering first ({recipe.get("precondition") or recipe.get("mechanism")}); the head changes on this page are '
                                 'blocked until then.', impact='high'))
    served = ctx['site_served'] or {}
    soft = served.get('soft_404') if isinstance(served.get('soft_404'), dict) else {}
    soft_flag = ctx['site'].get('soft_404') if not isinstance(ctx['site'].get('soft_404'), dict) else ctx['site']['soft_404'].get('soft_404')
    if soft.get('soft_404') or soft_flag is True:
        site_changes.append(platform_change(
            'platform.soft_404', 'unknown URLs return 200 with a client-side "not found" view',
            'Return HTTP 404 for unknown URLs (serve a static 404 page; limit SPA rewrites to real routes)',
            where=SOFT_404_WHERE.get(ctx['hosting'], 'hosting/server config: return 404 for unknown paths'), hosting=ctx['hosting'],
            rationale='Soft 404s waste crawl budget and can get thin error views indexed', change_id='chg_soft_404', impact='high'))
    slash = served.get('trailing_slash') or {}
    if slash.get('with_slash') == 200 and slash.get('without_slash') == 200:
        advice.append(ops.advice(ctx, 'technical', 'technical.trailing_slash', 'Both /path and /path/ return 200 (duplicate URLs); '
                                 'redirect one form permanently or rely on a self-referencing canonical per route.', impact='medium'))
    parity = ctx['mobile_parity'] or {}
    if parity.get('headings_missing_on_mobile') or parity.get('links_missing_on_mobile'):
        advice.append(ops.advice(ctx, 'technical', 'technical.mobile_parity', 'Mobile DOM is missing headings/links that desktop has; '
                                 'mobile-first indexing uses the mobile version.', evidence=parity))
    slug = ctx['url'].strip('/').split('/')[-1] if ctx['url'] != '/' else ''
    if slug and (slug != slug.lower() or '_' in slug or len(slug.split('-')) > 6 or re.search(r'\d{4,}', slug)):
        advice.append(ops.advice(ctx, 'technical', 'technical.slug', 'Readable, lowercase, hyphenated slug (changing it needs a 301)',
                                 impact='low', evidence={'url': ctx['url']}))
    return result(data={'expected_canonical': expected_canonical, 'csr_shell': bool(csr)}, changes=changes, advice=advice,
                  site_changes=site_changes)


# ====================================================================== 9. structured data (deterministic @id graph)
ENTITY_TYPES = {'personal': 'Person'}
PAGE_NODE = {'home': 'WebPage', 'profile': 'ProfilePage', 'about': 'AboutPage', 'contact': 'ContactPage',
             'collection': 'CollectionPage', 'article': 'BlogPosting', 'product': 'WebPage', 'docs': 'WebPage', 'other': 'WebPage'}


def job_title(ctx, name):
    title = ctx['title'] or ''
    parts = [p.strip() for p in re.split(r'\s+[|—–-]\s+', title) if p.strip()]
    for part in parts:
        if name and v.normalize(part) != v.normalize(name) and len(part.split()) <= 5 and v.normalize(part) in v.normalize(ctx['corpus']):
            return part
    return None


def check_structured_data(ctx, prior, writer=None):
    name = brand(ctx, prior)
    origin = ctx['origin'] or ''
    url = ctx['absolute_url'] or ctx['url']
    kind = site_type(ctx, prior)
    ptype = page_type(ctx, prior)
    plan = plan_of(prior)
    entity_type = ENTITY_TYPES.get(kind, 'Organization')
    entity = {'@type': entity_type, '@id': origin + '/#' + entity_type.lower(), 'name': name, 'url': origin + '/'}
    if ctx['profiles']:
        entity['sameAs'] = ctx['profiles']
    inputs = []
    if entity_type == 'Person':
        role = job_title(ctx, name)
        if role:
            entity['jobTitle'] = role
        portrait = next((i for i in ctx['images'] if i.get('src') and not LOGOISH.search(i['src']) and
                         re.search(r'(?i)(portrait|headshot|profile|avatar|me\b|photo)', i['src'] + ' ' + (i.get('alt') or ''))), None)
        if portrait:
            entity['image'] = (origin + portrait['src']) if portrait['src'].startswith('/') else portrait['src']
        else:
            inputs.append(ops.client_input(ctx, 'Portrait photo URL for Person.image (a logo or favicon is not a person image)',
                                           field='head.json_ld'))
    else:
        logo = next((a['src'] for a in ctx['assets'] if LOGOISH.search(a['src'] or '')), None)
        if logo:
            entity['logo'] = (origin + logo) if logo.startswith('/') else logo
    page_node_type = 'ProfilePage' if ptype == 'home' and kind == 'personal' else PAGE_NODE.get(ptype, 'WebPage')
    page_node = {'@type': page_node_type, '@id': url + '#webpage', 'url': url, 'name': plan.get('title') or ctx['title'],
                 'isPartOf': {'@id': origin + '/#website'}, 'inLanguage': ctx['lang']}
    if plan.get('description') or ctx['description']:
        page_node['description'] = plan.get('description') or ctx['description']
    if page_node_type == 'ProfilePage':
        page_node['mainEntity'] = {'@id': entity['@id']}
    else:
        page_node['about'] = {'@id': entity['@id']}
    graph = [page_node]
    if page_node_type == 'BlogPosting':
        page_node.update({'headline': plan.get('h1') or ctx['title'], 'author': {'@id': entity['@id']}, 'mainEntityOfPage': url})
        if not any(s.get('signal') == 'dates' and s.get('status') == 'observed' for s in ctx['eeat_observed']):
            inputs.append(ops.client_input(ctx, 'datePublished for this post (no visible date in the page)', field='head.json_ld'))
    if ctx['is_home']:
        graph.append({'@type': 'WebSite', '@id': origin + '/#website', 'url': origin + '/', 'name': name})
    graph.append(entity)
    if not ctx['is_home']:
        crumbs = [{'@type': 'ListItem', 'position': 1, 'name': 'Home', 'item': origin + '/'},
                  {'@type': 'ListItem', 'position': 2, 'name': plan.get('h1') or ctx['title'] or ctx['url'], 'item': url}]
        graph.append({'@type': 'BreadcrumbList', '@id': url + '#breadcrumb', 'itemListElement': crumbs})
    faq = plan.get('faq') or []
    if faq:  # mirrors the visible FAQ verbatim (P10)
        graph.append({'@type': 'FAQPage', '@id': url + '#faq', 'mainEntity': [
            {'@type': 'Question', 'name': q['question'], 'acceptedAnswer': {'@type': 'Answer', 'text': q['answer']}} for q in faq]})
    data = {'@context': 'https://schema.org', '@graph': graph}
    current = [{'types': j.get('types'), 'valid': j.get('valid')} for j in ctx['json_ld']] or None
    target = add_target(ctx, 'head.json_ld')
    change = ops.make_change(ctx, 'structured_data', 'head.json_ld', current, data, target=target,
                             op='insert_node' if not current else 'set_text', confidence=0.85, html=render.json_ld(data),
                             rationale='JSON-LD @id graph that mirrors visible content: ' + ', '.join(n['@type'] for n in graph))
    for item in inputs:
        item['change'] = change['id']
    return result(data={'json_ld': data, 'types': [n['@type'] for n in graph]}, changes=[change], client_inputs=inputs)


# ====================================================================== 10. social meta (deterministic)
def share_image(ctx):
    """og:image must be absolute and ~1200x630 (1.91:1); otherwise it is a client input, not a guess."""
    og = ctx['head'].get('og') or {}
    current = value(og.get('image'))
    if current:
        return current, None
    for image in ctx['images']:
        natural = image.get('natural') or []
        if image.get('src') and len(natural) == 2 and natural[0] >= 1200 and 1.85 <= natural[0] / max(natural[1], 1) <= 1.97:
            src = image['src']
            return (ctx['origin'] + src if ctx['origin'] and src.startswith('/') else src), None
    return '[VERIFY: 1200x630 share image]', '1200x630 share image (og:image); page screenshots are the wrong size/aspect'


def check_social_meta(ctx, prior, writer=None):
    plan = plan_of(prior)
    title = plan.get('title') or ctx['title']
    description = plan.get('description') or ctx['description']
    name = brand(ctx, prior)
    og = ctx['head'].get('og') or {}
    tw = ctx['head'].get('twitter') or {}
    og_title = title
    if name and title:
        stripped = re.split(r'\s+[|—–-]\s+' + re.escape(name) + r'\s*$', title)[0]
        og_title = stripped if len(stripped) >= 10 else title
    image, needed = share_image(ctx)
    expected = [('og:title', og_title), ('og:description', description), ('og:type', 'article' if page_type(ctx, prior) == 'article' else 'website'),
                ('og:url', ctx['absolute_url'] or ctx['url']), ('og:image', image), ('og:site_name', name),
                ('twitter:card', 'summary_large_image')]
    expected = [(k, e) for k, e in expected if e]
    inputs = []
    changes = []
    if not og and not tw:
        # nothing exists yet: one atomic insert (no per-tag + composite duplicates)
        change = ops.make_change(ctx, 'social_meta', 'head.social', None, dict(expected), target=add_target(ctx, 'head.social'),
                                 op='insert_node', confidence=0.85, html=render.head_tags(expected),
                                 rationale='Share preview (Open Graph / X card); social crawlers do not run JavaScript')
        changes.append(change)
    else:
        for key, exp in expected:
            group, prop = key.split(':', 1)
            cur_field = (og if group == 'og' else tw).get(prop)
            cur = value(cur_field)
            if cur == exp:
                continue
            changes.append(ops.make_change(ctx, 'social_meta', 'head.' + key.replace(':', '.'), cur, exp,
                                           target=ops.target_of(cur_field, attr='content') or add_target(ctx, 'head.' + key),
                                           op='set_attr' if cur else 'insert_node', confidence=0.8, html=render.head_tags([(key, exp)]),
                                           rationale='Share preview (Open Graph / X card)'))
    if needed and changes:
        inputs.append(ops.client_input(ctx, needed, change=changes[0]['id'], field='head.og.image'))
        for change in changes:
            if change['field'] in ('head.social', 'head.og.image') and change['status'] == 'proposed':
                change['status'] = 'needs_input'
    return result(data={'expected': dict(expected)}, changes=changes, client_inputs=inputs)


# ====================================================================== 11. fact-check + freshness (deterministic)
FRESH_CONTEXT = re.compile(r'(?i)(©|copyright|updated|as of|latest|since|in (19|20)\d\d)')
YEAR = re.compile(r'\b(19|20)\d{2}\b')
SKIP_STRING_KEYS = {'@id', '@type', 'url', 'src', 'href', '@context', 'image', 'logo', 'item', 'sameAs', 'dateModified', 'email',
                    'isPartOf', 'mainEntityOfPage', 'inLanguage'}


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, item in obj.items():
            if k not in SKIP_STRING_KEYS:
                yield from _strings(item)
    elif isinstance(obj, list):
        for item in obj:
            yield from _strings(item)


def check_fact_check(ctx, prior, writer=None):
    corpus = ctx['corpus']
    corpus_words = set(re.findall(r'[a-z0-9]+', corpus.lower()))
    allowed = numbers_allowed(ctx)
    report, inputs = [], []
    written_by_model = {name for name, _skill, _fn, uses_llm in CHECKS if uses_llm}
    for check, res in prior.items():
        if check not in written_by_model:
            continue
        for change in (res or {}).get('changes') or []:
            texts = list(_strings(change.get('value')))
            joined = ' '.join(texts)
            sentences = [x for t in texts for x in re.split(r'(?<=[.!?:])\s+', v.VERIFY.sub(' ', v.strip_markup(t)))]
            numbers_bad = v.ungrounded_numbers(joined, corpus, allowed)
            capitalised = [w for s in sentences for w in re.findall(r'\b[A-Z][A-Za-z0-9]{2,}\b', s.split(' ', 1)[-1] if ' ' in s else '')]
            terms = sorted({t for t in capitalised if t.lower() not in corpus_words and t.lower() not in v.STOPWORDS})
            placeholders = v.VERIFY.findall(joined)
            change['evidence']['fact_check'] = {'needs_verification': numbers_bad, 'unverified_terms': terms[:15], 'placeholders': placeholders}
            if numbers_bad or placeholders or terms:
                change['status'] = 'needs_input'
            inputs += [ops.client_input(ctx, p, change=change['id'], field=change['field']) for p in placeholders]
            inputs += [ops.client_input(ctx, f'Confirm "{t}" (not found in the site content)', change=change['id'], field=change['field'])
                       for t in terms[:5]]
            if numbers_bad or terms:
                report.append({'change': change['id'], 'numbers': numbers_bad, 'terms': terms[:15]})
    run_year = int(ctx['run_date'][:4])
    stale = [{'text': t[:160], 'year': int(mt.group(0))} for t in ctx['copy'] for mt in YEAR.finditer(t)
             if int(mt.group(0)) < run_year - 2 and FRESH_CONTEXT.search(t)]
    changes, advice = [], []
    if stale:
        advice.append(ops.advice(ctx, 'fact_check', 'content.freshness', f'Outdated years in freshness-sensitive copy (as of {run_year})',
                                 impact='low', evidence=stale))
    has_date = any(s.get('signal') == 'dates' and s.get('status') == 'observed' for s in ctx['eeat_observed'])
    if page_type(ctx, prior) == 'article' and not has_date:  # personal/home pages do not get a "Last updated" line
        anchor = first_main_edit(ctx, heading=True)
        changes.append(ops.make_change(ctx, 'fact_check', 'freshness.published', None, '[VERIFY: publication date]',
                                       target=ops.target_of(anchor, placement='under the post title') if anchor else None, op='insert_node',
                                       confidence=0.6, html='<time datetime="[VERIFY]">[VERIFY: publication date]</time>',
                                       rationale='Visible publication/updated date for a post (mirrored in BlogPosting dates)', status='needs_input'))
        inputs.append(ops.client_input(ctx, 'Publication date for this post', change=changes[-1]['id'], field='freshness.published'))
    return result(data={'claims_report': report, 'stale': stale,
                        'policy': 'numbers (digits and spelled-out) must appear in the visible text/data JSON; capitalised terms not '
                                  'found there go to client_inputs'},
                  changes=changes, advice=advice, client_inputs=inputs)


# ====================================================================== 12. QA: scope gate + consolidation + checklist (deterministic)
def outcome(changes, *fields):
    hits = [c for c in changes if c['field'].startswith(fields)]
    for status, label in (('proposed', True), ('blocked', 'blocked'), ('needs_input', 'needs_input')):
        if any(c['status'] == status for c in hits):
            return label
    return False


def checklist(ctx, prior, final):
    h1s = [h for h in ctx['headings'] if h['level'] == 1]
    kw = (prior.get('keyword_plan') or {}).get('data') or {}
    placements = kw.get('placements_current') or {}
    canonical = value(ctx['head'].get('canonical'))
    expected_canonical = ctx['absolute_url'] or ctx['url']
    images_missing = [i for i in ctx['images'] if i.get('src') and alt_status(i) in ('missing', 'generic')]
    items = {
        'title_tag': bool(ctx['title']) and LIMITS['TITLE_CHARS'][0] <= len(ctx['title'] or '') <= LIMITS['TITLE_CHARS'][1],
        'meta_description': bool(ctx['description']) and LIMITS['DESCRIPTION_CHARS'][0] <= len(ctx['description'] or '') <= LIMITS['DESCRIPTION_CHARS'][1],
        'single_h1': len(h1s) == 1,
        'direct_answer': False,
        'keyword_placement': all(placements.get(k) for k in ('title', 'h1', 'first_100_words', 'meta_description')),
        'image_alt_text': not images_missing,
        'canonical': bool(canonical) and canonical.rstrip('/') == expected_canonical.rstrip('/'),
        'social_meta': bool((ctx['head'].get('og') or {}).get('title')) and bool((ctx['head'].get('og') or {}).get('image')),
        'structured_data': bool(ctx['json_ld']),
        'served_html': ctx['head_scope'] != 'shared-shell',
    }
    after_fields = {
        'title_tag': ('head.title',), 'meta_description': ('head.description',), 'single_h1': ('headings.h1',),
        'direct_answer': ('content.direct_answer',), 'keyword_placement': ('head.title', 'headings.h1', 'content.direct_answer'),
        'image_alt_text': ('images.alt',), 'canonical': ('technical.canonical',), 'social_meta': ('head.social', 'head.og'),
        'structured_data': ('head.json_ld',), 'served_html': ('platform.prerender',),
    }
    site_changes = [c for c in final if c['op'] == 'platform_config']
    out = {}
    for key, before in items.items():
        after = True if before else outcome(final, *after_fields[key])
        if key == 'served_html' and not before:
            after = 'needs_platform_change' if site_changes else False
        out[key] = {'before': before, 'after': after}
    return out


def check_qa(ctx, prior, writer=None):
    collected, advice, inputs, site_changes = [], [], [], []
    for name, _skill, _fn, _llm in CHECKS:
        res = prior.get(name) or {}
        collected += res.get('changes') or []
        advice += res.get('advice') or []
        inputs += res.get('client_inputs') or []
        site_changes += res.get('site_changes') or []
    gated, needs_prerender, needs_per_route = ops.gate(collected, ctx)
    if needs_per_route and not any(c['id'] == 'chg_per_route_head' for c in site_changes):
        site_changes.append(platform_change('platform.per_route_head', 'head shared by several routes',
                                            'Give each route its own title/description/canonical/JSON-LD',
                                            where=ctx['recipe'].get('mechanism') or 'per-route head', hosting=ctx['hosting'],
                                            rationale='The head lives in a shared layout; per-route values need the stack\'s head API',
                                            change_id='chg_per_route_head'))
    changes, more = ops.consolidate(gated, ctx)
    seen_inputs, unique_inputs = set(), []
    for item in inputs:
        key = (item['needed'], item.get('change'))
        if key not in seen_inputs:
            seen_inputs.add(key)
            unique_inputs.append(item)
    failed = {k: r.get('errors') for k, r in prior.items() if (r or {}).get('status') == 'failed'}
    final = changes + more + site_changes
    return result(data={'changes': changes, 'changes_more': more, 'advice': advice, 'client_inputs': unique_inputs,
                        'site_changes': site_changes, 'checklist': checklist(ctx, prior, final),
                        'expected_page_html': render.page_from_changes(changes + more, ctx), 'failed_checks': failed,
                        'needs_prerender': needs_prerender})


# ---------------------------------------------------------------------- registry (execution order)
CHECKS = [
    ('intent', 'search-intent', check_intent, True),
    ('keyword_plan', 'keyword-integration', check_keyword_plan, False),
    ('page_plan', 'page-plan', check_page_plan, True),
    ('media_links', 'media-links', check_media_links, True),
    ('advice', 'eeat-aeo-advice', check_advice, True),
    ('media_technical', 'media-technical', check_media_technical, False),
    ('accessibility', 'link-accessibility', check_accessibility, False),
    ('technical', 'technical-onpage', check_technical, False),
    ('structured_data', 'structured-data', check_structured_data, False),
    ('social_meta', 'social-meta', check_social_meta, False),
    ('fact_check', 'fact-check-freshness', check_fact_check, False),
    ('qa', 'qa-consolidation', check_qa, False),
]
DEPENDS = {'keyword_plan': ['intent'], 'page_plan': ['intent'], 'media_links': ['intent'], 'advice': ['intent']}
