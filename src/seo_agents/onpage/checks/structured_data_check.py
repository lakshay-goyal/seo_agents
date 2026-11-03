"""Check 9 (deterministic): strict JSON-LD @id graph that mirrors everything visible on the page.

Nodes (each only when the page shows the facts): Person/Organization (+logo, sameAs, email, telephone), WebSite,
the page node (WebPage/ItemPage/AboutPage/ContactPage/CollectionPage/ProfilePage), BreadcrumbList from the real path
hierarchy, primary ImageObject, BlogPosting/Article/TechArticle, Product or SoftwareApplication/WebApplication
(+Offer per visible plan, first-party AggregateRating, featureList), FAQPage, HowTo, ItemList, VideoObject.
The graph is validated against schema.org/Google rules; optional nodes with errors are pruned, never shipped, and
properties only the owner can supply become client inputs.
"""

from __future__ import annotations

import re

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.changes import page_preview_renderer as render
from seo_agents.onpage.validation import content_validators as v
from seo_agents.onpage.page_context.page_context_builder import LOGOISH
from seo_agents.onpage.checks.check_helpers import add_target, brand, intent_of, page_type, plan_of, result, site_type
from seo_agents.onpage.checks.base_check import OnpageCheck
from seo_agents.onpage.structured_data import schema_facts as facts
from seo_agents.onpage.structured_data import schema_nodes as nodes
from seo_agents.onpage.structured_data import schema_rules as rules

# ====================================================================== 9. structured data (deterministic @id graph)
ENTITY_TYPES = {'personal': 'Person'}
PAGE_NODE = {'home': 'WebPage', 'profile': 'ProfilePage', 'about': 'AboutPage', 'contact': 'ContactPage',
             'collection': 'CollectionPage', 'article': 'WebPage', 'product': 'ItemPage', 'docs': 'WebPage', 'other': 'WebPage'}
PRODUCT_URL = re.compile(r'(?i)/(?:products?|pricing|plans|features|platform|solutions?|apps?|tools?|software|editions?)'
                         r'(?:/[^/]+)?/?$')
DOCS_URL = re.compile(r'(?i)/(?:docs?|documentation|guides?|tutorials?|help|kb|knowledge-base|learn)(?:/|$)')
BLOG_URL = re.compile(r'(?i)/(?:blog|posts?|news|journal|stories)/')


def job_title(ctx, name):
    title = ctx['title'] or ''
    parts = [p.strip() for p in re.split(r'\s+[|—–-]\s+', title) if p.strip()]
    for part in parts:
        if name and v.normalize(part) != v.normalize(name) and len(part.split()) <= 5 and v.normalize(part) in v.normalize(ctx['corpus']):
            return part
    return None


def schema_page_kind(ctx, prior, ptype, kind, offers):
    """product | article | docs | collection | <page type>: which main entity this page describes."""
    url = ctx['url'] or '/'
    if ptype == 'article' or (ptype == 'other' and BLOG_URL.search(url)):
        return 'article'
    if ptype == 'docs' or (ptype == 'other' and DOCS_URL.search(url)):
        return 'docs'
    if ptype == 'product' or (ptype == 'other' and kind != 'personal' and (PRODUCT_URL.search(url) or offers)):
        return 'product'
    if ptype == 'home' and kind in ('business', 'ecommerce', 'other') and offers:
        return 'product'
    return ptype


def product_name(ctx, prior, name):
    """The product's visible name: an intent entity shown in the H1/title, else the first title segment."""
    h1, title = facts.current_h1(ctx) or '', ctx['title'] or ''
    for candidate in list(intent_of(prior).get('entities') or []) + [name]:
        if candidate and (v.normalize(candidate) in v.normalize(h1) or v.normalize(candidate) in v.normalize(title)):
            return candidate
    if re.search(r'(?i)/(?:pricing|plans)/?$', ctx['url'] or '') and name:
        return name
    head = facts.title_head(title)
    return head if head and len(head.split()) <= 6 else (h1 if h1 and len(h1.split()) <= 6 else name)


