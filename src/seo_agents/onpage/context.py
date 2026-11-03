"""Compact, JSON-only working context for one page (what every checklist function reads)."""

from __future__ import annotations

from datetime import date
import hashlib
import re
from urllib.parse import urlsplit

from seo_agents.onpage import model as m
from seo_agents.onpage import recipes

TITLE_SPLIT = re.compile(r'\s+[|—–-]\s+|\s*\|\s*')
DATA_KINDS = {'json', 'yaml', 'toml', 'markdown', 'i18n', 'cms'}
SOCIAL = re.compile(r'(?i)^https?://(?:www\.)?(?:github\.com|gitlab\.com|linkedin\.com|x\.com|twitter\.com|mastodon\.[a-z.]+|'
                    r'bsky\.app|instagram\.com|facebook\.com|youtube\.com|medium\.com|dev\.to|dribbble\.com|behance\.net|'
                    r'stackoverflow\.com|threads\.net|tiktok\.com|crunchbase\.com|wikipedia\.org|wikidata\.org)/[^?#]+$')
NOT_PROFILE = re.compile(r'(?i)/(?:intent|share|sharer|blob|tree|issues|pull|status|watch|hashtag|search)/|/sharing/')
LOGOISH = re.compile(r'(?i)(?:logo|favicon|icon|sprite|badge|avatar-placeholder)')


def brand_candidates(reports):
    names = []
    for page in reports.pages.values():
        og = m.head(page, 'og') or {}
        if m.clean(m.value(og.get('site_name'))):
            names.append(m.clean(m.value(og.get('site_name'))))
        title = m.page_title(page)
        if title:
            parts = [p.strip() for p in TITLE_SPLIT.split(title) if p.strip()]
            if parts:
                names.append(parts[0] if page.get('url') == '/' else parts[-1])
    entity = (reports.exploration or {}).get('entity') or {}
    if isinstance(entity, dict) and isinstance(entity.get('name'), str):
        names.insert(0, entity['name'])
    seen, out = set(), []
    for name in names:
        if name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out[:5]


def profile_urls(links):
    """sameAs candidates: profile URLs only (no tweet intents, README blobs, share links; audit §7 brand)."""
    out = []
    for link in links:
        href = link.get('href') or ''
        if SOCIAL.match(href) and not NOT_PROFILE.search(href) and href not in out:
            out.append(href.rstrip('/'))
    return out


def link_records(page):
    """Links with stable ids (href + anchor + DOM order) so rewrites never hit "the first link with that href"."""
    out, seen = [], {}
    for position, link in enumerate(m.links_out(page)):
        href = link.get('href')
        if not isinstance(href, str):
            continue
        anchor = m.clean(link.get('anchor')) or ''
        nth = seen.get((href, anchor), 0)
        seen[(href, anchor)] = nth + 1
        lid = 'lnk_' + hashlib.sha1(f'{href}|{anchor}|{nth}'.encode()).hexdigest()[:8]
        name = anchor or m.clean(link.get('aria_label')) or m.clean(link.get('img_alt')) or m.clean(link.get('title'))
        out.append({**link, 'id': lid, 'position': position, 'anchor': anchor, 'accessible_name': name,
                    'renders': link.get('visible') is not False and link.get('live') != ''})
    return out


def heading_records(page):
    rows = []
    for h in m.headings(page):
        edit = h.get('edit') or {}
        data_driven = edit.get('kind') in DATA_KINDS or bool(edit.get('template') or edit.get('composed_from')) \
            or edit.get('kind') == 'computed'
        rows.append({'level': h.get('level'), 'text': m.clean(h.get('text')), 'edit': h.get('edit'), 'method': h.get('method'),
                     'confidence': h.get('confidence'), 'in_main': h.get('section') not in ('header', 'nav', 'footer', 'aside'),
                     'data_driven': data_driven})
    return rows


