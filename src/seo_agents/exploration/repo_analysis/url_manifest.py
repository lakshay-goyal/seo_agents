"""Stage C: one URL manifest = adapter routes ∪ static instances ∪ sitemaps ∪ build output ∪ crawl.

Every URL keeps every method that found it (`discovered_by`), so the execution agent
can tell a route that exists only in a sitemap from one that exists only in code.
URLs are normalised (origin stripped for the site's own origin, basePath kept,
trailing slash / `index.html` / `.html` folded) before the union.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from seo_agents.exploration.repo_analysis.tools.adapters import adapter_routes, content_instances, static_params
from seo_agents.exploration.repo_analysis.tools.safe_xml import safe_xml_fromstring as safe_fromstring

PAGE_KINDS = {'page', 'content-page', 'archive', 'alternate-template', 'template', 'server-route'}
PLACEHOLDER = re.compile(r'\[\[?\.\.\.[^\]/]+\]?\]|\[[^\]/]+\]|:[A-Za-z_]\w*\??|\{[^}/]+\}|<(?:[^:>/]+:)?[^>/]+>|\*')


def page_id(url):
    return 'p_' + hashlib.sha1(url.encode()).hexdigest()[:10]


def normalize_path(value, site_url=None, own_hosts=()):
    """Comparable path for a URL or route; None for other origins / non-HTTP.

    `own_hosts` are the site's production hosts (sitemaps, site config, CNAME): absolute
    links to them are the same site even when a local server is being measured."""
    if value is None:
        return None
    value = value.strip()
    parsed = urlsplit(value)
    if parsed.scheme and parsed.scheme not in {'http', 'https'}:
        return None
    if parsed.netloc:
        host = (parsed.hostname or '').lower()
        if site_url and (urlsplit(site_url).hostname or '').lower() != host and host not in set(own_hosts):
            return None
    path = parsed.path
    if not path.startswith('/'):
        path = '/' + path
    path = re.sub(r'/index\.html?$', '/', path)
    path = re.sub(r'\.html?$', '', path)
    path = re.sub(r'/{2,}', '/', path)
    return path.rstrip('/') or '/'


HOST_HINTS = [
    (r'(?:^|/)CNAME$', r'^\s*([\w.\-]+\.[a-z]{2,})\s*$'),
    (r'(?:^|/)astro\.config\.[cm]?[jt]s$', r"""\bsite\s*:\s*['"](https?://[^'"]+)"""),
    (r'(?:^|/)(?:hugo|config)\.(?:toml|ya?ml)$', r"""(?im)^\s*baseURL\s*[=:]\s*["']?(https?://[^"'\s]+)"""),
    (r'(?:^|/)_config\.ya?ml$', r"""(?im)^url\s*:\s*["']?(https?://[^"'\s]+)"""),
    (r'(?:^|/)package\.json$', r'"homepage"\s*:\s*"(https?://[^"]+)"'),
    (r'(?:^|/)(?:next-sitemap\.config\.[cm]?js|nuxt\.config\.[jt]s|svelte\.config\.js|gatsby-config\.[jt]s|docusaurus\.config\.[jt]s)$',
     r"""\b(?:siteUrl|url|baseURL|site)\s*:\s*['"](https?://[^'"]+)"""),
    # Hidden env files are retained by the snapshot with secrets redacted (only URL-like public values kept).
    (r'(?:^|/)(?:\.env(?:\.production|\.example|\.sample)?|vercel\.json|netlify\.toml|wrangler\.toml)$',
     r"""(?:\w*_)?(?:SITE_URL|PUBLIC_URL|BASE_URL|APP_URL|SITE_ORIGIN|URL)\s*=\s*["']?(https?://[^"'\s]+)"""),
]


def site_hosts(snapshot, site_url=None):
    """The site's own hostnames: site_url, sitemap <loc> hosts, canonical/site config, CNAME."""
    hosts = set()
    if site_url:
        hosts.add((urlsplit(site_url).hostname or '').lower())
    files = snapshot['files']
    for path, text in list(files.items()) + list((snapshot.get('env_hints') or {}).items()):
        for path_pattern, value_pattern in HOST_HINTS:
            if re.search(path_pattern, path):
                for match in re.finditer(value_pattern, text, re.M):
                    value = match.group(1)
                    host = ((urlsplit(value).hostname or '') if '://' in value else value).lower()
                    if host and '.' in host:
                        hosts.add(host)
    for url, _ in sitemap_urls_from_source(files):
        host = (urlsplit(url).hostname or '').lower()
        if host:
            hosts.add(host)
    hosts |= {'www.' + h for h in list(hosts) if not h.startswith('www.')} | {h[4:] for h in list(hosts) if h.startswith('www.')}
    hosts.discard('')
    return hosts


