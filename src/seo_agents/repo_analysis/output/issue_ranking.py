"""Ranked ISSUE list for summary.json (the planner input).

Each issue points at facts (by shard path) and carries a fix target {kind, where, recipe}; the planner never
needs to open a shard to decide what to assign. `status` is `measured` when the fact was observed on the
served site, `lead` when it is a source-only inference.
"""

from __future__ import annotations

from collections import Counter
import re

SEVERITY_RANK = {'high': 0, 'medium': 1, 'low': 2, 'info': 3}
SPA_PRERENDER = ('Pre-render every route at build time (vite-ssg / Vike / react-snap / @angular/ssr prerender / '
                 'nuxi generate) or move to an SSR/SSG framework, then set title/description/canonical per route '
                 '(react-helmet-async / @unhead / useHead). Runtime-only head tags are invisible to social and most AI crawlers.')
OVERSERVE_LIMIT = 2.0


def issue(id_, severity, title, *, routes=None, all_routes=(), facts=(), fix_kind='code', where=None, recipe=None,
          measured=True, **extra):
    routes = sorted(set(routes or []))
    if routes and len(routes) == len(all_routes) and len(routes) > 1:
        scope = 'site'
    elif len(routes) == 1:
        scope = 'route:' + routes[0]
    elif routes:
        scope = 'routes'
    else:
        scope = 'site'
    record = {'id': 'iss_' + id_, 'severity': severity, 'scope': scope, 'title': title, 'facts': list(facts)[:8],
              'fix': {k: v for k, v in {'kind': fix_kind, 'where': where, 'recipe': recipe}.items() if v},
              'status': 'measured' if measured else 'lead', **extra}
    if scope == 'routes':
        record['routes'] = routes[:25]
        record['route_count'] = len(routes)
    return record


def _where(edit):
    if not edit or not edit.get('file'):
        return None
    return edit['file'] + (f":{edit['line']}" if edit.get('line') else '') + (f"#{edit['pointer']}" if edit.get('pointer') else '')


