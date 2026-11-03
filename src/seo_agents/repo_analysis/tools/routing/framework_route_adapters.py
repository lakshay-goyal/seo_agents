"""Framework route adapters: public URL pattern -> entry template, per stack.

Each adapter is deterministic and keyed by the framework fingerprint. It returns
page rows: {url, template, kind, is_dynamic, adapter, params, line?}. Patterns keep
the framework's own placeholder syntax (`[slug]`, `:slug`, `{post}`, `<slug:slug>`).

Covered: Next (app + pages, basePath), Nuxt, SvelteKit, Astro, Remix/React Router 7,
Gatsby, Docusaurus/VitePress (docs), Eleventy/Jekyll/Hugo (content + permalinks),
Shopify themes (JSON + Liquid templates), WordPress classic and block themes
(template hierarchy), Ghost, Django, Flask/FastAPI, Laravel, Rails, Express,
ASP.NET Razor Pages, static HTML and code-declared SPA routers (React Router,
Vue Router, Angular, Solid/Preact routers) including nested `children`.
Unknown stacks fall back to the generic routes skill (`tools/routes.py`).
"""

from __future__ import annotations

import posixpath
import re
from pathlib import PurePosixPath

from seo_agents.repo_analysis.tools.parsing.element_index import load_aliases, resolve_module

DYNAMIC = re.compile(r'\[[^\]]+\]|(?:^|/):\w+|\{[^}]+\}|<[^>]+>|\*')
INTERCEPTING = re.compile(r'^\((?:\.{1,3}|\.\.\)\(\.\.)\)')  # Next intercepting routes: (.)x (..)x (..)(..)x (...)x
I18N_PARAMS = {'locale', 'lang', 'lng', 'language', 'hl'}
TEST_PATH = re.compile(r'(?:^|/)(?:tests?|__tests__|fixtures|spec|e2e|cypress|playwright|stories|node_modules)/|\.(?:test|spec|stories)\.')


def row(url, template, adapter, kind='page', **extra):
    url = '/' + url.strip('/') if url not in ('', '/') else '/'
    url = re.sub(r'/{2,}', '/', url)
    return {'url': url, 'template': template, 'kind': kind, 'adapter': adapter,
            'is_dynamic': bool(DYNAMIC.search(url)), **extra}


def strip_segments(parts, *, groups=True, private=True):
    kept = []
    for part in parts:
        if INTERCEPTING.match(part):
            return None  # intercepting route: renders an existing URL in a modal, not a URL of its own
        if groups and re.fullmatch(r'\([^()]*\)', part):
            continue
        if part.startswith('@'):
            return None  # parallel-route slot: not a URL of its own
        if private and part.startswith('_'):
            return None
        kept.append(part)
    return kept


def file_routes(files, base, suffixes, adapter, *, index_names=('index',), skip=(), page_file=None, kind='page'):
    """Generic file-system router: base/**/name.ext -> /path."""
    rows = []
    for path in sorted(files):
        if not path.startswith(base) or TEST_PATH.search(path):
            continue
        relative = path[len(base):]
        suffix = PurePosixPath(relative).suffix
        if suffix not in suffixes:
            continue
        stem = relative[:-len(suffix)]
        parts = stem.split('/')
        if page_file:
            if parts[-1] != page_file:
                continue
            parts = parts[:-1]
        elif parts[-1] in index_names:
            parts = parts[:-1]
        if any(re.fullmatch(pattern, '/'.join(parts) or '/') for pattern in skip):
            continue
        parts = strip_segments(parts)
        if parts is None:
            continue
        rows.append(row('/'.join(parts), path, adapter, kind))
    return rows


def roots(files, marker):
    """Directories that contain a framework marker (monorepo-aware)."""
    found = sorted({posixpath.dirname(p) for p in files if re.search(marker, p) and 'node_modules' not in p})
    return [d + '/' if d else '' for d in found] or ['']


def next_routes(files):
    rows = []
    for root in roots(files, r'(?:^|/)next\.config\.[cm]?[jt]s$|(?:^|/)package\.json$'):
        base_path = ''
        for name in ('next.config.js', 'next.config.mjs', 'next.config.ts', 'next.config.cjs'):
            match = re.search(r'''basePath\s*:\s*['"]([^'"]+)['"]''', files.get(root + name, ''))
            if match:
                base_path = match.group(1)
        locales = next_locales(files, root)
        for app in (root + 'app/', root + 'src/app/'):
            for r in file_routes(files, app, {'.tsx', '.jsx', '.ts', '.js', '.mdx', '.md'}, 'next-app', page_file='page'):
                url = (base_path + r['url']).rstrip('/') or '/'
                param = next((n for n in re.findall(r'\[(\w+)\]', url) if n.lower() in I18N_PARAMS), None)
                extra = {'i18n_param': param, 'locales': locales} if param else {}
                rows.append({**r, 'url': url, **extra})
        for pages in (root + 'pages/', root + 'src/pages/'):
            for r in file_routes(files, pages, {'.tsx', '.jsx', '.ts', '.js', '.mdx', '.md'}, 'next-pages',
                                 skip=(r'_app|_document|_error|api(?:/.*)?|404|500|_middleware',)):
                if not r['template'].startswith(app):
                    rows.append({**r, 'url': (base_path + r['url']).rstrip('/') or '/'})
    return rows


def next_locales(files, root=''):
    """Locales declared for App Router i18n (middleware / next-intl routing / i18n config) or next.config."""
    for name in ('middleware.ts', 'middleware.js', 'src/middleware.ts', 'src/middleware.js', 'i18n/routing.ts',
                 'src/i18n/routing.ts', 'i18n.ts', 'src/i18n.ts', 'i18n-config.ts', 'i18n.config.ts',
                 'next-i18next.config.js', 'next.config.js', 'next.config.mjs', 'next.config.ts'):
        match = re.search(r'''\blocales\s*:\s*\[([^\]]*)\]''', files.get(root + name, ''))
        if match:
            found = re.findall(r'''['"`]([A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,4})?)['"`]''', match.group(1))
            if found:
                return found
    return []


def nuxt_routes(files):
    rows = []
    for base in ('pages/', 'src/pages/', 'app/pages/'):
        rows += file_routes(files, base, {'.vue'}, 'nuxt-pages')
    return rows


def sveltekit_routes(files):
    return file_routes(files, 'src/routes/', {'.svelte'}, 'sveltekit', page_file='+page')


def astro_routes(files):
    rows = []
    for root in roots(files, r'(?:^|/)astro\.config\.[cm]?[jt]s$'):
        rows += file_routes(files, root + 'src/pages/', {'.astro', '.md', '.mdx', '.html'}, 'astro-pages', skip=(r'404|500',))
    return rows


def remix_routes(files):
    """Remix v2 / React Router 7 flat routes (`blog.$slug.tsx`, `blog.$slug/route.tsx`) and Remix v1
    nested folders (`routes/blog/$slug.tsx`, `routes/blog/index.tsx`, `__layout` pathless dirs)."""
    rows = []
    route_dirs = {posixpath.dirname(p) for p in files if re.match(r'^app/routes/.+/route\.(?:tsx|jsx|ts|js|mdx)$', p)}
    for path in sorted(files):
        match = re.match(r'^app/routes/(.+?)\.(?:tsx|jsx|ts|js|mdx|md)$', path)
        if not match or TEST_PATH.search(path):
            continue
        relative = match.group(1)
        directory = posixpath.dirname(path)
        if relative.endswith('/route'):
            relative = relative[:-len('/route')]  # v2 folder route
        elif directory in route_dirs or any(directory.startswith(d + '/') for d in route_dirs):
            continue  # colocated module inside a v2 folder route, not a route of its own
        if re.match(r'^(?:api|resources)(?:[./]|$)', relative) or relative.endswith('[.]xml'):
            continue
        segments = []
        for part in relative.split('/'):
            for segment in re.split(r'\.(?![^\[]*\])', part):
                optional = bool(re.fullmatch(r'\(.+\)', segment))
                segment = segment[1:-1] if optional else segment
                if segment in {'_index', 'index'} or (segment.startswith('_') and len(segment) > 1):
                    continue  # index route / pathless layout (v2 `_auth`, v1 `__auth`)
                segment = segment.rstrip('_') or segment  # `blog_` opts out of the parent layout, not the URL
                segment = re.sub(r'\[(.+?)\]', r'\1', segment)  # escaped literal: [.]xml
                if segment.startswith('$'):
                    segment = ':' + segment[1:] if segment != '$' else '*'
                segments.append(segment + ('?' if optional else ''))
        if '*' in segments:
            rows.append(row('/'.join(segments), path, 'remix-flat-routes', kind='not-found', catch_all=True))
            continue
        rows.append(row('/'.join(segments), path, 'remix-flat-routes'))
    return rows


def balanced(text, start, pair='{}'):
    """Index just past the bracket matching text[start] (string/comment aware); len(text) if unbalanced."""
    open_, close = pair
    depth, index, quote = 0, start, None
    while index < len(text):
        char = text[index]
        if quote:
            if char == '\\':
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in '"\'`':
            quote = char
        elif text.startswith('//', index) and (index == 0 or text[index - 1] != ':'):
            newline = text.find('\n', index)
            index = len(text) if newline < 0 else newline
            continue
        elif text.startswith('/*', index):
            end = text.find('*/', index + 2)
            index = len(text) if end < 0 else end + 2
            continue
        elif char == open_:
            depth += 1
        elif char == close:
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return len(text)


def top_level(body):
    """Bracketed body with nested {...}/[...] contents blanked, so only the outer object's own keys match."""
    out, depth, quote = [], 0, None
    for index, char in enumerate(body):
        if quote:
            out.append(char if depth <= 1 else ' ')
            if char == quote and body[index - 1] != '\\':
                quote = None
            continue
        if char in '"\'`':
            quote = char
            out.append(char if depth <= 1 else ' ')
        elif char in '{[':
            depth += 1
            out.append(char if depth == 1 else ' ')
        elif char in '}]':
            out.append(char if depth == 1 else ' ')
            depth -= 1
        else:
            out.append(char if depth <= 1 else ' ')
    return ''.join(out)