SEGMENT_RANK = [(re.compile(r'^\[\[\.\.\.'), 4), (re.compile(r'^\[\.\.\.|^\*$'), 3), (PLACEHOLDER, 1)]


def specificity(pattern):
    """Sort key: static segments beat dynamic ones, which beat catch-alls (Next/SvelteKit order)."""
    ranks = []
    for segment in (pattern.strip('/').split('/') if pattern.strip('/') else []):
        ranks.append(next((value for regex, value in SEGMENT_RANK if regex.search(segment)), 0))
    return ranks + [5] * (12 - len(ranks))


def pattern_regex(pattern, optional_rest=False):
    """Route pattern -> regex. `[[...x]]` (and `[...x]` when the framework allows empty rest
    params, e.g. Astro/SvelteKit) also match the parent path itself."""
    parts = []
    pieces = [p for p in re.split(r'(' + PLACEHOLDER.pattern + r')', pattern.rstrip('/') or '/') if p]
    for index, piece in enumerate(pieces):
        if PLACEHOLDER.fullmatch(piece):
            rest = piece.startswith('[...') or piece.startswith('[[...') or piece == '*'
            optional = rest and (piece.startswith('[[...') or optional_rest)
            if optional and parts and parts[-1].endswith('/'):
                parts[-1] = parts[-1][:-1]
                parts.append('(?:/.*)?')
            else:
                parts.append('.+' if rest else '[^/]+')
        else:
            parts.append(re.escape(piece).replace('\\/', '/'))
    return re.compile('^' + (''.join(parts) or '/') + '/?$')


def substitute(pattern, value):
    return PLACEHOLDER.sub(lambda _: str(value), pattern, count=1)


def sitemap_urls_from_source(files):
    urls = []
    for path, text in sorted(files.items()):
        if PurePosixPath(path).suffix.lower() == '.xml' and 'sitemap' in PurePosixPath(path).name.lower():
            try:
                root = safe_fromstring(text)
            except Exception:  # noqa: BLE001 - malformed or unsafe XML is skipped and reported elsewhere
                continue
            for element in root.iter():
                if element.tag.rsplit('}', 1)[-1] == 'loc' and element.text:
                    urls.append((element.text.strip(), 'sitemap:' + path))
        elif PurePosixPath(path).name == 'robots.txt':
            continue
    return urls


def build_output_urls(output_dir):
    if not output_dir:
        return []
    root = Path(output_dir)
    urls = []
    for path in sorted(root.rglob('*.html'))[:5000]:
        relative = path.relative_to(root).as_posix()
        if relative.startswith(('_next/', 'assets/', '_astro/')) or PurePosixPath(relative).name in {'404.html', '500.html', '200.html'}:
            continue
        urls.append(('/' + relative, 'build-output'))
    return urls


CATCH_ALL_DESTINATION = re.compile(r'^/?(?:index\.html?|200\.html)?$')


def catch_all_rewrites(files):
    """Host rewrites that answer every unknown URL with the app shell (HTTP 200): soft-404 risk, and
    robots.txt/sitemap.xml served as HTML when those files don't exist (PR #8: vercel.json `/(.*) -> /index.html`)."""
    import json
    found = []
    for path, text in files.items():
        name = PurePosixPath(path).name
        if name == 'vercel.json':
            try:
                document = json.loads(text)
            except ValueError:
                continue
            for rule in (document.get('rewrites') or []) + (document.get('routes') or []) if isinstance(document, dict) else []:
                if not isinstance(rule, dict):
                    continue
                source = str(rule.get('source') or rule.get('src') or '')
                dest = str(rule.get('destination') or rule.get('dest') or '')
                if re.fullmatch(r'/?(?:\(\.\*\)|\.\*|:path\*|\(\.\+\)|:\w+\*|\*)', source) and CATCH_ALL_DESTINATION.match(dest):
                    found.append({'kind': 'host-rewrite', 'file': path, 'rule': f'{source} -> {dest}', 'host': 'vercel'})
        elif name == '_redirects' or name == 'netlify.toml':
            for match in re.finditer(r'(?m)^\s*/\*\s+/index\.html\s+200\b|from\s*=\s*["\']/\*["\'][^\[]*?to\s*=\s*["\']/index\.html["\'][^\[]*?status\s*=\s*200', text, re.S):
                found.append({'kind': 'host-rewrite', 'file': path, 'rule': ' '.join(match.group(0).split())[:120], 'host': 'netlify'})
        elif name == '.htaccess' and re.search(r'(?m)^\s*RewriteRule\s+\S+\s+/?index\.html\s*(?:\[L\])?\s*$', text):
            found.append({'kind': 'host-rewrite', 'file': path, 'rule': 'RewriteRule . /index.html', 'host': 'apache'})
        elif re.search(r'(?:^|/)nginx[^/]*\.conf$|(?:^|/)conf\.d/', path) and re.search(r'try_files\s+\$uri[^;]*\s/index\.html\s*;', text):
            found.append({'kind': 'host-rewrite', 'file': path, 'rule': 'try_files ... /index.html', 'host': 'nginx'})
        elif name in {'firebase.json', 'static.json'} and re.search(r'"source"\s*:\s*"\*\*"[^}]*"destination"\s*:\s*"/index\.html"', text):
            found.append({'kind': 'host-rewrite', 'file': path, 'rule': '** -> /index.html', 'host': 'firebase' if name == 'firebase.json' else 'static'})
    return found