def crumbs_for(ctx, origin, url, last_name):
    rows = [('Home', origin + '/')]
    rows += [(label, origin + path) for path, label in facts.trail(ctx)]
    return rows + [(last_name, url)]


MERGE_TYPES = {'Organization', 'Person', 'Product', 'SoftwareApplication', 'WebApplication', 'BlogPosting', 'Article',
               'TechArticle', 'WebSite', 'HowTo'}


def existing_nodes(rows):
    """Top-level nodes of the page's current (valid) JSON-LD blocks."""
    out = []
    for row in rows or []:
        value = row.get('value') if isinstance(row, dict) and row.get('valid') is not False else None
        stack = [value]
        while stack:
            item = stack.pop(0)
            if isinstance(item, list):
                stack += item
            elif isinstance(item, dict):
                if isinstance(item.get('@graph'), list):
                    stack += item['@graph']
                elif item.get('@type'):
                    out.append({k: v for k, v in item.items() if k != '@context'})
    return out


def error_rows(graph):
    return rules.errors_of(rules.validate({'@context': rules.CONTEXT, '@graph': graph}))


def merge_existing(graph, rows, url):
    """Keep what the owner already marked up: extra properties of a node we regenerate are added to ours (our
    values win), and nodes of types we do not generate (Event, LocalBusiness, Course, Review...) are carried over
    with an @id. Everything still goes through validation, so bad carried-over markup is pruned, never shipped."""
    by_type = {}
    for node in graph:
        for t in rules.types_of(node):
            by_type.setdefault(t, node)
    generated = set(by_type)
    carried, kept = [], []
    for n, node in enumerate(existing_nodes(rows), 1):
        kinds = rules.types_of(node)
        same = next((by_type[t] for t in kinds & MERGE_TYPES if t in by_type), None)
        for family in (rules.ARTICLE_TYPES, rules.PAGE_TYPES - {'FAQPage', 'QAPage'}):  # one article, one page node
            if same is None and kinds & family:
                same = next((by_type[t] for t in family if t in by_type), None)
        if same is not None:
            for key, value in node.items():
                if key not in same and not key.startswith('@') and not rules.is_ref(value):
                    before = len(error_rows(graph))
                    same[key] = value
                    if len(error_rows(graph)) > before:  # invalid owner value: never let it break our node
                        del same[key]
            continue
        if kinds & generated or not kinds:
            continue
        node = dict(node)
        node['@id'] = node.get('@id') if isinstance(node.get('@id'), str) and '#' in node['@id'] and \
            node['@id'].startswith('http') else f'{url}#existing-{sorted(kinds)[0].lower()}-{n}'
        if node['@id'] in {g.get('@id') for g in graph + carried}:
            continue
        carried.append(node)
        kept += sorted(kinds)
    graph += carried
    return kept