GATSBY_COMPONENT_VAR = re.compile(
    r'''(?:const|let|var)\s+(\w+)\s*=\s*(?:path\.resolve\s*\(|require\.resolve\s*\()?\s*[`'"]([^`'"]+\.(?:js|jsx|ts|tsx))[`'"]''')
GATSBY_COMPONENT = re.compile(
    r'''\bcomponent\s*:\s*(?:path\.resolve\s*\(|require\.resolve\s*\()?\s*(?:[`'"]([^`'"]+)[`'"]|(\w+))''')


def gatsby_routes(files):
    rows = file_routes(files, 'src/pages/', {'.js', '.jsx', '.tsx', '.ts'}, 'gatsby-pages', skip=(r'404',))
    for path in ('gatsby-node.js', 'gatsby-node.ts', 'gatsby-node.mjs'):
        source = files.get(path, '')
        variables = {m.group(1): m.group(2) for m in GATSBY_COMPONENT_VAR.finditer(source)}
        for call in re.finditer(r'\bcreatePage\s*\(\s*\{', source):
            brace = call.end() - 1
            body = top_level(source[brace:balanced(source, brace)])  # nested context:{...} no longer ends the object
            url = re.search(r'''\bpath\s*:\s*([`'"])(.+?)\1''', body)
            if not url:
                continue
            component = GATSBY_COMPONENT.search(body)
            target = (component.group(1) or variables.get(component.group(2) or '')) if component else None
            if target:
                target = re.sub(r'^\$\{__dirname\}/', '', target)
                target = posixpath.normpath(target[2:] if target.startswith('./') else target)
            pattern = re.sub(r'\$\{[^}]+\}', ':param', url.group(2))
            rows.append(row(pattern, target if target in files else path, 'gatsby-create-page',
                            line=source.count('\n', 0, call.start()) + 1, declared_in=path))
    return rows


def docs_routes(files, base='docs/', adapter='docs'):
    return file_routes(files, base, {'.md', '.mdx'}, adapter, index_names=('index', 'README', 'readme'))


