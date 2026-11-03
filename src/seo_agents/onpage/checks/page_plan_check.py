"""Check 3 (model, mid): one plan for title, meta description, H1, outline, answer-first opening, sections and FAQ."""

from __future__ import annotations

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.changes import page_preview_renderer as render
from seo_agents.onpage.validation import content_validators as v
from seo_agents.onpage.page_context.page_context_builder import facts
from seo_agents.onpage.validation.content_policy import LIMITS
from seo_agents.onpage.checks.check_helpers import add_target, brand, first_main_edit, intent_of, numbers_allowed, page_type, result, site_type, site_url_set
from seo_agents.onpage.checks.base_check import OnpageCheck

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


class PagePlanCheck(OnpageCheck):
    """Check: One coherent plan: title, meta description, H1, headings, answer-first opening, sections, FAQ (model, mid)."""

    name = 'page_plan'
    skill = 'page-plan'
    uses_model = True
    depends_on = ('intent',)

    def run(self, ctx, prior, writer):
        return check_page_plan(ctx, prior, writer)