def merge_suggested_instances(manifest_pages, suggestions, *, site_url=None, own_hosts=()):
    """Feed gap-worker `instances_suggested` back into the manifest (the doc's missing loop edge).

    suggestions: iterable of URL strings or {url|path, route?, template?, source?}. Each URL that matches a
    dynamic route pattern (or an explicit route) becomes a concrete row with discovered_by ['gap-suggested'];
    already-known URLs only gain the provenance. Returns (pages, added_rows). Rows are new dicts; inputs are
    not mutated except for appended provenance on existing rows."""
    pages = list(manifest_pages)
    by_url = {page['url']: page for page in pages}
    templates = [(pattern_regex(page['route']), page) for page in pages if page.get('instance_status') == 'template']
    templates.sort(key=lambda item: specificity(item[1]['route']))
    added = []
    for suggestion in suggestions or []:
        item = {'url': suggestion} if isinstance(suggestion, str) else dict(suggestion or {})
        raw = item.get('url') or item.get('path')
        path = normalize_path(raw, site_url, own_hosts) if raw else None
        if not path or PLACEHOLDER.search(path):
            continue
        if path in by_url:
            if 'gap-suggested' not in by_url[path]['discovered_by']:
                by_url[path]['discovered_by'].append('gap-suggested')
            continue
        parent = None
        if item.get('route'):
            parent = next((page for _, page in templates if page['route'] == item['route']), None)
        if parent is None:
            parent = next((page for regex, page in templates if regex.match(path)), None)
        if parent is None:
            continue  # a suggestion must instantiate a known template; free-floating URLs are left to the crawl
        new = {'id': page_id(path), 'url': path, 'route': parent['route'], 'template': item.get('template') or parent['template'],
               'kind': parent.get('kind', 'page'), 'is_dynamic': True, 'instance_status': 'concrete',
               'discovered_by': ['gap-suggested']}
        if item.get('source'):
            new['content_source'] = item['source']
        pages.append(new)
        by_url[path] = new
        added.append(new)
    pages.sort(key=lambda p: (p['url'] != '/', p['url'].count('/'), p['url']))
    return pages, added


