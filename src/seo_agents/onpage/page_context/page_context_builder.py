"""Compact, JSON-only working context for one page (what every checklist function reads)."""

from __future__ import annotations

from datetime import date
import hashlib
import re
from urllib.parse import urlsplit

from seo_agents.onpage.reports import exploration_reports as m
from seo_agents.onpage.changes import head_recipes as recipes

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
            if len(parts) > 1:
                names.append(parts[0] if page.get('url') == '/' else parts[-1])
            elif parts and len(parts[0].split()) <= 3:
                names.append(parts[0])  # a short separator-less title may be the brand; a full sentence never is
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
                     'data_driven': data_driven, 'visible': h.get('visible') if isinstance(h.get('visible'), bool) else None,
                     'section': h.get('section')})
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


LOCAL_HOST = re.compile(r'(?i)^(?:localhost|127(?:\.\d{1,3}){3}|0\.0\.0\.0|\[?::1\]?|.+\.local|.+\.localhost)$')


def is_local_origin(origin):
    """localhost / 127.x / 0.0.0.0 / ::1 / *.local: served probes describe a dev server, not production."""
    if not isinstance(origin, str) or not origin:
        return False
    try:
        host = urlsplit(origin if '://' in origin else 'http://' + origin).hostname or ''
    except ValueError:
        return False
    return bool(LOCAL_HOST.match(host))


# Platform preview/staging hosts: served probes and URLs there describe a deployment preview, not production.
PREVIEW_HOST = re.compile(r'(?i)\.(?:vercel\.app|netlify\.app|pages\.dev|web\.app|firebaseapp\.com|onrender\.com|herokuapp\.com|'
                          r'github\.io|gitlab\.io|surge\.sh|fly\.dev|railway\.app|azurestaticapps\.net|amplifyapp\.com|ngrok(?:-free)?\.app|'
                          r'ngrok\.io|trycloudflare\.com|loca\.lt)$')


def is_preview_origin(origin):
    """*.vercel.app, *.netlify.app, *.pages.dev, ... (deployment previews / platform subdomains)."""
    if not isinstance(origin, str) or not origin:
        return False
    try:
        host = urlsplit(origin if '://' in origin else 'https://' + origin).hostname or ''
    except ValueError:
        return False
    return bool(PREVIEW_HOST.search(host))


def is_nonproduction_origin(origin):
    return is_local_origin(origin) or is_preview_origin(origin)


def _origin_of(host):
    """'example.com' -> 'https://example.com'; local dev hosts without a scheme ('localhost:5173') get http://."""
    if not isinstance(host, str) or not host.strip():
        return None
    host = host.strip()
    if not host.startswith('http'):
        bare = host.lstrip('/')
        host = ('http://' if is_local_origin(bare) else 'https://') + bare
    try:
        parts = urlsplit(host)
    except ValueError:
        return None
    return f'{parts.scheme}://{parts.netloc}' if parts.netloc else None


def canonical_origin(reports, site, origin, site_url=None):
    """Canonical origin. Precedence: a measured, non-local/non-preview exploration canonical_host (the final origin after any
    apex -> www redirect) > explicit --site-url > other candidates (served/CLI origin) > local dev hosts."""
    measured = []
    host = site.get('canonical_host')
    if isinstance(host, dict):
        host = host.get('origin') or host.get('url') or host.get('value') or host.get('host')
    measured.append(_origin_of(host))
    served_host = (site.get('served') or {}).get('canonical_host') if isinstance(site.get('served'), dict) else None
    if isinstance(served_host, dict):
        measured.append(_origin_of(served_host.get('value') or served_host.get('host')))
    measured = [c for c in measured if c]
    found = next((c for c in measured if not is_nonproduction_origin(c)), None)
    if found:
        return found
    explicit = _origin_of(site_url)
    if explicit:
        return explicit
    candidates = measured + [origin.rstrip('/') if isinstance(origin, str) and origin else None]
    candidates = [c for c in candidates if c]
    return next((c for c in candidates if not is_nonproduction_origin(c)), candidates[0] if candidates else None)


def _host_of(url):
    try:
        return (urlsplit(url).hostname or '').lower().removeprefix('www.')
    except ValueError:
        return ''