def _scalar(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
        return value[1:-1]
    value = re.sub(r'\s+#.*$', '', value)
    if value.startswith('[') and value.endswith(']'):
        return [_scalar(v) for v in re.findall(r'''"[^"]*"|'[^']*'|[^,\[\]]+''', value[1:-1]) if v.strip()]
    return value


def _yaml_front_matter(block):
    """Flat YAML subset: scalars, quoted strings, inline [a, b] lists, block `- item` lists, `|`/`>` blocks."""
    fields, lines = {}, block.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        pair = re.match(r'^([\w\-]+)\s*:\s*(.*)$', line)
        index += 1
        if not pair:
            continue
        key, rest = pair.group(1), pair.group(2).rstrip()
        if rest in {'|', '>', '|-', '>-', '|+', '>+'}:
            body = []
            while index < len(lines) and (lines[index].startswith((' ', '\t')) or not lines[index].strip()):
                body.append(lines[index].strip())
                index += 1
            joiner = '\n' if rest.startswith('|') else ' '
            fields[key] = joiner.join(b for b in body).strip()
        elif rest == '':
            items = []
            while index < len(lines) and re.match(r'^\s*-\s+', lines[index]):
                items.append(_scalar(re.sub(r'^\s*-\s+', '', lines[index])))
                index += 1
            fields[key] = items if items else ''
        else:
            fields[key] = _scalar(rest)
    return fields


def _toml_front_matter(block):
    fields = {}
    for line in block.splitlines():
        if line.strip().startswith('['):
            break  # nested tables are not page-level fields
        pair = re.match(r'^\s*([\w\-]+)\s*=\s*(.+?)\s*$', line)
        if pair:
            fields[pair.group(1)] = _scalar(pair.group(2))
    return fields


def front_matter(source):
    """Page front matter as {key: str | list}: YAML `---`, Hugo TOML `+++` or JSON `{...}`."""
    source = source.lstrip('﻿')
    match = re.match(r'\A---[ \t]*\r?\n(.*?)\r?\n(?:---|\.\.\.)[ \t]*(?:\r?\n|\Z)', source, re.S)
    if match:
        return _yaml_front_matter(match.group(1))
    match = re.match(r'\A\+\+\+[ \t]*\r?\n(.*?)\r?\n\+\+\+[ \t]*(?:\r?\n|\Z)', source, re.S)
    if match:
        return _toml_front_matter(match.group(1))
    if source.startswith('{'):
        import json
        end = balanced(source, 0)
        try:
            document = json.loads(source[:end])
        except ValueError:
            return {}
        if isinstance(document, dict):
            return {k: v if isinstance(v, (str, list)) else str(v) for k, v in document.items()
                    if isinstance(v, (str, list, int, float, bool))}
    return {}


def has_front_matter(source):
    return bool(re.match(r'\A﻿?(?:---|\+\+\+)[ \t]*\r?\n', source)) or source.startswith('{')


def as_list(value):
    if not value:
        return []
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


DOC_FILE = re.compile(r'(?:^|/)(?:README|CHANGELOG|CONTRIBUTING|CODE_OF_CONDUCT|LICEN[CS]E|SECURITY|HISTORY)(?:\.[\w.]+)?$', re.I)


def _redirect_sources(fields, *keys):
    """Hugo `aliases`, Jekyll `redirect_from`, Eleventy `redirect_from`: URLs that should redirect here."""
    return [v if v.startswith('/') else '/' + v for key in keys for v in as_list(fields.get(key)) if v]


def jekyll_routes(files):
    rows = []
    config = files.get('_config.yml', '') or files.get('_config.yaml', '')
    permalink = re.search(r'(?m)^permalink:\s*(\S+)', config)
    for path in sorted(files):
        if path.startswith(('_', '.', 'vendor/', 'node_modules/')) or TEST_PATH.search(path):
            continue
        if PurePosixPath(path).suffix not in {'.md', '.markdown', '.html'}:
            continue
        source = files[path]
        if not has_front_matter(source):
            continue  # Jekyll only renders files with front matter
        fields = front_matter(source)
        if str(fields.get('published', '')).lower() == 'false':
            continue
        url = fields.get('permalink') or '/' + re.sub(r'(?:^|/)index$', '', path.rsplit('.', 1)[0])
        rows.append(row(str(url), path, 'jekyll-pages', layout=fields.get('layout'),
                        redirect_from=_redirect_sources(fields, 'redirect_from') or None))
    for path in sorted(p for p in files if p.startswith('_posts/')):
        match = re.match(r'_posts/(?:.+/)?(\d{4})-(\d{2})-(\d{2})-(.+)\.(?:md|markdown|html)$', path)
        if match:
            fields = front_matter(files[path])
            if str(fields.get('published', '')).lower() == 'false':
                continue
            style = (permalink.group(1) if permalink else '/:categories/:year/:month/:day/:title:output_ext')
            title = fields.get('slug') or match.group(4)
            url = fields.get('permalink') or (style.replace(':categories/', '').replace(':year', match.group(1))
                                              .replace(':month', match.group(2)).replace(':day', match.group(3))
                                              .replace(':title', str(title)).replace(':output_ext', '.html'))
            if url in {'pretty', 'date'}:
                url = f'/{match.group(1)}/{match.group(2)}/{match.group(3)}/{title}/'
            rows.append(row(str(url), path, 'jekyll-posts', kind='content-page', layout=fields.get('layout'),
                            redirect_from=_redirect_sources(fields, 'redirect_from') or None))
    return rows


def hugo_content_dir(files):
    for name in ('hugo.toml', 'config.toml', 'hugo.yaml', 'config.yaml', 'hugo.yml', 'config.yml',
                 'config/_default/hugo.toml', 'config/_default/config.toml'):
        match = re.search(r'''(?im)^\s*contentDir\s*[=:]\s*["']?([\w./\-]+)''', files.get(name, ''))
        if match:
            return match.group(1).strip('/') + '/'
    return 'content/'


def hugo_routes(files):
    rows = []
    base = hugo_content_dir(files)
    for path in sorted(files):
        if not path.startswith(base):
            continue
        match = re.match(r'^(.+)\.(?:md|markdown|html|adoc|org)$', path[len(base):])
        if not match:
            continue
        fields = front_matter(files[path])
        if str(fields.get('draft', '')).lower() == 'true' or str(fields.get('headless', '')).lower() == 'true':
            continue
        relative = match.group(1)
        language = None
        lang = re.match(r'^(.+)\.([a-z]{2}(?:-[a-z]{2})?)$', relative)  # index.fr.md -> fr translation
        if lang:
            relative, language = lang.group(1), lang.group(2)
        if fields.get('url'):
            url = str(fields['url'])
        else:
            parts = relative.split('/')
            if parts[-1] in {'_index', 'index'}:
                parts = parts[:-1]
            elif fields.get('slug'):
                parts[-1] = str(fields['slug'])
            url = '/'.join(parts)
            if language:
                url = language + '/' + url
        rows.append(row(url, path, 'hugo-content', kind='content-page',
                        section_list=PurePosixPath(relative).name == '_index',
                        aliases=_redirect_sources(fields, 'aliases') or None, language=language))
    if not any(r['url'] == '/' for r in rows) and 'layouts/index.html' in files:
        rows.append(row('/', 'layouts/index.html', 'hugo-home'))
    return [{k: v for k, v in r.items() if v is not None} for r in rows]


def eleventy_routes(files):
    rows = []
    config = next((files[p] for p in files if re.search(r'(?:^|/)(?:\.eleventy|eleventy\.config)\.[cm]?js$', p)), '')
    input_dir = re.search(r'''input\s*:\s*['"]([^'"]+)['"]''', config)
    base = (input_dir.group(1).strip('./') + '/') if input_dir and input_dir.group(1).strip('./') else ''
    for path in sorted(files):
        if not path.startswith(base) or '/_' in '/' + path[len(base):] or TEST_PATH.search(path):
            continue
        if path.startswith(('node_modules/', 'vendor/')) or (DOC_FILE.search(path) and not has_front_matter(files[path])):
            continue
        name = PurePosixPath(path).name
        if not (name.endswith('.11ty.js') or PurePosixPath(path).suffix in {'.md', '.njk', '.liquid', '.html', '.hbs', '.pug', '.webc'}):
            continue
        fields = front_matter(files[path])
        relative = path[len(base):].removesuffix('.11ty.js').rsplit('.', 1)[0] if not name.endswith('.11ty.js') \
            else path[len(base):].removesuffix('.11ty.js')
        url = fields.get('permalink') or '/' + re.sub(r'(?:^|/)index$', '', relative)
        if isinstance(url, list) or str(url).lower() == 'false':
            continue
        url = str(url)
        templated = bool(re.search(r'\{\{|\{%', url))
        if templated:
            url = re.sub(r'\{\{[^}]*\}\}|\{%[^%]*%\}', ':param', url)
        rows.append(row(url, path, 'eleventy', layout=fields.get('layout'), **({'pagination': True} if fields.get('pagination') or templated else {})))
    return rows


SHOPIFY_URLS = {
    'index': '/', 'product': '/products/:handle', 'collection': '/collections/:handle', 'list-collections': '/collections',
    'page': '/pages/:handle', 'blog': '/blogs/:blog', 'article': '/blogs/:blog/:article', 'cart': '/cart',
    'search': '/search', '404': '/404', 'password': '/password',
}


def shopify_routes(files):
    rows = []
    for path in sorted(files):
        match = re.match(r'^(?:(.*)/)?templates/([\w\-]+)(?:\.([\w\-]+))?\.(json|liquid)$', path)
        if not match:
            continue
        name, variant = match.group(2), match.group(3)
        if name not in SHOPIFY_URLS:
            continue
        url = SHOPIFY_URLS[name]
        kind = 'not-found' if name == '404' else 'utility' if name in {'cart', 'password'} else 'page'
        if variant:
            url += '?view=' + variant
            kind = 'alternate-template'
        # JSON template wins over a same-named Liquid template (Shopify's precedence).
        if path.endswith('.liquid') and path[:-7] + '.json' in files:
            continue
        rows.append(row(url, path, 'shopify-theme', kind=kind))
    return rows


def wordpress_routes(files):
    rows = []
    theme_roots = sorted({posixpath.dirname(p) for p in files if re.search(r'(?:^|/)style\.css$', p)
                          and re.search(r'(?i)Theme Name\s*:', files[p])}) or sorted(
        {posixpath.dirname(p) for p in files if re.search(r'(?:^|/)(?:index\.php|theme\.json)$', p)})
    for theme in theme_roots or ['']:
        prefix = theme + '/' if theme else ''
        names = {PurePosixPath(p).stem: p for p in files if p.startswith(prefix) and posixpath.dirname(p) == theme and p.endswith('.php')}
        names.update({PurePosixPath(p).stem: p for p in files if p.startswith(prefix + 'templates/') and p.endswith('.html')})
        home = names.get('front-page') or names.get('home') or names.get('index')
        if home:
            rows.append(row('/', home, 'wordpress-template-hierarchy'))
        if names.get('front-page') and names.get('home'):
            rows.append(row('/blog', names['home'], 'wordpress-template-hierarchy'))
        for stem, path in sorted(names.items()):
            if stem in {'single', 'singular'}:
                rows.append(row('/:post', path, 'wordpress-template-hierarchy'))
            elif stem == 'page':
                rows.append(row('/:page', path, 'wordpress-template-hierarchy'))
            elif stem.startswith('page-') and not stem.startswith('page-templates'):
                rows.append(row('/' + stem[5:], path, 'wordpress-template-hierarchy'))
            elif stem.startswith('single-'):
                rows.append(row('/' + stem[7:] + '/:slug', path, 'wordpress-template-hierarchy'))
            elif stem.startswith('archive-'):
                rows.append(row('/' + stem[8:], path, 'wordpress-template-hierarchy', kind='archive'))
            elif stem in {'category', 'tag', 'author'}:
                rows.append(row(f'/{stem}/:{stem}', path, 'wordpress-template-hierarchy', kind='archive'))
            elif stem.startswith(('category-', 'tag-')):
                kind, _, slug = stem.partition('-')
                rows.append(row(f'/{kind}/{slug}', path, 'wordpress-template-hierarchy', kind='archive'))
            elif stem == 'archive':
                rows.append(row('/:archive', path, 'wordpress-template-hierarchy', kind='archive'))
            elif stem == 'search':
                rows.append(row('/search/:query', path, 'wordpress-template-hierarchy', kind='utility'))
            elif stem == '404':
                rows.append(row('/404', path, 'wordpress-template-hierarchy', kind='not-found'))
        if not names.get('single') and not names.get('singular') and names.get('index'):
            rows.append(row('/:post', names['index'], 'wordpress-template-hierarchy', fallback=True))
        if not names.get('page') and not names.get('singular') and names.get('index'):
            rows.append(row('/:page', names['index'], 'wordpress-template-hierarchy', fallback=True))
    return rows


def ghost_routes(files):
    mapping = {'index': '/', 'home': '/', 'post': '/:slug', 'page': '/:page', 'tag': '/tag/:slug', 'author': '/author/:slug'}
    rows = []
    for path in sorted(files):
        stem = PurePosixPath(path).stem
        if path.endswith('.hbs') and posixpath.dirname(path) in {'', '.'} and stem in mapping:
            if stem == 'index' and 'home.hbs' in files:
                continue
            rows.append(row(mapping[stem], path, 'ghost-theme'))
    return rows


def find_template(files, name, directories):
    name = name.strip()
    for directory in directories:
        for candidate in (directory + name, directory + name + '.html'):
            if candidate in files:
                return candidate
    matches = [p for p in files if p.endswith('/' + name) or p == name]
    return matches[0] if len(matches) == 1 else None


def python_view_template(files, view_ref, decl_file):
    """views.home -> render(request, 'home.html') -> templates/home.html."""
    module, _, function = view_ref.rpartition('.')
    candidates = [p for p in files if p.endswith('views.py') or p.endswith('/views/' + function + '.py')]
    if module:
        candidates.sort(key=lambda p: (module.split('.')[-1] not in p, p))
    for path in candidates:
        match = re.search(r'def\s+' + re.escape(function) + r'\s*\((.*?)(?=\ndef\s|\nclass\s|\Z)', files[path], re.S)
        if match:
            template = re.search(r'''(?:render|render_to_response|TemplateResponse)\s*\([^)]*?['"]([\w./\-]+\.html?)['"]''', match.group(0))
            if template:
                return find_template(files, template.group(1), ['templates/', posixpath.dirname(path) + '/templates/'])
    class_view = re.search(r'''template_name\s*=\s*['"]([^'"]+)['"]''', ''.join(files[p] for p in candidates))
    return None if not class_view else find_template(files, class_view.group(1), ['templates/'])


def django_routes(files):
    rows = []

    def walk(path, prefix, depth=0):
        if depth > 5 or path not in files:
            return
        for match in re.finditer(r'''\b(?:re_)?path\s*\(\s*r?['"]([^'"]*)['"]\s*,\s*([^,)]+)''', files[path]):
            pattern, target = match.group(1), match.group(2).strip()
            line = files[path].count('\n', 0, match.start()) + 1
            include = re.match(r'''include\s*\(\s*['"]([\w.]+)['"]''', target)
            if include:
                module = include.group(1).replace('.', '/') + '.py'
                walk(module if module in files else next((p for p in files if p.endswith(module)), ''), prefix + pattern, depth + 1)
                continue
            if target.startswith('TemplateView.as_view'):
                name = re.search(r'''template_name\s*=\s*['"]([^'"]+)''', target + files[path][match.end():match.end() + 200])
                template = find_template(files, name.group(1), ['templates/']) if name else None
            else:
                template = python_view_template(files, target.replace('.as_view()', ''), path)
            url = '/' + (prefix + pattern).lstrip('^').rstrip('$')
            rows.append(row(url, template or path, 'django-urls', line=line, declared_in=path, template_resolved=bool(template)))
    for path in sorted(p for p in files if p.endswith('urls.py')):
        if re.search(r'''\binclude\s*\(''', ''.join(files[q] for q in files if q.endswith('urls.py') and q != path)) and \
                any(path.replace('/', '.').removesuffix('.py').endswith(m) for m in re.findall(r'''include\s*\(\s*['"]([\w.]+)['"]''',
                    ''.join(files[q] for q in files if q.endswith('urls.py')))):
            continue  # included by another urls.py; walked from the root
        walk(path, '')
    return rows


def flask_routes(files):
    rows = []
    for path in sorted(p for p in files if p.endswith('.py') and not TEST_PATH.search(p)):
        for match in re.finditer(r'''@\w+\.(?:route|get)\s*\(\s*['"]([^'"]+)['"][^)]*\)\s*\n(?:@[^\n]+\n)*\s*(?:async\s+)?def\s+\w+\s*\([^)]*\)\s*(?:->[^:]+)?:(.*?)(?=\n@|\ndef\s|\nclass\s|\Z)''',
                                 files[path], re.S):
            template = re.search(r'''(?:render_template|TemplateResponse)\s*\(\s*['"]([^'"]+)['"]''', match.group(2))
            resolved = find_template(files, template.group(1), ['templates/', posixpath.dirname(path) + '/templates/']) if template else None
            if resolved:
                rows.append(row(match.group(1), resolved, 'flask-routes', line=files[path].count('\n', 0, match.start()) + 1, declared_in=path))
    return rows


def enclosing_prefix(ranges, offset):
    """Concatenated prefixes of every (start, end, prefix) range that contains offset, outermost first."""
    return ''.join(p.strip('/') + '/' for s, e, p in sorted(ranges) if s <= offset < e and p.strip('/'))


def laravel_routes(files):
    """routes/*.php (not api/console/channels): Route::get/view/any/match/resource(s), with
    Route::prefix('x')->group(...) and Route::group(['prefix' => 'x'], ...) prefixes applied."""
    rows = []
    for decl in sorted(p for p in files if re.match(r'^routes/[\w\-/]+\.php$', p)
                       and not re.search(r'(?:^|/)(?:api|console|channels)\.php$', p)):
        source = files[decl]
        ranges = []
        for group in re.finditer(r'''Route::(?:[\w:>\-()'"\s,]*?)prefix\s*\(\s*['"]([^'"]*)['"]\s*\)[^;{]*?->\s*group\s*\(''', source):
            paren = source.find('(', group.end() - 1)
            ranges.append((group.start(), balanced(source, paren, '()'), group.group(1)))
        for group in re.finditer(r'''Route::group\s*\(\s*\[[^\]]*['"]prefix['"]\s*=>\s*['"]([^'"]*)['"]''', source):
            paren = source.find('(', group.start())
            ranges.append((group.start(), balanced(source, paren, '()'), group.group(1)))
        for match in re.finditer(r'''Route::(get|view|any|match|resource|resources)\s*\(\s*(?:\[[^\]]*\]\s*,\s*)?['"]([^'"]+)['"]\s*,''', source):
            method, url = match.group(1), match.group(2)
            paren = source.find('(', match.start())
            body = source[match.end():balanced(source, paren, '()') - 1]
            line = source.count('\n', 0, match.start()) + 1
            prefix = enclosing_prefix(ranges, match.start())
            full = '/' + prefix + url.strip('/')
            if method in {'resource', 'resources'}:
                name = url.strip('/').split('/')[-1].split('.')[-1]
                only = re.search(r'''->only\s*\(\s*\[([^\]]*)\]''', source[match.end():match.end() + 300])
                actions = set(re.findall(r'''['"](\w+)['"]''', only.group(1))) if only else {'index', 'show'}
                folder = name.replace('.', '/')
                param = '{' + (name[:-1] if name.endswith('s') else name) + '}'
                for action, path in (('index', full), ('show', full.rstrip('/') + '/' + param)):
                    if action in actions:
                        template = next((c for c in (f'resources/views/{folder}/{action}.blade.php', f'resources/views/{folder}/{action}.php') if c in files), None)
                        rows.append(row(path, template or decl, 'laravel-routes', line=line, declared_in=decl, template_resolved=bool(template)))
                continue
            view = re.search(r'''^\s*['"]([\w.\-/]+)['"]''', body) if method == 'view' else re.search(r'''\bview\s*\(\s*['"]([\w.\-/]+)['"]''', body)
            if not view:
                controller = re.search(r'''\[\s*([\w\\]+)::class\s*,\s*['"](\w+)['"]\s*\]|['"]([\w\\]+)@(\w+)['"]''', body)
                if controller:
                    cls = (controller.group(1) or controller.group(3)).split('\\')[-1]
                    action_name = controller.group(2) or controller.group(4)
                    file = next((p for p in files if p.endswith('/' + cls + '.php')), None)
                    if file:
                        view = re.search(r'function\s+' + re.escape(action_name) + r'\s*\(.*?\bview\s*\(\s*[\'"]([\w.\-/]+)[\'"]', files[file], re.S)
            if method in {'get', 'any', 'match'} and not view and re.search(r'\bresponse\(\)\s*->\s*json|\breturn\s+\[', body):
                continue  # JSON endpoint, not a page
            template = None
            if view:
                dotted = view.group(1).replace('.', '/')
                template = next((c for c in ('resources/views/' + dotted + '.blade.php', 'resources/views/' + dotted + '.php') if c in files), None)
            rows.append(row(full, template or decl, 'laravel-routes', line=line, declared_in=decl, template_resolved=bool(template)))
    return rows


def rails_routes(files):
    """config/routes.rb with namespace/scope/nested resources (structural parser in tools/routes.py)."""
    from seo_agents.repo_analysis.tools.routing.route_discovery import rails_routes as nested_rails_routes
    rows = []
    source = files.get('config/routes.rb', '')

    def view(controller, action):
        for ext in ('.html.erb', '.html.haml', '.html.slim', '.erb'):
            candidate = f'app/views/{controller}/{action}{ext}'
            if candidate in files:
                return candidate
        return None
    for start, end, url in nested_rails_routes(source):
        code = re.sub(r'''(?:^|\s)#[^'"]*$''', '', source[start:end].strip()).strip()  # comment, not controller#action
        target = re.search(r'''(?:to:\s*|=>\s*)['"]([\w/]+)#(\w+)['"]|^root\s+['"]([\w/]+)#(\w+)['"]''', code)
        if target:
            controller, action = (target.group(1), target.group(2)) if target.group(1) else (target.group(3), target.group(4))
        elif re.match(r'resources?\s+:(\w+)', code):
            controller = re.match(r'resources?\s+:(\w+)', code).group(1)
            action = 'show' if url.endswith(':id') else 'index'
            namespace = [s for s in url.strip('/').split('/') if s and not s.startswith(':')][:-1]
            if namespace and view('/'.join(namespace + [controller]), action):
                controller = '/'.join(namespace + [controller])
        else:
            parts = [s for s in url.strip('/').split('/') if s and not s.startswith(':')]
            controller, action = ('/'.join(parts[:-1]), parts[-1]) if len(parts) > 1 else ('pages', parts[0] if parts else 'home')
        rows.append(row(url, view(controller, action) or 'config/routes.rb', 'rails-routes',
                        line=source.count('\n', 0, start) + 1, declared_in='config/routes.rb'))
    return rows


EXPRESS_ROUTE = re.compile(r'''\b(\w+)\.get\s*\(\s*['"`](/[^'"`]*)['"`]\s*,''')
HTML_RESPONSE = re.compile(r'''\bres\.render\s*\(\s*['"`]([^'"`]+)['"`]|\bres\.sendFile\s*\([^)]*?['"`]([^'"`]*\.html?)['"`]|'''
                           r'''\b(?:res|reply|ctx)\.(?:send|type\s*\(\s*['"]html['"]\s*\)\s*\.send|body\s*=)\s*\(?\s*[`'"]\s*<|'''
                           r'''\breply\.view\s*\(\s*['"`]([^'"`]+)['"`]|\bctx\.render\s*\(\s*['"`]([^'"`]+)['"`]''')


def express_routes(files):
    """Express/Koa/Fastify GET routes whose handler returns HTML (render, sendFile *.html, send `<...`),
    with `app.use('/prefix', router)` mount prefixes resolved across files."""
    rows = []
    sources = sorted(p for p in files if re.search(r'\.[cm]?[jt]s$', p) and not TEST_PATH.search(p) and 'node_modules' not in p)
    mounts = {}  # router file -> prefix
    for path in sources:
        source = files[path]
        imports = {}
        for match in re.finditer(r'''(?:const|let|var|import)\s+(\w+)\s*(?:=\s*require\s*\(\s*|from\s+)['"](\.[^'"]+)['"]''', source):
            imports[match.group(1)] = match.group(2)
        for match in re.finditer(r'''\b\w+\.use\s*\(\s*['"`](/[^'"`]*)['"`]\s*,\s*(?:require\s*\(\s*['"](\.[^'"]+)['"]\s*\)|(\w+))''', source):
            spec = match.group(2) or imports.get(match.group(3) or '')
            if spec:
                base = posixpath.normpath(posixpath.join(posixpath.dirname(path), spec))
                target = next((base + ext for ext in ('', '.js', '.ts', '.mjs', '.cjs', '/index.js', '/index.ts') if base + ext in files), None)
                if target:
                    mounts[target] = match.group(1).rstrip('/')
    for path in sources:
        source = files[path]
        for match in EXPRESS_ROUTE.finditer(source):
            if match.group(1) not in {'app', 'router', 'server', 'fastify', 'api', 'r', 'routes'} and not match.group(1).endswith(('Router', 'router', 'app', 'App')):
                continue
            paren = source.find('(', match.start())
            body = source[match.end():balanced(source, paren, '()')]
            html = HTML_RESPONSE.search(body)
            if not html:
                continue
            name = next((g for g in (html.group(1), html.group(3), html.group(4)) if g), None)
            template = None
            if name:
                template = next((p for p in files if re.search(r'(?:^|/)views/' + re.escape(name.lstrip('/')) + r'(?:\.(?:ejs|pug|hbs|handlebars|njk|html|liquid|mustache))?$', p)), None)
            elif html.group(2):
                template = next((p for p in files if p.endswith(html.group(2).lstrip('./'))), None)
            url = mounts.get(path, '') + match.group(2)
            rows.append(row(url, template or path, 'express-routes', declared_in=path, line=source.count('\n', 0, match.start()) + 1))
    return rows


def razor_routes(files):
    rows = []
    for path in sorted(files):
        match = re.match(r'^(?:.*/)?Pages/(.+)\.cshtml$', path)
        if match and not re.search(r'(?:^|/)_|/Shared/', match.group(1)):
            directive = re.search(r'''@page\s+"([^"]+)"''', files[path])
            stem = re.sub(r'(?:^|/)Index$', '', match.group(1))
            rows.append(row(directive.group(1) if directive else '/' + stem.lower(), path, 'razor-pages'))
    return rows


def static_routes(files):
    rows = []
    for path in sorted(files):
        if PurePosixPath(path).suffix.lower() not in {'.html', '.htm'} or TEST_PATH.search(path):
            continue
        if re.search(r'(?:^|/)(?:_|partials?/|includes?/|components?/|layouts?/|templates?/|node_modules/|vendor/)', path):
            continue
        if re.search(r'(?:^|/)(?:README|LICENSE)', path):
            continue
        stem = path.rsplit('.', 1)[0]
        stem = re.sub(r'^(?:public|static|src|site|www|docs)/', '', stem)
        kind = 'not-found' if PurePosixPath(stem).name in {'404', '500'} else 'page'
        rows.append(row(re.sub(r'(?:^|/)index$', '', stem), path, 'static-html', kind=kind))
    return rows


# ---------------------------------------------------------------- declared SPA routers
def _ts_parser():
    from seo_agents.repo_analysis.tools.parsing.element_index import _parser
    return _parser


ROUTER_KEYS = ('element', 'component', 'Component', 'lazy', 'loadComponent', 'loadChildren', 'children', 'redirectTo',
               'redirect', 'index', 'errorElement', 'loader', 'components')


def spa_routes(files, adapter_name='spa-router'):
    """React Router / Vue Router / Angular / Solid route objects and <Route> JSX, with nesting."""
    parser_for = _ts_parser()
    aliases = load_aliases(files)
    rows = []
    for path in sorted(files):
        suffix = PurePosixPath(path).suffix
        if suffix not in {'.js', '.jsx', '.ts', '.tsx', '.mjs', '.vue'} or TEST_PATH.search(path) or 'node_modules' in path:
            continue
        source = files[path]
        if not re.search(r'createBrowserRouter|createHashRouter|createRouter|useRoutes|<Route\b|RouterModule|provideRouter|'
                         r'Routes\s*=|RouteObject|RouteRecordRaw|VueRouter|new Router\(|<Router\b', source):
            continue
        if suffix == '.vue':
            match = re.search(r'<script[^>]*>(.*?)</script>', source, re.S)
            if not match:
                continue
            code, offset = match.group(1), source[:match.start(1)].count('\n')
            language = 'ts'
        else:
            code, offset = source, 0
            language = 'tsx' if suffix == '.tsx' else 'ts' if suffix == '.ts' else 'js'
        data = code.encode()
        tree = parser_for(language).parse(data).root_node
        constants = None

        def route_value(node):
            """String literal, or a route constant (P.ABOUT, ROUTES.blog, `${BASE}/x`) resolved from this module or its imports."""
            nonlocal constants
            if node.type == 'string':
                return text(node)[1:-1]
            if constants is None:
                constants = module_constants(path, files, aliases)
            return resolve_route_expression(text(node), constants)

        def gap(node, expression):
            rows.append(row('/', path, adapter_name, kind='gap', gap='unresolved-route-path', expression=expression[:120],
                            line=node.start_point.row + 1 + offset, declared_in=path))
        text = lambda n: data[n.start_byte:n.end_byte].decode('utf-8', 'replace')
        imports = {}
        for match in re.finditer(r'''import\s+(?:(\w+)|\{([^}]+)\})\s+from\s+['"]([^'"]+)['"]''', code):
            names = [match.group(1)] if match.group(1) else [n.split(' as ')[-1].strip() for n in match.group(2).split(',')]
            for name in names:
                imports[name] = match.group(3)
        lazies = {m.group(1): m.group(2) for m in re.finditer(
            r'''(?:const|let)\s+(\w+)\s*=\s*(?:React\.)?lazy\s*\(\s*\(\)\s*=>\s*import\s*\(\s*['"]([^'"]+)['"]''', code)}

        def component_file(value):
            if value is None:
                return None
            raw = text(value)
            spec = None
            dynamic = re.search(r'''import\s*\(\s*['"]([^'"]+)['"]''', raw)
            if dynamic:
                spec = dynamic.group(1)
            else:
                name = re.search(r'<\s*([A-Z][\w.]*)', raw) or re.fullmatch(r'\s*([A-Z]\w*)\s*', raw)
                if name:
                    head = name.group(1).split('.')[0]
                    spec = lazies.get(head) or imports.get(head)
                    if spec is None:
                        return path  # same-file component
            return resolve_module(path, spec, files, aliases) if spec else None

        def prop(obj, name):
            for pair in obj.named_children:
                if pair.type == 'pair':
                    key = pair.child_by_field_name('key')
                    if key is not None and text(key).strip('\'"') == name:
                        return pair.child_by_field_name('value')
            return None

        def visit_object(obj, prefix):
            path_node = prop(obj, 'path')
            children = prop(obj, 'children')
            index_node = prop(obj, 'index')
            here = prefix
            if path_node is not None:
                segment = route_value(path_node)
                if segment is None:
                    gap(path_node, text(path_node))  # recorded, not silently dropped
                    return
                here = segment if segment.startswith('/') else (prefix.rstrip('/') + '/' + segment)
            elif index_node is None and children is None:
                return
            target = component_file(prop(obj, 'element') or prop(obj, 'component') or prop(obj, 'Component')
                                    or prop(obj, 'loadComponent') or prop(obj, 'lazy'))
            redirect = prop(obj, 'redirect') or prop(obj, 'redirectTo') or prop(obj, 'loader')
            if (path_node is not None or index_node is not None) and target and not (redirect is not None and target is None):
                if '**' not in here and here != '*':
                    rows.append(row(here or '/', target, adapter_name, line=obj.start_point.row + 1 + offset, declared_in=path))
            if children is not None and children.type == 'array':
                for child in children.named_children:
                    if child.type == 'object':
                        visit_object(child, here or '/')

        def visit_jsx(node, prefix):
            opening = node.child_by_field_name('open_tag') if node.type == 'jsx_element' else node
            name = opening.child_by_field_name('name') if opening is not None else None
            here = prefix
            if name is not None and text(name) == 'Route':
                attrs = {}
                for attribute in opening.named_children:
                    if attribute.type == 'jsx_attribute' and attribute.named_children:
                        key = text(attribute.named_children[0])
                        attrs[key] = attribute.named_children[1] if len(attribute.named_children) > 1 else None
                path_value = attrs.get('path')
                if path_value is not None:
                    inner = path_value.named_children[0] if path_value.type == 'jsx_expression' and path_value.named_children else path_value
                    segment = route_value(inner)
                    if segment is None:
                        gap(path_value, text(path_value))
                        path_value = None
                    else:
                        here = segment if segment.startswith('/') else prefix.rstrip('/') + '/' + segment
                target = component_file(attrs.get('element') or attrs.get('component') or attrs.get('Component'))
                if (path_value is not None or 'index' in attrs) and target and '*' not in here:
                    rows.append(row(here or '/', target, adapter_name, line=opening.start_point.row + 1 + offset, declared_in=path))
            for child in node.named_children:
                if child.type in {'jsx_element', 'jsx_self_closing_element'}:
                    visit_jsx(child, here)
                else:
                    walk(child, here)

        def walk(node, prefix='/'):
            if node.type in {'jsx_element', 'jsx_self_closing_element'}:
                visit_jsx(node, prefix)
                return
            if node.type == 'array':
                objects = [c for c in node.named_children if c.type == 'object']
                # A route table, not any array of {path}: `{path: '/api/v1'}` alone is not a route.
                if objects and any(prop(o, 'path') is not None for o in objects) and any(
                        prop(o, key) is not None for o in objects for key in ROUTER_KEYS):
                    for obj in objects:
                        visit_object(obj, prefix)
                    return
            for child in node.named_children:
                walk(child, prefix)
        walk(tree)
    return rows


# ---------------------------------------------------------------- route constants (path: P.ABOUT)
STRING_CONST = re.compile(r'''(?:export\s+)?(?:const|let|var)\s+(\w+)\s*(?::\s*[\w<>\[\]|]+\s*)?=\s*(['"`])([^'"`$]*)\2''')
OBJECT_CONST = re.compile(r'''(?:export\s+)?(?:const|let|var)\s+(\w+)\s*(?::\s*[\w<>\[\]|.]+\s*)?=\s*(?=\{)|export\s+default\s+(?=\{)''')
ENUM_CONST = re.compile(r'''(?:export\s+)?(?:const\s+)?enum\s+(\w+)\s*(?=\{)''')


def _object_strings(body, prefix, out, depth=0):
    """{KEY: '/x', NESTED: {A: '/y'}} -> out['P.KEY'], out['P.NESTED.A'] (one level of nesting is plenty)."""
    flat = top_level(body)
    for match in re.finditer(r'''(?:^|[{,\s])['"]?(\w+)['"]?\s*[:=]\s*(['"`])([^'"`$]*)\2''', flat):
        out[prefix + '.' + match.group(1)] = match.group(3)
    if depth < 2:
        for match in re.finditer(r'''(?:^|[{,\s])['"]?(\w+)['"]?\s*:\s*(?=\{)''', flat):
            start = match.end()
            _object_strings(body[start:balanced(body, start)], prefix + '.' + match.group(1), out, depth + 1)


def module_constants(path, files, aliases, depth=0, _seen=None):
    """String constants visible in a module: same-file and (one hop of) imported const/object/enum literals."""
    seen = _seen if _seen is not None else set()
    if path in seen or path not in files or depth > 2:
        return {}
    seen.add(path)
    source = files[path]
    out = {m.group(1): m.group(3) for m in STRING_CONST.finditer(source)}
    for match in OBJECT_CONST.finditer(source):
        start = match.end()
        _object_strings(source[start:balanced(source, start)], match.group(1) or 'default', out)
    for match in ENUM_CONST.finditer(source):
        start = match.end()
        _object_strings(source[start:balanced(source, start)], match.group(1), out)
    for match in re.finditer(r'''import\s+(?:(\w+)\s*,?\s*)?(?:\{([^}]+)\}|\*\s+as\s+(\w+))?\s*from\s+['"]([^'"]+)['"]''', source):
        default, named, namespace, spec = match.groups()
        target = resolve_module(path, spec, files, aliases)
        if not target or target == path:
            continue
        theirs = module_constants(target, files, aliases, depth + 1, seen)
        if default:
            for key, value in theirs.items():
                if key.startswith('default.'):
                    out[default + key[len('default'):]] = value
        if namespace:
            for key, value in theirs.items():
                out[namespace + '.' + key] = value
        for part in (named or '').split(','):
            part = part.strip()
            if not part:
                continue
            original, _, local = part.partition(' as ')
            original, local = original.strip(), (local or original).strip()
            for key, value in theirs.items():
                if key == original or key.startswith(original + '.'):
                    out[local + key[len(original):]] = value
    return out


def resolve_route_expression(expression, constants):
    """'P.ABOUT' / ABOUT / `${P.BLOG}/:slug` / P.BLOG + '/:slug' -> '/about' etc.; None when unresolvable."""
    expression = expression.strip().strip('{}').strip()
    if len(expression) >= 2 and expression[0] == expression[-1] and expression[0] in '"\'' :
        return expression[1:-1]
    if expression.startswith('`') and expression.endswith('`'):
        missing = []

        def sub(match):
            value = constants.get(match.group(1).strip())
            if value is None:
                missing.append(match.group(1))
            return value or ''
        value = re.sub(r'\$\{([^}]+)\}', sub, expression[1:-1])
        return None if missing else value
    parts = [p.strip() for p in expression.split('+')]
    values = []
    for part in parts:
        if len(part) >= 2 and part[0] == part[-1] and part[0] in '"\'`':
            values.append(part[1:-1])
        elif part in constants:
            values.append(constants[part])
        else:
            return None
    return ''.join(values)


# ---------------------------------------------------------------- pathname-switch SPAs (no router library)
DISPATCH_SOURCE = re.compile(r'''\b(?:window\.)?location\.pathname\b|\busePathname\s*\(\s*\)|\buseLocation\s*\(\s*\)\s*\.pathname|\$page\.url\.pathname|\brouter\.(?:pathname|asPath)\b''')


def _dispatch_names(source):
    names = set()
    for match in re.finditer(r'''(?:const|let|var)\s+(\w+)\s*=\s*([^;\n]*)''', source):
        if DISPATCH_SOURCE.search(match.group(2)):
            names.add(match.group(1))
    for match in re.finditer(r'''(?:const|let|var)\s*\{\s*([^}]*)\}\s*=\s*(?:useLocation\s*\(\s*\)|window\.location|location)''', source):
        for name in match.group(1).split(','):
            original, _, local = name.partition(':')
            if original.strip() == 'pathname':
                names.add((local or original).strip())
    return names


def _component_after(source, offset, path, files, aliases, imports):
    window = source[offset:offset + 160]
    match = re.match(r'''\s*(?:\)\s*)?(?:\?|&&|:|\breturn\b|=>)\s*\(?\s*<\s*([A-Z][\w.]*)''', window)
    if not match:
        return None
    name = match.group(1).split('.')[0]
    spec = imports.get(name)
    return (resolve_module(path, spec, files, aliases) if spec else path), name


def pathname_routes(files):
    """Routes from `path === '/about' ? <About/>`, `switch (pathname) { case '/x': }`, `path.startsWith('/blog/')`.

    PR #8 class of site: no router dependency, the app compares window.location.pathname. Each literal
    comparison is a static route; a startsWith/regex prefix becomes a `/prefix/:slug` template. A
    trailing fallback branch that renders "not found" is reported as a catch-all (soft-404 risk)."""
    aliases = load_aliases(files)
    rows = []
    for path in sorted(files):
        if PurePosixPath(path).suffix not in {'.js', '.jsx', '.ts', '.tsx', '.mjs', '.vue', '.svelte'} or TEST_PATH.search(path) or 'node_modules' in path:
            continue
        source = files[path]
        if not DISPATCH_SOURCE.search(source):
            continue
        names = _dispatch_names(source)
        subjects = '|'.join([re.escape(n) for n in sorted(names)] + [r'(?:window\.)?location\.pathname'])
        imports = {}
        for match in re.finditer(r'''import\s+(?:(\w+)|\{([^}]+)\})\s+from\s+['"]([^'"]+)['"]''', source):
            for name in ([match.group(1)] if match.group(1) else [n.split(' as ')[-1].strip() for n in match.group(2).split(',')]):
                imports[name] = match.group(3)
        lazies = re.finditer(r'''(?:const|let)\s+(\w+)\s*=\s*(?:React\.)?lazy\s*\(\s*\(\)\s*=>\s*import\s*\(\s*['"]([^'"]+)['"]''', source)
        imports.update({m.group(1): m.group(2) for m in lazies})
        line = lambda offset: source.count('\n', 0, offset) + 1
        found = []
        for match in re.finditer(r'''(?:\b(?:''' + subjects + r''')\s*===?\s*(['"`])(/[^'"`]*)\1|(['"`])(/[^'"`]*)\3\s*===?\s*(?:''' + subjects + r'''))''', source):
            url = match.group(2) or match.group(4)
            found.append((url, match.start(), _component_after(source, match.end(), path, files, aliases, imports), False))
        for switch in re.finditer(r'''\bswitch\s*\(\s*(?:''' + subjects + r''')\s*\)\s*\{''', source):
            body_end = balanced(source, switch.end() - 1)
            for case in re.finditer(r'''\bcase\s+(['"`])(/[^'"`]*)\1\s*:''', source[switch.end():body_end]):
                offset = switch.end() + case.end()
                found.append((case.group(2), switch.end() + case.start(), _component_after(source, offset, path, files, aliases, imports), False))
            if re.search(r'\bdefault\s*:', source[switch.end():body_end]):
                rows.append(row('/*', path, 'pathname-switch', kind='not-found', catch_all=True, line=line(switch.start())))
        for match in re.finditer(r'''\b(?:''' + subjects + r''')\s*\.\s*startsWith\s*\(\s*(['"`])(/[^'"`]+)\1\s*\)''', source):
            prefix = match.group(2)
            if prefix.endswith('/'):
                found.append((prefix.rstrip('/') + '/:slug', match.start(), _component_after(source, match.end(), path, files, aliases, imports), True))
        for match in re.finditer(r'''\b(?:''' + subjects + r''')\s*\.\s*match\s*\(\s*/\^((?:\\/[\w\-]+)+)\\/''', source):
            found.append((match.group(1).replace('\\/', '/') + '/:slug', match.start(), None, True))
        for url, offset, target, dynamic in found:
            if re.search(r'\.(?:\w{2,4})$', url) and not url.endswith(('.html', '.htm')):
                continue  # asset paths compared against pathname are not pages
            file, component = target if target else (path, None)
            extra = {'entry_component': component} if component else {}
            rows.append(row(url, file or path, 'pathname-switch', line=line(offset), declared_in=path, **extra))
        if found and re.search(r'''(?i)not\s*found|\b404\b|NotFound''', source):
            rows.append(row('/*', path, 'pathname-switch', kind='not-found', catch_all=True,
                            line=line(found[-1][1]),
                            note='Unknown paths render a client-side "not found" view with HTTP 200 (soft 404) unless the host returns 404.'))
    return rows


NAV_ARRAY = re.compile(r'''(?:const|let|var)\s+(\w+)\s*(?::\s*[\w<>\[\]{}:;,\s]+?)?=\s*(?=\[)''')
NAV_KEY = re.compile(r'''\b(?:href|path|to|url|link|route|slug)\s*:\s*(['"`])(/[^'"`\s]*)\1''')


def nav_literal_routes(files):
    """Internal URLs from nav arrays (`[{label:'About', href:'/about'}]`) that a component maps into
    <a href>/<Link to>. These are seeds (PR #8 PageRail.jsx), confirmed or refuted by the crawl."""
    rows = []
    for path in sorted(files):
        if PurePosixPath(path).suffix not in {'.js', '.jsx', '.ts', '.tsx', '.mjs', '.vue', '.svelte', '.astro'} or TEST_PATH.search(path) or 'node_modules' in path:
            continue
        source = files[path]
        if not re.search(r'''\bhref\s*=\s*\{|\bto\s*=\s*\{|:href\s*=|:to\s*=|href=\{[^}]*\}''', source):
            continue
        for match in NAV_ARRAY.finditer(source):
            name = match.group(1)
            if not re.search(r'\b' + re.escape(name) + r'\s*\.\s*map\s*\(|v-for\s*=\s*"[^"]*\bin\s+' + re.escape(name) + r'\b|\{#each\s+' + re.escape(name) + r'\b', source):
                continue
            start = match.end()
            body = source[start:balanced(source, start, '[]')]
            for key in NAV_KEY.finditer(body):
                url = key.group(2).split('#', 1)[0].split('?', 1)[0]
                if not url or url.startswith('//') or re.search(r'\.(?:png|jpe?g|gif|svg|webp|pdf|ico|xml|txt|json|js|css)$', url, re.I):
                    continue
                rows.append(row(url, None, 'nav-literal', line=source.count('\n', 0, start + key.start()) + 1,
                                declared_in=path, discovered_by='nav-literal'))
    return rows


MAPPED_LINK = re.compile(r'''(\w+)\s*\.\s*map\s*\(\s*(?:async\s*)?\(?\s*(\w+)\s*(?:,\s*\w+\s*)?\)?\s*=>''')


def data_link_instances(files):
    """Concrete URLs from links built over local data: `posts.map(p => <a href={`/blog/${p.slug}`}>)` with `posts`
    imported from JSON or declared as a literal array. These instantiate dynamic templates (/blog/:slug)."""
    aliases = None
    rows = []
    for path in sorted(files):
        if PurePosixPath(path).suffix not in {'.js', '.jsx', '.ts', '.tsx', '.vue', '.svelte', '.astro'} or TEST_PATH.search(path) or 'node_modules' in path:
            continue
        source = files[path]
        if '${' not in source or '.map' not in source:
            continue
        for mapped in MAPPED_LINK.finditer(source):
            array, item = mapped.group(1), mapped.group(2)
            window = source[mapped.end():mapped.end() + 600]
            link = re.search(r'''(?:href|to|:href|:to)\s*=\s*\{?\s*`(/[^`$]*)\$\{\s*''' + re.escape(item) + r'''\.(\w+)\s*\}([^`]*)`''', window)
            if not link:
                continue
            if aliases is None:
                aliases = load_aliases(files)
            for value in _array_field_values(files, path, array, link.group(2), aliases)[:500]:
                url = link.group(1) + value + link.group(3)
                rows.append(row(url, None, 'data-link', line=source.count('\n', 0, mapped.end() + link.start()) + 1,
                                declared_in=path, discovered_by='data-link'))
    return rows


ANGULAR_TEMPLATE = re.compile(r'''templateUrl\s*:\s*['"]([^'"]+)['"]''')


def angular_template(files, component_file):
    if component_file is None or not component_file.endswith('.ts'):
        return component_file
    match = ANGULAR_TEMPLATE.search(files.get(component_file, ''))
    if match:
        candidate = posixpath.normpath(posixpath.join(posixpath.dirname(component_file), match.group(1)))
        if candidate in files:
            return candidate
    return component_file


ADAPTERS = {
    'next': next_routes, 'nuxt': nuxt_routes, 'sveltekit': sveltekit_routes, 'astro': astro_routes,
    'remix': remix_routes, 'gatsby': gatsby_routes, 'docusaurus': lambda f: docs_routes(f) + file_routes(f, 'src/pages/', {'.js', '.jsx', '.tsx', '.md', '.mdx'}, 'docusaurus-pages'),
    'vitepress': lambda f: docs_routes(f, 'docs/', 'vitepress') or docs_routes(f, '', 'vitepress'),
    'eleventy': eleventy_routes, 'jekyll': jekyll_routes, 'hugo': hugo_routes, 'shopify-theme': shopify_routes,
    'wordpress-theme': wordpress_routes, 'wordpress-block-theme': wordpress_routes, 'ghost-theme': ghost_routes,
    'django': django_routes, 'flask': flask_routes, 'fastapi': flask_routes, 'laravel': laravel_routes, 'rails': rails_routes,
    'express': express_routes, 'aspnet': razor_routes, 'static-html': static_routes,
    'react-spa': spa_routes, 'vue-spa': spa_routes, 'svelte-spa': spa_routes, 'preact-spa': spa_routes,
    'solid-start': lambda f: file_routes(f, 'src/routes/', {'.tsx', '.jsx'}, 'solid-start'),
    'angular': lambda f: [{**r, 'template': angular_template(f, r['template'])} for r in spa_routes(f, 'angular-router')],
    'qwik': lambda f: file_routes(f, 'src/routes/', {'.tsx', '.mdx', '.md'}, 'qwik-city'),
}


SPA_FRAMEWORKS = {'react-spa', 'vue-spa', 'svelte-spa', 'preact-spa', 'angular'}
NON_PAGE_KINDS = {'gap', 'not-found'}


def _adapter_name(adapter, framework):
    return getattr(adapter, '__name__', None) if getattr(adapter, '__name__', '<lambda>') != '<lambda>' else framework + '_routes'


def _site_rows(files, framework):
    adapter = ADAPTERS.get(framework)
    if adapter is None:
        return [], None
    rows = list(adapter(files))
    if framework in SPA_FRAMEWORKS:
        # Router-less SPAs dispatch on location.pathname; nav arrays seed the crawl either way.
        rows += pathname_routes(files)
        rows += nav_literal_routes(files)
    if framework in SPA_FRAMEWORKS or ADAPTERS.get(framework) in {next_routes, astro_routes, sveltekit_routes, nuxt_routes, remix_routes, gatsby_routes}:
        rows += data_link_instances(files)
    if framework in SPA_FRAMEWORKS:
        if not any(r['kind'] not in NON_PAGE_KINDS and r['adapter'] != 'nav-literal' for r in rows):
            entry = next((p for p in ('index.html', 'public/index.html', 'src/App.tsx', 'src/App.jsx', 'src/App.vue', 'src/App.svelte')
                          if p in files), None)
            if entry:
                rows.insert(0, row('/', entry, 'spa-single-entry'))
    return rows, _adapter_name(adapter, framework)


def _sites(fingerprint):
    """[(root, [framework, ...])]: every framework above threshold, per package root; the primary always runs."""
    primary = fingerprint.get('primary')
    sites = []
    for site in fingerprint.get('sites') or []:
        names = [n for n in (site.get('frameworks') or [site.get('primary')]) if n in ADAPTERS]
        if names:
            sites.append((site.get('root') or '', names))
    if primary in ADAPTERS and not any(primary in names for _, names in sites):
        sites.insert(0, ('', [primary]))
    # The primary framework's site first so its templates win URL collisions.
    sites.sort(key=lambda item: (primary not in item[1], item[0].count('/') if item[0] else -1, item[0]))
    return sites


def adapter_routes(files, fingerprint):
    """Rows from every detected framework's adapter (per package root in monorepos), de-duplicated.

    Returns (rows, adapter_name). Non-page rows are kept with their kind: `gap` (route the adapter saw but
    could not resolve, e.g. `path: ROUTES[x]`) and `not-found` (catch-all / client-side 404 view)."""
    names, rows = [], []
    for root, frameworks in _sites(fingerprint):
        prefix = root + '/' if root else ''
        sub = {p[len(prefix):]: t for p, t in files.items() if p.startswith(prefix)} if prefix else files
        for framework in frameworks:
            site_rows, name = _site_rows(sub, framework)
            if name and site_rows:
                names.append(name if not root else f'{name}@{root}')
            for item in site_rows:
                if prefix:
                    item = {**item, 'template': prefix + item['template'] if item.get('template') else item.get('template'),
                            'site_root': root}
                    if item.get('declared_in'):
                        item['declared_in'] = prefix + item['declared_in']
                rows.append(item)
    seen, unique = set(), []
    for item in rows:
        key = (item['url'], item['template'], item['kind'], item.get('line') if item['kind'] == 'gap' else None)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    if not names:
        primary = fingerprint.get('primary')
        return unique, (_adapter_name(ADAPTERS[primary], primary) if primary in ADAPTERS else None)
    return unique, '+'.join(dict.fromkeys(names))


GENERATOR_FUNCTION = re.compile(r'''(?:export\s+)?(?:async\s+)?function\s+(generateStaticParams|getStaticPaths|entries|getStaticProps)\s*\(|'''
                                r'''(?:export\s+)?const\s+(generateStaticParams|getStaticPaths|entries|staticPaths)\s*(?::[^=]+)?=\s*(?:async\s*)?\([^)]*\)\s*=>\s*''')


def _generator_bodies(source):
    for match in GENERATOR_FUNCTION.finditer(source):
        start = source.find('{', match.end() - 1) if match.group(1) else match.end()
        if start < 0:
            continue
        if source[start] == '{':
            yield source[start:balanced(source, start)]
        elif source[start] == '(':
            yield source[start:balanced(source, start, '()')]
        else:  # concise arrow body: up to the end of the statement
            end = source.find('\n\n', start)
            yield source[start:end if end > 0 else len(source)]


def _array_field_values(files, template, array_name, field_name, aliases):
    """Values of `field` for each item of an array imported from JSON/a module, or declared in the same file."""
    import json
    source = files.get(template, '')
    spec = re.search(r'''import\s+(?:\{[^}]*\b''' + re.escape(array_name) + r'''\b[^}]*\}|''' + re.escape(array_name) +
                     r''')\s+from\s+['"]([^'"]+)['"]|(?:const|let|var)\s+''' + re.escape(array_name) +
                     r'''\s*=\s*require\s*\(\s*['"]([^'"]+)['"]''', source)
    candidates = []
    if spec:
        target = resolve_module(template, spec.group(1) or spec.group(2), files, aliases)
        if target and target.endswith('.json'):
            try:
                data = json.loads(files[target])
            except (KeyError, ValueError):
                data = None
            if isinstance(data, dict):  # {posts: [...]} or a keyed map
                data = data.get(array_name) or next((v for v in data.values() if isinstance(v, list)), None)
            if isinstance(data, list):
                return [str(item[field_name]) for item in data if isinstance(item, dict) and isinstance(item.get(field_name), (str, int))]
            return []
        if target:
            candidates.append(files.get(target, ''))
    candidates.append(source)
    for text in candidates:
        declared = re.search(r'''(?:export\s+)?(?:const|let|var)\s+''' + re.escape(array_name) + r'''\s*(?::[^=]+)?=\s*(?=\[)''', text)
        if declared:
            body = text[declared.end():balanced(text, declared.end(), '[]')]
            return re.findall(r'''\b''' + re.escape(field_name) + r'''\s*:\s*['"]([\w\-.~%]+)['"]''', body)
    return []


def static_params(files, template, pattern):
    """Concrete instances for a dynamic route from its static-params generator.

    Only literals inside generateStaticParams/getStaticPaths/entries count (a `slug: 'x'` elsewhere in the
    file is not a page); `posts.map(p => ({slug: p.slug}))` reads the imported/declared array or JSON."""
    names = re.findall(r'\[(?:\.\.\.)?(\w+)\]|:(\w+)|\{(\w+)\}', pattern)
    names = [next(n for n in group if n) for group in names]
    if len(names) != 1:
        return []
    name = names[0]
    source = files.get(template, '')
    if not re.search(r'generateStaticParams|getStaticPaths|entries|staticPaths', source):
        return []
    aliases = None
    seen = []
    for body in _generator_bodies(source):
        values = re.findall(r'''\b''' + re.escape(name) + r'''\s*:\s*['"]([\w\-.~%]+)['"]''', body)
        for mapped in re.finditer(r'''(\w+)(?:\.\w+)*\s*\.\s*map\s*\(\s*(?:async\s*)?\(?\s*(\w+)\s*\)?\s*=>\s*\(?\s*\{[^}]*?\b''' + re.escape(name) +
                                  r'''\s*:\s*\2\.(\w+)''', body):
            if aliases is None:
                aliases = load_aliases(files)
            values += _array_field_values(files, template, mapped.group(1), mapped.group(3), aliases)
        for mapped in re.finditer(r'''(\w+)\s*\.\s*map\s*\(\s*\(?\s*\{\s*(\w+)\s*\}\s*\)?\s*=>\s*\(?\s*\{[^}]*?\b''' + re.escape(name) + r'''\b''', body):
            if aliases is None:
                aliases = load_aliases(files)
            values += _array_field_values(files, template, mapped.group(1), mapped.group(2), aliases)
        for value in values:
            if value not in seen:
                seen.append(value)
    return seen[:500]


def content_instances(files, pattern, template):
    """Instances for `/<prefix>/[slug]` from a matching content folder (md/mdx/json)."""
    match = re.fullmatch(r'(/[\w\-/]*?)/(?:\[(?:\.\.\.)?\w+\]|:\w+|\{\w+\})', pattern)
    if not match:
        return []
    prefix = match.group(1).strip('/')
    if not prefix:
        return []
    folders = [f'src/content/{prefix}/', f'content/{prefix}/', f'{prefix}/', f'_{prefix}/', f'src/{prefix}/', f'data/{prefix}/']
    instances = []
    for path in sorted(files):
        for folder in folders:
            if path.startswith(folder) and PurePosixPath(path).suffix in {'.md', '.mdx', '.markdown'} and path != template:
                stem = PurePosixPath(path).stem
                if stem in {'index', '_index', 'README'}:
                    continue
                slug = front_matter(files[path]).get('slug') or re.sub(r'^\d{4}-\d{2}-\d{2}-', '', stem)
                instances.append((slug, path))
                break
    return instances[:500]


# ---------------------------------------------------------------- uniform adapter interface (doc §15)
LAYOUT_PATTERNS = {
    'next': [r'(?:^|/)app/(?:.*/)?layout\.[jt]sx?$', r'(?:^|/)pages/_(?:app|document)\.[jt]sx?$'],
    'nuxt': [r'(?:^|/)layouts/[^/]+\.vue$', r'(?:^|/)app\.vue$'],
    'sveltekit': [r'(?:^|/)src/routes/(?:.*/)?\+layout\.svelte$', r'(?:^|/)src/app\.html$'],
    'astro': [r'(?:^|/)src/layouts/[^/]+\.astro$'],
    'remix': [r'(?:^|/)app/root\.[jt]sx?$'],
    'gatsby': [r'(?:^|/)src/components/(?:layout|seo)\.[jt]sx?$'],
    'hugo': [r'(?:^|/)layouts/_default/baseof\.html$', r'(?:^|/)layouts/partials/head[^/]*\.html$'],
    'jekyll': [r'^_layouts/[^/]+\.html$', r'^_includes/head[^/]*\.html$'],
    'eleventy': [r'(?:^|/)_includes/(?:layouts/)?[^/]+\.(?:njk|liquid|html)$'],
    'shopify-theme': [r'(?:^|/)layout/theme\.liquid$', r'(?:^|/)snippets/meta-tags\.liquid$'],
    'wordpress-theme': [r'(?:^|/)header\.php$', r'(?:^|/)footer\.php$', r'(?:^|/)functions\.php$'],
    'wordpress-block-theme': [r'(?:^|/)parts/header\.html$', r'(?:^|/)templates/[^/]+\.html$'],
    'django': [r'(?:^|/)templates/(?:base|layout)[^/]*\.html$'], 'flask': [r'(?:^|/)templates/(?:base|layout)[^/]*\.html$'],
    'laravel': [r'^resources/views/(?:layouts|components)/[^/]+\.blade\.php$'],
    'rails': [r'^app/views/layouts/[^/]+$'], 'express': [r'(?:^|/)views/(?:layout|partials/head)[^/]*$'],
    'static-html': [], 'react-spa': [r'^index\.html$', r'(?:^|/)src/App\.[jt]sx?$'], 'vue-spa': [r'^index\.html$', r'(?:^|/)src/App\.vue$'],
    'svelte-spa': [r'^index\.html$', r'(?:^|/)src/App\.svelte$'], 'preact-spa': [r'^index\.html$'], 'angular': [r'(?:^|/)src/index\.html$'],
}
METADATA_DECLARATIONS = re.compile(
    r'''export\s+(?:const\s+metadata\b|(?:async\s+)?function\s+generateMetadata\b|const\s+meta\b|function\s+meta\b|const\s+Head\b|function\s+Head\b)|'''
    r'''\buse(?:SeoMeta|Head|ServerSeoMeta|Meta)\s*\(|<svelte:head>|<Head\b|<Helmet\b|<title\b|<meta\s[^>]*\b(?:name|property)=|'''
    r'''<link\s[^>]*rel=["']canonical|\bdocument\.title\s*=|\{%-?\s*block\s+(?:title|meta|head)|@(?:section|yield)\s*\(\s*['"](?:title|meta)|'''
    r'''content_for\s*[:(]\s*:?title|\{%-?\s*seo\s*%\}|wp_head\s*\(|add_theme_support\s*\(\s*['"]title-tag|\{\{\s*(?:page_title|ghost_head)|'''
    r'''^(?:title|description)\s*[:=]''', re.M)


def layout_files(files, framework, template=None):
    """Shared layout/shell files that wrap `template` (head_scope / shared_by). Nearest-first for nested layouts."""
    patterns = [re.compile(p) for p in LAYOUT_PATTERNS.get(framework, [])]
    found = [p for p in sorted(files) if any(r.search(p) for r in patterns)]
    if template and framework in {'next', 'sveltekit'}:
        directory = posixpath.dirname(template)
        found = [p for p in found if not re.search(r'layout|\+layout', p) or directory.startswith(posixpath.dirname(p))]
        found.sort(key=lambda p: -len(posixpath.dirname(p)))
    return found


def metadata_declarations(files, file):
    """Head/metadata declarations in one file with line and column spans: [{kind, line, col, end_col, text}]."""
    source = files.get(file, '')
    out = []
    for match in METADATA_DECLARATIONS.finditer(source):
        line = source.count('\n', 0, match.start()) + 1
        col = match.start() - (source.rfind('\n', 0, match.start()) + 1) + 1
        token = match.group(0).strip()
        kind = ('metadata-export' if token.startswith('export') else 'head-composable' if token.startswith('use')
                else 'front-matter' if re.match(r'^(?:title|description)\s*[:=]', token) else 'template-block' if token.startswith(('{%', '@', 'content_for'))
                else 'runtime' if token.startswith('document.title') else 'platform-hook' if token.startswith(('wp_head', 'add_theme_support', '{{'))
                else 'head-element')
        out.append({'kind': kind, 'line': line, 'col': col, 'end_col': col + len(match.group(0)), 'text': token[:80]})
    return out


def build_plan(fingerprint):
    """Install/build/serve recipe per stack (data only; runtime/build_serve executes it, without secrets)."""
    framework = (fingerprint or {}).get('primary')
    manager = (fingerprint or {}).get('package_manager') or 'npm'
    install = {'npm': ['npm', 'ci'], 'pnpm': ['pnpm', 'install', '--frozen-lockfile'], 'bun': ['bun', 'install', '--frozen-lockfile'],
               'yarn': ['yarn', 'install', '--immutable']}.get(manager, ['npm', 'install'])
    serve = {'next': {'command': ['npx', 'next', 'start', '-p', '{port}'], 'port_flag': '-p'},
             'remix': {'command': ['npm', 'run', 'start'], 'port_env': 'PORT'},
             'nuxt': {'command': ['node', '.output/server/index.mjs'], 'port_env': 'PORT'},
             'sveltekit': {'command': ['npx', 'vite', 'preview', '--port', '{port}', '--strictPort'], 'port_flag': '--port'},
             'astro': {'command': ['npx', 'astro', 'preview', '--port', '{port}'], 'port_flag': '--port'},
             'gatsby': {'command': ['npx', 'gatsby', 'serve', '-p', '{port}'], 'port_flag': '-p'}}.get(framework)
    if serve is None and framework in {'react-spa', 'vue-spa', 'svelte-spa', 'preact-spa', 'solid-start', 'qwik'}:
        serve = {'command': ['npx', 'vite', 'preview', '--port', '{port}', '--strictPort'], 'port_flag': '--port'}
    return {'framework': framework, 'install': install if (fingerprint or {}).get('package_json') else None,
            'install_cwd': 'workspace-root', 'build': ((fingerprint or {}).get('build') or {}).get('command'),
            'output_dirs': ((fingerprint or {}).get('build') or {}).get('output_dirs') or [],
            'serve': serve or {'command': 'static-file-server', 'port_flag': None},
            'readiness': '2xx on "/" (a 5xx is not up)', 'notes': ['Respect packageManager/corepack and .nvmrc / engines.node.']}


class Adapter:
    """detect / routes / layouts / metadata / head_recipe / build_plan for one framework (doc §15)."""

    def __init__(self, name):
        self.name = name

    def detect(self, snapshot):
        from seo_agents.repo_analysis.tools.repository.framework_detection import detect_frameworks
        fingerprint = detect_frameworks(snapshot)
        return next((c['score'] for c in fingerprint.get('candidates', []) if c['name'] == self.name), 0)

    def routes(self, snapshot):
        rows, _ = adapter_routes(snapshot['files'], {'primary': self.name})
        return rows

    def layouts(self, snapshot, template=None):
        return layout_files(snapshot['files'], self.name, template)

    def metadata(self, snapshot, file):
        return metadata_declarations(snapshot['files'], file)

    def head_recipe(self, fingerprint=None):
        from seo_agents.repo_analysis.tools.routing.head_recipes import head_recipe
        return head_recipe({**(fingerprint or {}), 'primary': self.name})

    def build_plan(self, fingerprint=None):
        return build_plan({**(fingerprint or {}), 'primary': self.name})


REGISTRY = {name: Adapter(name) for name in ADAPTERS}


def get_adapter(name):
    return REGISTRY.get(name)


class FrameworkRouteAdapters:
    """Tool: framework route adapters (file routes, router configs, front matter, server routes) -> URL rows."""

    name = 'framework_route_adapters'

    def routes(self, files, fingerprint):
        return adapter_routes(files, fingerprint)

    def adapter(self, name):
        return get_adapter(name)