def sections_with_flags(page):
    sections = m.current_sections(page)
    for s in sections:
        kinds = [(b.get('edit') or {}).get('kind') for b in s['blocks']]
        data = sum(1 for k in kinds if k in DATA_KINDS)
        s['data_driven'] = bool(kinds) and data >= max(1, len(kinds) / 2)
    return sections


def site_type_guess(reports, brand):
    """personal | business | ... from deterministic hints (entity shard, Person JSON-LD, person-like brand)."""
    entity = (reports.exploration or {}).get('entity') or {}
    if isinstance(entity, dict) and entity.get('type') in ('Person', 'person'):
        return 'personal'
    if isinstance(entity, dict) and entity.get('type') in ('Organization', 'LocalBusiness', 'Corporation'):
        return 'business'
    for page in reports.pages.values():
        for row in page.get('structured_data') or []:
            if isinstance(row, dict) and 'Person' in str(row.get('types') or row.get('@type')):
                return 'personal'
    titles = ' '.join(m.page_title(p) or '' for p in reports.pages.values()).lower()
    if re.search(r'\b(portfolio|resume|cv|software engineer|developer|designer|photographer|writer)\b', titles) and brand \
            and len(brand.split()) in (2, 3) and all(w[:1].isupper() for w in brand.split()):
        return 'personal'
    if re.search(r'\b(shop|store|cart|buy)\b', titles):
        return 'ecommerce'
    return 'other'


def canonical_origin(reports, site, origin):
    """Canonical host: exploration canonical_host > the final URL after redirects > the CLI/served origin."""
    host = site.get('canonical_host')
    if isinstance(host, dict):
        host = host.get('origin') or host.get('url') or host.get('host')
    if isinstance(host, str) and host:
        if not host.startswith('http'):
            host = 'https://' + host
        parts = urlsplit(host)
        return f'{parts.scheme}://{parts.netloc}'
    return origin