def external_link_rows(links, origin):
    """Off-site links: absolute (or protocol-relative) hrefs whose host differs from the canonical origin's host."""
    own = _host_of(origin) if origin else ''
    out = []
    for l in links:
        href = l.get('href')
        if not isinstance(href, str):
            continue
        absolute = href.lower().startswith(('http://', 'https://', '//'))
        if not absolute or (l.get('kind') not in (None, 'absolute') and l.get('kind') != 'external'):
            continue
        host = _host_of('https:' + href if href.startswith('//') else href.strip())
        if not host or (own and host == own):
            continue
        out.append({'id': l['id'], 'href': href, 'anchor': l['accessible_name'], 'status': l.get('status')})
    return out


def _path_of(url):
    if not isinstance(url, str) or not url:
        return None
    path = re.sub(r'^(?:https?:)?//[^/]+', '', url.strip()).split('#')[0]
    path = path or '/'
    return path if path == '/' else path.rstrip('/') or '/'


def page_noindex(page):
    robots = m.clean(m.value((page.get('head') or {}).get('robots'))) or ''
    headers = ((page.get('http') or {}).get('headers') or {}) if isinstance(page.get('http'), dict) else {}
    x_robots = headers.get('x-robots-tag') if isinstance(headers, dict) else None
    tokens = set(re.split(r'[\s,;:]+', (robots + ' ' + (x_robots if isinstance(x_robots, str) else '')).lower()))
    return bool(tokens & {'noindex', 'none'})


def page_redirect_target(page):
    http = page.get('http') if isinstance(page.get('http'), dict) else {}
    own = _path_of(page.get('url'))
    final = _path_of(http.get('final_url'))
    if final:  # host-only redirects (apex -> www) keep the path: not a page redirect
        return final if own and final.split('?')[0] != own.split('?')[0] else None
    chain = [r for r in (http.get('redirect_chain') or []) if isinstance(r, dict)]
    target = (chain[-1].get('location') or chain[-1].get('target')) if chain else None
    if isinstance(target, str) and target:
        path = _path_of(target) if target.startswith(('http', '/')) else target
        return path if path and path != own else None
    return None


def site_pages(reports):
    """{path: {http_status, noindex, redirects_to, template}} for every page record in the model."""
    out = {}
    for page in (reports.pages or {}).values():
        if not isinstance(page, dict):
            continue
        url = page.get('url') or page.get('route')
        if not isinstance(url, str):
            continue
        status = (page.get('http') or {}).get('http_status') if isinstance(page.get('http'), dict) else None
        discovered = page.get('discovered_by') if isinstance(page.get('discovered_by'), list) else []
        out[url] = {'http_status': status if isinstance(status, int) else None, 'noindex': page_noindex(page),
                    'redirects_to': page_redirect_target(page), 'template': page.get('instance_status') == 'template',
                    'kind': page.get('kind') or 'page', 'in_sitemap': 'sitemap:live' in discovered}
    return out


def link_graph(page):
    links = page.get('links') if isinstance(page.get('links'), dict) else {}
    in_count, depth, orphan = links.get('in_count'), links.get('click_depth'), links.get('orphan_candidate')
    return {'in_count': in_count if isinstance(in_count, int) else None, 'click_depth': depth if isinstance(depth, int) else None,
            'orphan_candidate': orphan if isinstance(orphan, bool) else None}


def _meta_row(row):
    """Raw <meta> row; `http_equiv` (exploration key) is also exposed as `http-equiv` (the attribute name)."""
    if row.get('http_equiv') is not None and row.get('http-equiv') is None:
        return {**row, 'http-equiv': row['http_equiv']}
    if row.get('http-equiv') is not None and row.get('http_equiv') is None:
        return {**row, 'http_equiv': row['http-equiv']}
    return row


def _dict_rows(rows):
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _keyword_list(items, limit=12):
    out = []
    for item in items or []:
        text = re.sub(r'\s+', ' ', item).strip() if isinstance(item, str) else ''
        if text and text.lower() not in {o.lower() for o in out}:
            out.append(text)
    return out[:limit]


def _site_path(url):
    if not isinstance(url, str) or not url:
        return None
    path = re.sub(r'^https?://[^/]+', '', url.strip()) or '/'
    return path if path == '/' else path.rstrip('/') or '/'