def build_manifest(snapshot, fingerprint, *, routes=None, site_url=None, extra_urls=(), learned_rows=(), own_hosts=()):
    """Return (pages, notes). `extra_urls` = [(url, method)] from sitemaps, build output and crawl."""
    files = snapshot['files']
    rows, adapter = adapter_routes(files, fingerprint)
    notes = {'adapter': adapter, 'fallback_routes_skill': False, 'excluded': [], 'unmatched_urls': [],
             'gaps': [], 'soft_404_risk': catch_all_rewrites(files), 'declared_redirects': []}
    for item in rows:
        if item['kind'] == 'gap':
            notes['gaps'].append({k: item.get(k) for k in ('gap', 'expression', 'declared_in', 'line', 'adapter')})
        elif item['kind'] == 'not-found' and item.get('catch_all'):
            notes['soft_404_risk'].append({'kind': 'client-catch-all', 'file': item['template'], 'line': item.get('line'),
                                           'adapter': item['adapter'], 'note': item.get('note')})
        for source in (item.get('aliases') or []) + (item.get('redirect_from') or []):
            notes['declared_redirects'].append({'from': source, 'to': item['url'], 'file': item['template'],
                                                'mechanism': 'hugo-aliases' if item.get('aliases') else 'jekyll-redirect-from'})
    data_links = [(item['url'], 'data-link') for item in rows if item['adapter'] == 'data-link']
    rows = [item for item in rows if item['kind'] != 'gap' and not (item['kind'] == 'not-found' and item.get('catch_all'))
            and item['adapter'] != 'data-link']
    if not rows and learned_rows:
        rows, notes['adapter'] = list(learned_rows), 'learned-adapter'
    if not rows and routes:
        notes['fallback_routes_skill'] = True
        for route in routes:
            if route.get('kind') not in PAGE_KINDS:
                continue
            location = (route.get('source') or {}).get('location') or {}
            rows.append({'url': route['route'], 'template': location.get('file'), 'kind': route.get('kind', 'page'),
                         'adapter': 'routes-skill:' + route.get('method', ''), 'is_dynamic': route.get('is_dynamic', False)})
    pages, by_path, patterns = [], {}, []

    def add(url, template, discovered, *, kind='page', route=None, is_dynamic=False, status='static', **extra):
        path = normalize_path(url, site_url, own_hosts) if not is_dynamic else url.rstrip('/') or '/'
        if path is None:
            return None
        key = path
        if key in by_path:
            page = by_path[key]
            if discovered not in page['discovered_by']:
                page['discovered_by'].append(discovered)
            if page['template'] is None and template:
                page['template'] = template
            return page
        page = {'id': page_id(path), 'url': path, 'route': route or path, 'template': template, 'kind': kind,
                'is_dynamic': is_dynamic, 'instance_status': status, 'discovered_by': [discovered], **extra}
        if not is_dynamic and (urlsplit(url).netloc or re.search(r'\.html?$|.+/$', url)):
            page['fetch_path'] = urlsplit(url).path or '/'  # request what the source/sitemap names, not the folded key
        pages.append(page)
        by_path[key] = page
        return page

    for item in rows:
        if item['kind'] not in PAGE_KINDS and item['kind'] not in {'utility'}:
            notes['excluded'].append({'url': item['url'], 'template': item['template'], 'kind': item['kind']})
            continue
        if item['kind'] == 'utility':
            notes['excluded'].append({'url': item['url'], 'template': item['template'], 'kind': 'utility'})
            continue
        method = item.get('discovered_by') or 'adapter:' + item['adapter']
        extra = {key: item[key] for key in ('i18n_param', 'site_root', 'entry_component') if item.get(key)}
        if item['adapter'] == 'nav-literal':
            extra['declared_in'] = item.get('declared_in')
        if item['is_dynamic']:
            pattern = item['url']
            add(pattern, item['template'], method, kind=item['kind'], route=pattern, is_dynamic=True, status='template',
                adapter_line=item.get('line'), **extra)
            patterns.append((pattern_regex(pattern, optional_rest=fingerprint.get('primary') in {'astro', 'sveltekit', 'solid-start', 'qwik'}),
                             pattern, item['template']))
            if item.get('i18n_param') and item.get('locales'):
                for locale in item['locales']:
                    concrete = pattern.replace('[' + item['i18n_param'] + ']', locale)
                    add(concrete, item['template'], 'i18n-locales', kind=item['kind'], route=pattern,
                        is_dynamic=bool(PLACEHOLDER.search(concrete)), status='template' if PLACEHOLDER.search(concrete) else 'concrete',
                        locale=locale)
            for value in static_params(files, item['template'], pattern):
                add(substitute(pattern, value), item['template'], 'static-params-literal', kind=item['kind'], route=pattern,
                    is_dynamic=True, status='concrete')
            for value, source in content_instances(files, pattern, item['template']):
                page = add(substitute(pattern, value), item['template'], 'content-collection', kind=item['kind'], route=pattern,
                           is_dynamic=True, status='concrete')
                if page is not None:
                    page.setdefault('content_source', source)
        else:
            page = add(item['url'], item['template'], method, kind=item['kind'], route=item['url'], **extra)
            if page is not None and item['adapter'] == 'static-html' and item['template']:
                page['fetch_path'] = '/' + re.sub(r'^(?:public|static|src|site|www|docs)/', '', item['template'])
    for url, method in list(sitemap_urls_from_source(files)) + data_links + list(extra_urls):
        path = normalize_path(url, site_url, own_hosts)
        if path is None:
            continue
        if path in by_path:
            add(path, None, method)
            continue
        ordered = sorted(patterns, key=lambda item: specificity(item[1]))
        match = next(((pattern, template) for regex, pattern, template in ordered if regex.match(path or '/')), None)
        if match:
            add(path, match[1], method, route=match[0], is_dynamic=True, status='concrete')
        else:
            add(path, None, method, kind='discovered', status='static')
            notes['unmatched_urls'].append({'url': path, 'method': method})
    # Most specific pattern first so /blog/[slug] wins over /[...slug].
    pages.sort(key=lambda p: (p['url'] != '/', p['url'].count('/'), p['url']))
    return pages, notes