def check_structured_data(ctx, prior, writer=None):
    name = brand(ctx, prior)
    origin = (ctx['origin'] or '').rstrip('/')
    url = facts.page_url(ctx) or ctx['url']
    kind = site_type(ctx, prior)
    ptype = page_type(ctx, prior)
    plan = plan_of(prior)
    lang = ctx['lang']
    entity_type = ENTITY_TYPES.get(kind, 'Organization')
    id_of = nodes.ids(origin, url, entity_type)
    visible = facts.visible_text(ctx, plan)
    found_offers = facts.offers(ctx)
    schema_kind = schema_page_kind(ctx, prior, ptype, kind, found_offers)
    title = rules.plain_text(plan.get('title') or ctx['title'] or name)
    description = rules.plain_text(plan.get('description') or ctx['description']) or None
    page_name = plan.get('h1') or facts.current_h1(ctx) or facts.title_head(ctx['title']) or name
    inputs, notes = [], []

    def ask(needed, site=False):
        """Client input; `site=True` for facts about the whole site (domain, logo, portrait): one id for every page."""
        item = ops.client_input({'page_id': 'site'} if site else ctx, needed, field='head.json_ld')
        inputs.append({**item, 'scope': 'site'} if site else item)

    if not origin:
        ask('Production domain (https://...) so JSON-LD @id and url values are absolute', site=True)

    # ---- entity (Person / Organization)
    contact = facts.contact(ctx)
    profiles = facts.same_as(ctx['profiles'])
    logo = facts.logo(ctx) if entity_type == 'Organization' else None
    graph_extra = []
    if entity_type == 'Person':
        portrait = next((i for i in ctx.get('images') or [] if i.get('src') and not LOGOISH.search(i['src']) and
                         re.search(r'(?i)(portrait|headshot|profile|avatar|\bme\b|photo)', i['src'] + ' ' + (i.get('alt') or ''))), None)
        if not portrait:
            ask('Portrait photo URL for Person.image (a logo or favicon is not a person image)', site=True)
        entity = nodes.entity('Person', id_of['entity'], name=name, url=origin + '/', same_as=profiles,
                              image=facts.absolute(ctx, portrait['src']) if portrait else None,
                              job_title=job_title(ctx, name), email=contact['email'], telephone=contact['telephone'])
    else:
        if logo:
            graph_extra.append(nodes.image_object(id_of['logo'], logo, caption=name))
        else:
            ask('Logo image URL (square, at least 112x112 px, crawlable) for Organization.logo', site=True)
        entity = nodes.entity('Organization', id_of['entity'], name=name, url=origin + '/', same_as=profiles,
                              logo_id=id_of['logo'] if logo else None, email=contact['email'], telephone=contact['telephone'])
        if not profiles and ctx['is_home']:
            notes.append('Organization.sameAs is empty: link the official social/directory profiles on the page')

    # ---- primary image
    images = facts.page_images(ctx)
    primary = images[0] if images else None
    if primary:
        graph_extra.append(nodes.image_object(id_of['image'], primary))

    # ---- page node
    page_node_type = 'ProfilePage' if ptype == 'home' and kind == 'personal' else PAGE_NODE.get(ptype, 'WebPage')
    if schema_kind == 'product' and page_node_type == 'WebPage' and not ctx['is_home']:
        page_node_type = 'ItemPage'
    found_dates = facts.dates(ctx)
    main_entity = None
    graph = []

    # ---- main entity by page kind
    if schema_kind in ('article', 'docs'):
        article_type = 'TechArticle' if schema_kind == 'docs' else ('BlogPosting' if BLOG_URL.search(ctx['url'] or '') or
                                                                    ptype == 'article' else 'Article')
        author_name = facts.author(ctx)
        if author_name and v.normalize(author_name) != v.normalize(name or ''):
            author = {'@type': 'Person', 'name': author_name}
        else:
            author = nodes.ref(id_of['entity'])
        keywords = [k for k in [(ctx.get('keyword_plan') or {}).get('primary')] + list((ctx.get('keyword_plan') or {}).get('secondary') or [])
                    if k][:8]
        graph.append(nodes.article(article_type, id_of['article'], url=url, webpage_id=id_of['webpage'],
                                   title=plan.get('h1') or facts.current_h1(ctx) or title, description=description,
                                   author=author, publisher_id=id_of['entity'], lang=lang,
                                   image_ids=[id_of['image']] if primary else [], published=found_dates['published'],
                                   modified=found_dates['modified'], word_count=ctx.get('main_words'),
                                   keywords=', '.join(keywords) or None))
        main_entity = nodes.ref(id_of['article'])
        if article_type == 'BlogPosting' and not found_dates['published']:
            ask('datePublished for this post (no visible date in the page)')
        if not primary:
            ask(f'A content image (>= 1200 px wide, not a logo) for {article_type}.image')
    elif schema_kind == 'product':
        offering = ((ctx.get('keyword_research') or {}).get('business') or {}).get('offering')
        software = kind != 'ecommerce' and (facts.is_software(ctx, offering) or facts.subscription(found_offers))
        product_type = ('WebApplication' if facts.is_web_app(ctx, offering) else 'SoftwareApplication') if software else 'Product'
        found_rating = facts.rating(ctx)
        if found_rating and found_rating['third_party']:
            notes.append('Visible rating is from a third-party review site; not marked up (Google requires ratings '
                         'collected from your own users)')
            found_rating = None
        offers = nodes.offers_of(found_offers, url=url) if found_offers else None
        graph.append(nodes.product(product_type, id_of['product'], name=product_name(ctx, prior, name), url=url,
                                   description=description, webpage_id=id_of['webpage'], brand_id=id_of['entity'],
                                   image_ids=[id_of['image']] if primary else [], offers=offers, rating=found_rating,
                                   features=facts.features(ctx) if software else None,
                                   category=facts.app_category(ctx, offering) if software else None,
                                   operating_systems=facts.operating_systems(ctx) if software else None,
                                   lang=lang))
        main_entity = nodes.ref(id_of['product'])
        if not found_offers:
            ask(f'Visible price for {product_type}.offers (amount + currency per plan, or state "Free")')
        if software and not found_rating:
            ask(f'First-party user rating shown on the page (average out of 5 + number of ratings) for '
                f'{product_type}.aggregateRating - only if real users rated it')
        if not primary:
            ask(f'Product screenshot or photo (>= 1200 px wide) for {product_type}.image')
    elif schema_kind == 'collection':
        items = [(label, origin + path) for path, label in facts.children(ctx)]
        if items:
            graph.append(nodes.item_list(id_of['itemlist'], name=page_name, items=items))
            main_entity = nodes.ref(id_of['itemlist'])

    featured = facts.showcase(ctx) if schema_kind != 'collection' else None
    if featured:
        heading = featured['name'] or page_name
        products = kind in ('business', 'ecommerce') and re.search(r'(?i)\bproducts?\b', heading or '')
        graph.append(nodes.showcase_list(f'{url}#showcase', name=heading, items=featured['items'],
                                         item_type='Product' if products else 'CreativeWork', creator_id=id_of['entity']))
        main_entity = main_entity or nodes.ref(f'{url}#showcase')

    page_node = nodes.web_page(page_node_type, id_of['webpage'], url=url, name=title, description=description,
                               website_id=id_of['website'], lang=lang,
                               breadcrumb_id=None if ctx['is_home'] else id_of['breadcrumb'],
                               image_id=id_of['image'] if primary else None, about_id=id_of['entity'],
                               main_entity=main_entity, published=found_dates['published'], modified=found_dates['modified'])
    graph = [page_node] + graph

    # ---- site nodes (WebSite is on every page so isPartOf always resolves; the home page is the canonical one)
    graph.append(nodes.website(id_of['website'], origin=origin or url, name=name, publisher_id=id_of['entity'], lang=lang))
    graph.append(entity)
    graph += graph_extra

    if not ctx['is_home']:
        graph.append(nodes.breadcrumbs(id_of['breadcrumb'], crumbs_for(ctx, origin, url, page_name)))

    # ---- FAQ (plan FAQ mirrored verbatim + visible question headings with answers)
    qa = facts.questions(ctx, plan)
    if qa:
        graph.append(nodes.faq_page(id_of['faq'], url=url, questions=qa, website_id=id_of['website'], lang=lang))
    # ---- HowTo (visible ordered steps)
    how = facts.steps(ctx, plan, how_to=bool(re.search(r'(?i)\bhow to\b|step[- ]by[- ]step', f'{title} {page_name}')))
    if how:
        graph.append(nodes.how_to(id_of['howto'], name=how['name'] or page_name, url=url, steps=how['steps'], lang=lang,
                                  image_id=id_of['image'] if primary else None))
    # ---- videos on the page
    for n, row in enumerate(facts.videos(ctx), 1):
        if not row.get('name'):
            row['name'] = f'{page_name} video {n}' if n > 1 else page_name
        graph.append(nodes.video(f'{url}#video-{n}', row, description=description))
        ask(f'Upload date (YYYY-MM-DD) and thumbnail image URL for the video {row.get("embedUrl") or row.get("contentUrl")} '
            '(VideoObject.uploadDate / thumbnailUrl)' if not row.get('thumbnailUrl') else
            f'Upload date (YYYY-MM-DD) for the video {row.get("embedUrl")} (VideoObject.uploadDate)')

    merged = merge_existing(graph, ctx['json_ld'], url)
    data = {'@context': rules.CONTEXT, '@graph': graph}
    core = {id_of['webpage'], id_of['website'], id_of['entity'], id_of['breadcrumb']}
    data, issues, dropped = rules.prune(data, visible=visible, keep=core)
    errors = rules.errors_of(issues)

    # ---- existing markup on the page: replaced, so say what is lost or fixed
    def type_list(row):
        found = row.get('types') if isinstance(row, dict) else None
        found = [found] if isinstance(found, str) else (found if isinstance(found, list) else [])
        return [t for t in found if isinstance(t, str) and t]

    current = [{'types': type_list(j), 'valid': j.get('valid')} for j in ctx['json_ld'] if isinstance(j, dict)] or None
    existing_types = {t for j in ctx['json_ld'] for t in type_list(j)}
    generated = set()
    stack = list(data['@graph'])
    while stack:  # nested types count too (HowToTool inside HowTo, PostalAddress inside Organization)
        item = stack.pop()
        if isinstance(item, list):
            stack += item
        elif isinstance(item, dict):
            generated |= rules.types_of(item)
            stack += [v for v in item.values() if isinstance(v, (dict, list))]
    lost = sorted(existing_types - generated - {'ListItem', 'Question', 'Answer', 'ImageObject', 'Offer', 'HowToStep',
                                                'AggregateRating', 'SearchAction', 'EntryPoint', 'Brand', 'PostalAddress'})
    if lost:
        ask(f'The page already marks up {", ".join(lost)} - confirm these facts so they can be merged into the new '
            'graph (otherwise they are dropped when it replaces the old JSON-LD)')
    invalid = [j.get('error') or 'invalid JSON' for j in ctx['json_ld'] if isinstance(j, dict) and j.get('valid') is False]

    types = [n['@type'] for n in data['@graph']]
    target = add_target(ctx, 'head.json_ld')
    rationale = 'JSON-LD @id graph that mirrors visible content: ' + ', '.join(types)
    if invalid:
        rationale += f'; replaces {len(invalid)} invalid JSON-LD block(s)'
    # Strict: a graph that still has errors after pruning (only possible in the always-kept core nodes, e.g. no
    # production domain for absolute @id/url values) is never proposed; the owner inputs unblock it.
    changes = [] if errors else [ops.make_change(ctx, 'structured_data', 'head.json_ld', current, data, target=target,
                                                 op='insert_node' if not current else 'set_text', confidence=0.85,
                                                 html=render.json_ld(data), rationale=rationale[:500])]
    seen, unique = set(), []
    for item in inputs:
        if item['id'] not in seen:
            seen.add(item['id'])
            item['change'] = changes[0]['id'] if changes else None
            unique.append(item)
    needs = [f'{"/".join(i["type"])}.{i["prop"]}: {i["message"]}' for i in issues if i['level'] == 'needs_input']
    warnings = [f'{"/".join(i["type"])}.{i["prop"]}: {i["message"]}' for i in issues if i['level'] == 'warning']
    return result(data={'json_ld': data, 'types': types, 'schema_kind': schema_kind,
                        'validation': {'errors': [f'{"/".join(i["type"])}.{i["prop"]}: {i["message"]}' for i in errors],
                                       'needs_input': needs, 'warnings': warnings + notes, 'pruned': dropped,
                                       'existing_invalid': invalid, 'existing_types_not_regenerated': lost,
                                       'existing_carried_over': [t for t in merged if t in generated]}},
                  changes=changes, client_inputs=unique)


class StructuredDataCheck(OnpageCheck):
    """Check: strict JSON-LD @id graph from the exploration facts and the page plan (deterministic)."""

    name = 'structured_data'
    skill = 'structured-data'
    uses_model = False
    depends_on = ()

    def run(self, ctx, prior, writer):
        return check_structured_data(ctx, prior, writer)