def normalize_keyword_plan(plan):
    """PageKeywordPlan dict (keyword_research/contracts.py) -> the shape the checks read. None stays None."""
    if not isinstance(plan, dict):
        return None
    primary = plan.get('primary')
    primary = re.sub(r'\s+', ' ', primary).strip().lower() if isinstance(primary, str) and primary.strip() else None
    cluster = plan.get('cluster') if isinstance(plan.get('cluster'), dict) else {}
    return {
        'page_id': plan.get('page_id'), 'url': plan.get('url'), 'intent': plan.get('intent'),
        'journey_stage': plan.get('journey_stage'), 'primary': primary,
        'secondary': [k.lower() for k in _keyword_list(plan.get('secondary')) if k.lower() != primary],
        'questions': _keyword_list(plan.get('questions'), 8),
        'conversational': _keyword_list(plan.get('conversational'), 6),
        'short_tail': [k.lower() for k in _keyword_list(plan.get('short_tail'))],
        'cluster': {'role': cluster.get('role'), 'pillar_url': _site_path(cluster.get('pillar_url')),
                    'related_urls': [u for u in (_site_path(x) for x in cluster.get('related_urls') or []) if u]},
        'competitor_urls': _keyword_list(plan.get('competitor_urls'), 10),
        'content_gaps': _keyword_list(plan.get('content_gaps'), 10),
        'iterations': plan.get('iterations'), 'status': plan.get('status') or 'complete',
    }


def keyword_research_meta(reports):
    kmap = reports.keyword_map or {}
    if not kmap:
        return None
    return {'schema': kmap.get('schema'), 'market': kmap.get('market'), 'generated_at': kmap.get('generated_at'),
            'source': 'seo_reports/keywords/keyword_map.json',
            'business': {k: (kmap.get('business') or {}).get(k) for k in ('brand', 'site_type', 'offering')}
            if isinstance(kmap.get('business'), dict) else None}


def build_context(reports, page_id, *, origin=None, run_date=None, site_url=None):
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
    origin = canonical_origin(reports, site, origin, site_url)
    brands = brand_candidates(reports)
    visible = m.visible_strings(page)
    site_strings = [u['title'] for u in urls if u.get('title')] + brands
    other_titles = [m.clean(row.get('title')) for row in reports.page_rows() if row['id'] != page_id and row.get('title')]
    other_descriptions = [d for pid, p in reports.pages.items() if pid != page_id and isinstance(p, dict)
                          for d in [m.page_description(p)] if d]
    external = external_link_rows(links, origin)
    discovered = page.get('discovered_by') or route.get('discovered_by') or []
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
        'other_descriptions': other_descriptions,
        'link_graph': link_graph(page),
        'discovered_by': [d for d in discovered if isinstance(d, str)] if isinstance(discovered, list) else [],
        # True for local dev hosts AND platform preview hosts (*.vercel.app, ...): not the production origin, so checks
        # must not write it into canonicals/sitemaps nor judge production headers from it.
        'origin_is_local': is_nonproduction_origin(origin),
        'origin_is_preview': is_preview_origin(origin),
        'site_pages': site_pages(reports),
        'meta_tags': [_meta_row(r) for r in _dict_rows(head.get('meta_tags'))],
        'head_links': _dict_rows(head.get('head_links')),
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
                                          'trailing_slash', 'url_manifest', 'hreflang', 'duplicates', 'orphan_candidates',
                                          'click_depth', 'crawl_errors', 'redirected_links', 'blocked_links', 'i18n',
                                          'link_health') if site.get(k) is not None},
        'site_served': site.get('served') or {},
        'site_gaps': {k: (v or {}).get('gaps') for k, v in (site.get('deterministic_signals') or {}).items() if isinstance(v, dict)},
        'broken_links': site.get('broken_links') or [],
        'corpus': ' \n '.join(visible + site_strings),
        # Researched keywords (keyword_research stage) for this page, or None (on-page then uses model hypotheses).
        'keyword_plan': normalize_keyword_plan(m.keyword_plan(reports, page_id, page)),
        'keyword_research': keyword_research_meta(reports),
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


class PageContextBuilder:
    """Tool: one page context (facts, copy, links, images, brand, site/page type) built from the exploration JSON only."""

    def build(self, reports, page_id, *, origin=None, run_date=None, site_url=None):
        return build_context(reports, page_id, origin=origin, run_date=run_date, site_url=site_url)