def build_context(reports, page_id, *, origin=None, run_date=None):
    page = reports.pages[page_id]
    run_date = run_date or date.today().isoformat()
    url = page.get('url') or page.get('route') or '/'
    head = page.get('head') or {}
    site = m.exploration_site(reports)
    route = m.route_record(reports, page)
    page_for_scope = {**page, **({'head_scope': route['head_scope']} if route.get('head_scope') else {})}
    fingerprint = site.get('framework') if isinstance(site.get('framework'), dict) else \
        ((reports.index.get('repo') or {}).get('framework') or {})
    links = link_records(page)
    urls = m.site_urls(reports)
    origin = canonical_origin(reports, site, origin)
    brands = brand_candidates(reports)
    visible = m.visible_strings(page)
    site_strings = [u['title'] for u in urls if u.get('title')] + brands
    other_titles = [m.clean(row.get('title')) for row in reports.page_rows() if row['id'] != page_id and row.get('title')]
    external = [{'id': l['id'], 'href': l.get('href'), 'anchor': l['accessible_name'], 'status': l.get('status')}
                for l in links if isinstance(l.get('href'), str) and l['href'].startswith('http')]
    context_pages = [p for p in reports.repo_context.get('pages') or [] if isinstance(p, dict) and p.get('route') == url]
    files = [e.get('path') for e in reports.repo_context.get('file_inventory') or [] if isinstance(e, dict) and e.get('path')]
    files += [f for f in (((reports.exploration or {}).get('manifest') or {}).get('files') or []) if isinstance(f, str)]
    recipe = recipes.head_recipe(fingerprint)
    framework = (fingerprint or {}).get('primary') or (fingerprint or {}).get('name')
    host = recipes.hosting(site, files)
    scope = recipes.head_scope(fingerprint, page_for_scope, site)
    return {
        'page_id': page_id,
        'url': url,
        'origin': origin,
        'absolute_url': (origin + url) if origin else None,
        'template': page.get('template'),
        'run_date': run_date,
        'lang': m.clean(m.value(head.get('lang'))) or 'en',
        'is_home': url == '/',
        'framework': framework,
        'fingerprint': {k: (fingerprint or {}).get(k) for k in ('primary', 'family', 'version', 'router', 'renderer')},
        'recipe': recipe,
        'head_scope': scope,
        'framework_hint': f"{framework or 'unknown stack'} ({scope}): {recipe.get('mechanism', '')}"[:300],
        'hosting': host,
        'brand_candidates': brands,
        'site_type': site_type_guess(reports, brands[0] if brands else None),
        'title': m.page_title(page),
        'description': m.page_description(page),
        'headings': m.heading_texts(page),
        'heading_records': heading_records(page),
        'sections': sections_with_flags(page),
        'copy': m.copy_text(page),
        'main_words': (page.get('content') or {}).get('main_words'),
        'images': m.images(page),
        'assets': m.site_assets(reports, page),
        'links': links,
        'external_links': external,
        'profiles': profile_urls(links),
        'site_urls': urls,
        'other_titles': other_titles,
        'head': head,
        'add_target': head.get('add_target'),
        'json_ld': head.get('json_ld') or [],
        'structured_data': page.get('structured_data') or [],
        'eeat_observed': page.get('eeat') or [],
        'eeat_assessment': page.get('eeat_assessment'),
        'exploration_keywords': {**(page.get('keywords') or {}),
                                 'site_candidates': (reports.repo_context.get('summary') or {}).get('primary_keyword_candidates') or []},
        'rendering': {**(page.get('rendering') or {}), **({'mode': route['render']} if route.get('render') else {})},
        'raw_source': context_pages[0] if context_pages else {},
        'http': page.get('http') or {},
        'mobile_parity': page.get('mobile_parity'),
        'performance': page.get('performance'),
        'site': {k: site.get(k) for k in ('served', 'soft_404', 'robots', 'sitemap', 'canonical_host', 'hosting', 'redirects',
                                          'trailing_slash', 'url_manifest') if site.get(k) is not None},
        'site_served': site.get('served') or {},
        'site_gaps': {k: (v or {}).get('gaps') for k, v in (site.get('deterministic_signals') or {}).items() if isinstance(v, dict)},
        'broken_links': site.get('broken_links') or [],
        'corpus': ' \n '.join(visible + site_strings),
    }


def page_brief(ctx, *, copy_words=900):
    """The page as the writer sees it (no locators)."""
    copy, total = [], 0
    for text in ctx['copy']:
        total += len(text.split())
        if total > copy_words:
            break
        copy.append(text)
    return {
        'url': ctx['url'], 'lang': ctx['lang'], 'title': ctx['title'], 'description': ctx['description'],
        'headings': ctx['headings'], 'copy': copy,
        'images': [{'src': i.get('src'), 'alt': i.get('alt')} for i in ctx['images']],
        'links': [{'href': l.get('href'), 'anchor': l['accessible_name']} for l in ctx['links'] if l.get('href') and l['renders']],
    }


def facts(ctx):
    """Verifiable facts with ids (so the page plan can assign each fact to exactly one slot)."""
    rows = [t for t in [ctx['title'], ctx['description']] if t] + list(ctx['copy'])
    rows += [f"{l['anchor']} -> {l['href']}" for l in ctx['external_links'] if l.get('anchor')]
    rows += [f"contact/resource link: {l['href']}" for l in ctx['links']
             if isinstance(l.get('href'), str) and (l['href'].startswith('mailto:') or l['href'].lower().endswith('.pdf'))]
    rows += [f"image {i.get('src')}: {i.get('alt')}" for i in ctx['images'] if i.get('src') and i.get('alt')]
    seen, out = set(), []
    for row in rows:
        if row not in seen:
            seen.add(row)
            out.append({'id': 'f' + str(len(out) + 1), 'text': row})
    return out
