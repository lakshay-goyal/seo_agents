"""Check 9 (deterministic): JSON-LD @id graph (WebSite, Person/Organization, page node, breadcrumbs, FAQPage)."""

from __future__ import annotations

import re

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.changes import page_preview_renderer as render
from seo_agents.onpage.validation import content_validators as v
from seo_agents.onpage.page_context.page_context_builder import LOGOISH
from seo_agents.onpage.checks.check_helpers import add_target, brand, page_type, plan_of, result, site_type
from seo_agents.onpage.checks.base_check import OnpageCheck

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


class StructuredDataCheck(OnpageCheck):
    """Check: JSON-LD @id graph from the exploration facts and the page plan (deterministic)."""

    name = 'structured_data'
    skill = 'structured-data'
    uses_model = False
    depends_on = ()

    def run(self, ctx, prior, writer):
        return check_structured_data(ctx, prior, writer)