def build_issues(model, rid, *, hosting=None):
    """model: the in-memory site model; rid: page id -> route id. Returns issues ranked most-severe first."""
    pages = [p for p in model['pages'].values() if p.get('instance_status') != 'template']
    site = model['site']
    served = site.get('served') or {}
    all_routes = [rid[p['id']] for p in pages]
    measured_site = bool(served)
    platform_where = ((hosting or {}).get('config_files') or [{}])[0].get('file') or (hosting or {}).get('value') or 'hosting configuration'
    out = []

    # ---- head shared by every route (SPA shell / layout without per-route metadata)
    titles = Counter((p['head']['title'].get('value') or '').strip() for p in pages)
    shared_shell = [p for p in pages if p.get('head_scope') == 'shared-shell']
    if shared_shell:
        sample = shared_shell[0]['head']['title'].get('edit') or shared_shell[0]['head'].get('add_target')
        out.append(issue('shared_head', 'high', 'Every route serves the same <head> from one shared shell (title, description, '
                         'canonical, OG); per-route metadata cannot be set until routes are pre-rendered or given their own head.',
                         routes=[rid[p['id']] for p in shared_shell], all_routes=all_routes,
                         facts=['metadata.json#/routes', 'routes.json#/routes/*/head_scope'], fix_kind='build',
                         where=_where(sample), recipe=SPA_PRERENDER, measured=measured_site))
    dup_titles = [p for p in pages if titles[(p['head']['title'].get('value') or '').strip()] > 1 and p['head']['title'].get('value')]
    if dup_titles and not shared_shell:
        out.append(issue('duplicate_titles', 'medium', 'Several routes share the same <title>.', routes=[rid[p['id']] for p in dup_titles],
                         all_routes=all_routes, facts=['metadata.json#/routes'], recipe='Give each route a unique, descriptive title.'))

    # ---- client-side only rendering
    csr = [p for p in pages if (p.get('rendering') or {}).get('js_dependent') or
           ((p.get('rendering') or {}).get('raw_html_words') == 0 and (p.get('rendering') or {}).get('rendered_words'))]
    if csr:
        out.append(issue('csr_only', 'high', 'Content exists only after JavaScript runs; the raw HTML is an empty shell.',
                         routes=[rid[p['id']] for p in csr], all_routes=all_routes, facts=['pages/*.json#/rendering'],
                         fix_kind='build', recipe=SPA_PRERENDER))
    elif (site.get('deterministic_signals') or {}).get('rendering_strategy', {}).get('csr_only'):
        out.append(issue('csr_only', 'high', 'Source shows client-side rendering only (no SSR/SSG); crawlers without JS see an empty shell.',
                         facts=['site.json#/deterministic_signals/rendering_strategy'], fix_kind='build', recipe=SPA_PRERENDER,
                         measured=False))

    # ---- served behaviour
    soft = served.get('soft_404') or {}
    if soft.get('soft_404'):
        out.append(issue('soft_404', 'high', 'Unknown URLs return HTTP 200' + (' with the home-page shell (catch-all rewrite)'
                                                                               if soft.get('kind') == 'spa-catch-all' else '') + '.',
                         facts=['site.json#/served/soft_404'], fix_kind='platform', where=platform_where,
                         recipe='Limit the catch-all rewrite to known routes (or emit a real 404.html) so unknown paths return 404; '
                                'in an SPA also render <meta name="robots" content="noindex"> on the not-found view.'))
    elif not soft:
        # Not measured live: report the source-level risk (host catch-all rewrite / client "not found" view) as a lead.
        risks = (site.get('url_manifest') or {}).get('soft_404_risk') or []
        if risks:
            first = risks[0]
            where = first.get('file') + (f":{first['line']}" if first.get('line') else '') if first.get('file') else platform_where
            out.append(issue('soft_404', 'high', 'Unknown URLs likely return HTTP 200 (catch-all rewrite or client-side '
                             '"not found" view): soft 404s and duplicate shells can be indexed.',
                             facts=['site.json#/url_manifest/soft_404_risk'], fix_kind='platform', where=where,
                             recipe='Limit the catch-all rewrite to known routes (or emit a real 404.html) so unknown paths return 404; '
                                    'in an SPA also render <meta name="robots" content="noindex"> on the not-found view.',
                             measured=False))
    for key, label in (('robots_txt', 'robots.txt'), ('sitemap_xml', 'sitemap.xml')):
        state = (served.get(key) or {}).get('value')
        if state in {'spa-shell-fallback', 'missing', 'invalid'}:
            out.append(issue(key + '_' + state.replace('-', '_'), 'high' if state == 'spa-shell-fallback' or key == 'sitemap_xml' else 'medium',
                             f'/{label} is {"served as the HTML app shell (it does not exist)" if state == "spa-shell-fallback" else state}.',
                             facts=[f'site.json#/served/{key}'], fix_kind='code',
                             where='public/' + label + ' (or the framework route that generates it)',
                             recipe=f'Add a static {label} to the public/static directory (or a generator route) and exclude it from the catch-all rewrite.'))
    host = served.get('canonical_host') or {}
    for key, label in (('host_redirect', 'Host'), ('scheme_redirect', 'http to https')):
        redirect = host.get(key)
        if redirect and not redirect.get('permanent'):
            out.append(issue(key + '_temporary', 'medium', f'{label} redirect uses {redirect.get("status")} (temporary); use 301/308.',
                             facts=['site.json#/served/canonical_host'], fix_kind='platform', where=platform_where,
                             recipe='Configure the domain redirect as permanent (308/301) in the hosting dashboard or config.'))
    https = served.get('http_to_https') or {}
    if https.get('status') == 'measured' and https.get('value') is False:
        out.append(issue('no_https_redirect', 'high', 'http:// does not redirect to https://.', facts=['site.json#/served/http_to_https'],
                         fix_kind='platform', where=platform_where))
    slash = served.get('trailing_slash') or {}
    if slash.get('value') == 'duplicate (both 200)':
        out.append(issue('trailing_slash_duplicates', 'medium', f'{slash.get("sample")} and {slash.get("sample")}/ both return 200 (duplicate URLs).',
                         facts=['site.json#/served/trailing_slash'], fix_kind='platform', where=platform_where,
                         recipe='Redirect one form to the other (e.g. vercel.json "trailingSlash": false) and keep canonicals consistent.'))
    if served and (served.get('canonical_host') or {}).get('value', '').startswith('https://') and not (served.get('hsts') or {}).get('value'):
        out.append(issue('no_hsts', 'low', 'No Strict-Transport-Security header on the canonical host.', facts=['site.json#/served/hsts'],
                         fix_kind='platform', where=platform_where))

    # ---- per-route head/content
    def missing(key, severity, title, recipe, fact):
        rows = [p for p in pages if not (p['head'].get(key) or {}).get('value')]
        if rows:
            target = rows[0]['head'].get('add_target')
            out.append(issue('missing_' + key, severity, title, routes=[rid[p['id']] for p in rows], all_routes=all_routes,
                             facts=[fact], where=_where(target), recipe=recipe, measured=any(p['lens'] != 'source' for p in rows)))
    missing('title', 'high', 'Missing <title>.', 'Add a unique 50-60 character title per route.', 'metadata.json#/routes/*/title')
    missing('description', 'high', 'Missing meta description.', 'Add a 140-160 character description per route.', 'metadata.json#/routes/*/description')
    missing('canonical', 'medium', 'No canonical link.', 'Add a self-referencing absolute canonical per route (never one shared value in a shared shell).',
            'metadata.json#/routes/*/canonical')
    missing('lang', 'medium', 'No <html lang>.', 'Set lang on the root <html> element.', 'metadata.json#/routes/*/lang')
    missing('viewport', 'low', 'No viewport meta (verify: some frameworks inject it at runtime).', None, 'metadata.json#/routes/*/viewport')
    no_og = [p for p in pages if not (p['head'].get('og') or {}).get('image')]
    if no_og:
        out.append(issue('missing_og_image', 'low', 'No og:image (social/AI previews have no image).', routes=[rid[p['id']] for p in no_og],
                         all_routes=all_routes, facts=['metadata.json#/routes/*/og'], recipe='Add a 1200x630 absolute-URL og:image per route.',
                         measured=any(p['lens'] != 'source' for p in no_og)))
    h1 = Counter()
    for page in pages:
        h1[page['id']] = sum(h.get('level') == 1 for h in page['headings'])
    no_h1 = [p for p in pages if h1[p['id']] == 0]
    many_h1 = [p for p in pages if h1[p['id']] > 1]
    if no_h1:
        out.append(issue('missing_h1', 'high', 'No <h1> on the page.', routes=[rid[p['id']] for p in no_h1], all_routes=all_routes,
                         facts=['pages/*.json#/h1_count'], where=_where(next((h.get('edit') for h in no_h1[0]['headings']), None)),
                         recipe='Promote the main page heading to <h1> (one per route, in the route component, not the shared shell).',
                         measured=any(p['lens'] != 'source' for p in no_h1)))
    if many_h1:
        out.append(issue('multiple_h1', 'low', 'More than one <h1>.', routes=[rid[p['id']] for p in many_h1], all_routes=all_routes,
                         facts=['pages/*.json#/headings']))
    no_ld = [p for p in pages if not p['head'].get('json_ld')]
    if no_ld:
        out.append(issue('no_structured_data', 'low', 'No JSON-LD structured data.', routes=[rid[p['id']] for p in no_ld], all_routes=all_routes,
                         facts=['structured_data.json#/routes'], recipe='Add JSON-LD that mirrors visible content (WebSite + Person/Organization on home; '
                                                                         'BreadcrumbList / Article where they apply).'))

    # ---- links and landmarks
    landmark = Counter()
    landmark_routes = {}
    for page in pages:
        for row in (page.get('landmarks') or {}).get('issues') or []:
            landmark[row.get('issue')] += 1
            landmark_routes.setdefault(row.get('issue'), []).append(rid[page['id']])
    for name, routes in sorted(landmark_routes.items()):
        if name:
            out.append(issue('landmark_' + re.sub(r'\W+', '_', name), 'low', f'Landmark structure: {name}.', routes=routes,
                             all_routes=all_routes, facts=['pages/*.json#/landmarks'],
                             recipe='Keep exactly one <main>; render <nav>/<header>/<footer>/<aside> outside it.'))
    nav_in_main = [p for p in pages if any(l.get('section') == 'nav' and l.get('in_main') for l in p['links']['out'])]
    if nav_in_main and not any('nav' in str(k) for k in landmark_routes):
        out.append(issue('nav_inside_main', 'low', '<nav> renders inside <main>; landmarks are mis-nested.',
                         routes=[rid[p['id']] for p in nav_in_main], all_routes=all_routes, facts=['links.json#/links/*/in_main'],
                         recipe='Move site navigation and contact rails outside <main>.'))
    js_links = [p for p in pages if any(str(l.get('href') or '').strip() in {'', '#'} or str(l.get('href')).startswith('javascript:')
                                        for l in p['links']['out'])]
    if js_links:
        out.append(issue('js_only_links', 'medium', 'Links without a crawlable href (href="#", javascript: or onClick only).',
                         routes=[rid[p['id']] for p in js_links], all_routes=all_routes, facts=['links.json#/links/*/kind']))
    broken = [u for u in site.get('broken_links') or [] if u]
    if broken:
        out.append(issue('broken_links', 'high', f'{len(broken)} broken link target(s).', facts=['site.json#/broken_links', 'links.json'],
                         recipe='Fix or remove the links (see links.json status).'))
    orphans = site.get('orphan_candidates') or []
    if orphans:
        out.append(issue('orphans', 'medium', f'{len(orphans)} route(s) with no internal links pointing to them.',
                         routes=[rid[p['id']] for p in pages if p['url'] in set(orphans)], all_routes=all_routes,
                         facts=['site.json#/orphan_candidates'], measured=False))

    # ---- images
    no_alt = [p for p in pages if any(i.get('alt') is None for i in p['images'])]
    if no_alt:
        out.append(issue('images_missing_alt', 'medium', 'Images without alt text.', routes=[rid[p['id']] for p in no_alt],
                         all_routes=all_routes, facts=['images.json#/images/*/alt_status'], fix_kind='data',
                         recipe='Add alt where the image is defined (data file field when the <img> is a mapped template).'))
    oversized = [p for p in pages if any((i.get('oversized_ratio') or 0) / 2 > OVERSERVE_LIMIT for i in p['images'])]
    if oversized:
        out.append(issue('oversized_images', 'medium', 'Images served much larger than displayed (>2x even at 2x DPR).',
                         routes=[rid[p['id']] for p in oversized], all_routes=all_routes, facts=['images.json#/images/*/dpr_overserve'],
                         recipe='Resize/convert to WebP/AVIF and serve srcset/sizes.'))
    no_dims = [p for p in pages if any(not i.get('width') and not i.get('height') and i.get('src') for i in p['images'])]
    if no_dims:
        out.append(issue('images_without_dimensions', 'low', 'Images without width/height (layout shift).',
                         routes=[rid[p['id']] for p in no_dims], all_routes=all_routes, facts=['images.json']))

    # ---- performance
    slow = []
    for page in pages:
        lcp = (((page.get('performance') or {}).get('mobile') or {}).get('metrics') or {}).get('lcp_ms')
        if lcp and lcp > 2500:
            slow.append(page)
    if slow:
        out.append(issue('slow_lcp', 'medium', 'Mobile LCP above 2.5 s (lab).', routes=[rid[p['id']] for p in slow], all_routes=all_routes,
                         facts=['performance.json#/routes']))
    failed_perf = [p for p in pages if (site.get('coverage', {}).get('ledger', {}).get(p['id']) or {}).get('performance') == 'failed']
    if failed_perf:
        out.append(issue('performance_not_measured', 'info', 'Lighthouse runs failed; no lab performance data.',
                         routes=[rid[p['id']] for p in failed_perf], all_routes=all_routes, facts=['performance.json'], fix_kind='none'))
    if not measured_site:
        out.append(issue('source_only', 'info', 'Degraded mode: nothing was served, so every fact is a source-only lead. '
                         'Re-run with --site-url or --build to measure.', fix_kind='none', measured=False))
    out.sort(key=lambda i: (SEVERITY_RANK[i['severity']], -(i.get('route_count') or (len(all_routes) if i['scope'] == 'site' else 1))))
    return out


class IssueRanker:
    """Tool: ranked issues[] (with fact refs and fix target) for summary.json."""

    name = 'issue_ranking'

    def rank(self, model, rid, *, hosting=None):
        return build_issues(model, rid, hosting=hosting)
