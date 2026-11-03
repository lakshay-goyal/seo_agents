"""Check 4 (model, small): image alt text, generic-anchor rewrites and up to three contextual internal links."""

from __future__ import annotations

import re

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.changes import page_preview_renderer as render
from seo_agents.onpage.validation import content_validators as v
from seo_agents.onpage.reports.exploration_reports import clean
from seo_agents.onpage.validation.content_policy import LIMITS
from seo_agents.onpage.checks.check_helpers import WEAK_ALT, brand, intent_of, plan_of, result
from seo_agents.onpage.checks.base_check import OnpageCheck

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


class MediaLinksCheck(OnpageCheck):
    """Check: Alt text for weak/missing alts, better generic anchors, up to three internal links (model, small)."""

    name = 'media_links'
    skill = 'media-links'
    uses_model = True
    depends_on = ('intent',)

    def run(self, ctx, prior, writer):
        return check_media_links(ctx, prior, writer)
