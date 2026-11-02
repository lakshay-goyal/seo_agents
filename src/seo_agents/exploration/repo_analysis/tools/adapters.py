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

from seo_agents.exploration.repo_analysis.tools.element_index import load_aliases, resolve_module

DYNAMIC = re.compile(r'\[[^\]]+\]|(?:^|/):\w+|\{[^}]+\}|<[^>]+>|\*')
TEST_PATH = re.compile(r'(?:^|/)(?:tests?|__tests__|fixtures|spec|e2e|cypress|playwright|stories|node_modules)/|\.(?:test|spec|stories)\.')


def row(url, template, adapter, kind='page', **extra):
    url = '/' + url.strip('/') if url not in ('', '/') else '/'
    url = re.sub(r'/{2,}', '/', url)
    return {'url': url, 'template': template, 'kind': kind, 'adapter': adapter,
            'is_dynamic': bool(DYNAMIC.search(url)), **extra}


def strip_segments(parts, *, groups=True, private=True):
    kept = []
    for part in parts:
        if groups and re.fullmatch(r'\(.*\)', part):
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
        for app in (root + 'app/', root + 'src/app/'):
            for r in file_routes(files, app, {'.tsx', '.jsx', '.ts', '.js', '.mdx', '.md'}, 'next-app', page_file='page'):
                rows.append({**r, 'url': (base_path + r['url']).rstrip('/') or '/'})
        for pages in (root + 'pages/', root + 'src/pages/'):
            for r in file_routes(files, pages, {'.tsx', '.jsx', '.ts', '.js', '.mdx', '.md'}, 'next-pages',
                                 skip=(r'_app|_document|_error|api(?:/.*)?|404|500|_middleware',)):
                if not r['template'].startswith(app):
                    rows.append({**r, 'url': (base_path + r['url']).rstrip('/') or '/'})
    return rows


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
    rows = []
    for path in sorted(files):
        match = re.match(r'^app/routes/([^/]+?)(?:/route)?\.(?:tsx|jsx|ts|js|mdx)$', path)
        if not match or match.group(1).startswith('api.') or match.group(1).endswith('[.]xml'):
            continue
        segments = []
        for segment in re.split(r'\.(?![^\[]*\])', match.group(1)):
            optional = bool(re.fullmatch(r'\(.+\)', segment))
            segment = segment[1:-1] if optional else segment
            if segment == '_index' or (segment.startswith('_') and len(segment) > 1):
                continue  # index route / pathless layout
            segment = segment.rstrip('_') or segment  # `blog_` opts out of the parent layout, not the URL
            segment = re.sub(r'\[(.+?)\]', r'\1', segment)  # escaped literal: [.]xml
            if segment.startswith('$'):
                segment = ':' + segment[1:] if segment != '$' else '*'
            segments.append(segment + ('?' if optional else ''))
        rows.append(row('/'.join(segments), path, 'remix-flat-routes'))
    return rows


def gatsby_routes(files):
    rows = file_routes(files, 'src/pages/', {'.js', '.jsx', '.tsx', '.ts'}, 'gatsby-pages', skip=(r'404',))
    for path in ('gatsby-node.js', 'gatsby-node.ts', 'gatsby-node.mjs'):
        for match in re.finditer(r'''createPage\s*\(\s*\{[^}]*?path\s*:\s*[`'"]([^`'"]+)[`'"][^}]*?component\s*:\s*[^'"`]*['"`]([^'"`]+)['"`]''',
                                 files.get(path, ''), re.S):
            url = re.sub(r'\$\{[^}]+\}', ':param', match.group(1))
            target = posixpath.normpath(match.group(2).replace('./', '', 1)) if match.group(2).startswith('.') else match.group(2)
            rows.append(row(url, target if target in files else path, 'gatsby-create-page'))
    return rows


def docs_routes(files, base='docs/', adapter='docs'):
    return file_routes(files, base, {'.md', '.mdx'}, adapter, index_names=('index', 'README', 'readme'))


def front_matter(source):
    match = re.match(r'\A---\s*\n(.*?)\n---', source, re.S)
    fields = {}
    if match:
        for line in match.group(1).splitlines():
            pair = re.match(r'''^(\w+)\s*:\s*["']?([^"'\n#]*)["']?\s*$''', line)
            if pair:
                fields[pair.group(1)] = pair.group(2).strip()
    return fields


def jekyll_routes(files):
    rows = []
    config = files.get('_config.yml', '')
    permalink = re.search(r'(?m)^permalink:\s*(\S+)', config)
    for path in sorted(files):
        if path.startswith(('_', '.', 'vendor/', 'node_modules/')) or TEST_PATH.search(path):
            continue
        if PurePosixPath(path).suffix not in {'.md', '.markdown', '.html'}:
            continue
        source = files[path]
        if not source.startswith('---'):
            continue
        fields = front_matter(source)
        url = fields.get('permalink') or '/' + re.sub(r'(?:^|/)index$', '', path.rsplit('.', 1)[0])
        rows.append(row(url, path, 'jekyll-pages', layout=fields.get('layout')))
    for path in sorted(p for p in files if p.startswith('_posts/')):
        match = re.match(r'_posts/(\d{4})-(\d{2})-(\d{2})-(.+)\.(?:md|markdown|html)$', path)
        if match:
            fields = front_matter(files[path])
            style = (permalink.group(1) if permalink else '/:categories/:year/:month/:day/:title:output_ext')
            url = fields.get('permalink') or (style.replace(':categories/', '').replace(':year', match.group(1))
                                              .replace(':month', match.group(2)).replace(':day', match.group(3))
                                              .replace(':title', match.group(4)).replace(':output_ext', '.html'))
            if url in {'pretty', 'date'}:
                url = f'/{match.group(1)}/{match.group(2)}/{match.group(3)}/{match.group(4)}/'
            rows.append(row(url, path, 'jekyll-posts', layout=fields.get('layout')))
    return rows


def hugo_routes(files):
    rows = []
    for path in sorted(files):
        match = re.match(r'^content/(.+)\.(?:md|markdown|html)$', path)
        if not match:
            continue
        fields = front_matter(files[path])
        relative = match.group(1)
        if fields.get('url'):
            url = fields['url']
        else:
            parts = relative.split('/')
            if parts[-1] in {'_index', 'index'}:
                parts = parts[:-1]
            elif fields.get('slug'):
                parts[-1] = fields['slug']
            url = '/'.join(parts)
        rows.append(row(url, path, 'hugo-content', kind='content-page',
                        section_list=PurePosixPath(path).stem == '_index'))
    if not any(r['url'] == '/' for r in rows) and 'layouts/index.html' in files:
        rows.append(row('/', 'layouts/index.html', 'hugo-home'))
    return rows


def eleventy_routes(files):
    rows = []
    config = next((files[p] for p in files if re.search(r'(?:^|/)(?:\.eleventy|eleventy\.config)\.[cm]?js$', p)), '')
    input_dir = re.search(r'''input\s*:\s*['"]([^'"]+)['"]''', config)
    base = (input_dir.group(1).strip('./') + '/') if input_dir else ''
    for path in sorted(files):
        if not path.startswith(base) or '/_' in '/' + path[len(base):] or TEST_PATH.search(path):
            continue
        if PurePosixPath(path).suffix not in {'.md', '.njk', '.liquid', '.html', '.11ty.js', '.hbs', '.pug'}:
            continue
        fields = front_matter(files[path])
        relative = path[len(base):].rsplit('.', 1)[0]
        url = fields.get('permalink') or '/' + re.sub(r'(?:^|/)index$', '', relative)
        if url in {'false', 'False'}:
            continue
        rows.append(row(url, path, 'eleventy', layout=fields.get('layout')))
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


def laravel_routes(files):
    rows = []
    source = files.get('routes/web.php', '')
    for match in re.finditer(r'''Route::(get|view|any|match)\s*\(\s*['"]([^'"]+)['"]\s*,\s*(.*?)\)\s*;''', source, re.S):
        method, url, body = match.groups()
        view = re.search(r'''^\s*['"]([\w.\-/]+)['"]''', body) if method == 'view' else re.search(r'''\bview\s*\(\s*['"]([\w.\-/]+)['"]''', body)
        if not view:
            controller = re.search(r'''\[\s*([\w\\]+)::class\s*,\s*['"](\w+)['"]\s*\]''', body)
            if controller:
                cls = controller.group(1).split('\\')[-1]
                file = next((p for p in files if p.endswith('/' + cls + '.php')), None)
                if file:
                    action = re.search(r'function\s+' + controller.group(2) + r'\s*\(.*?\bview\s*\(\s*[\'"]([\w.\-/]+)[\'"]', files[file], re.S)
                    view = action
        template = None
        if view:
            dotted = view.group(1).replace('.', '/')
            template = next((c for c in ('resources/views/' + dotted + '.blade.php', 'resources/views/' + dotted + '.php') if c in files), None)
        rows.append(row(url, template or 'routes/web.php', 'laravel-routes', line=source.count('\n', 0, match.start()) + 1,
                        declared_in='routes/web.php', template_resolved=bool(template)))
    return rows


def rails_routes(files):
    rows = []
    source = files.get('config/routes.rb', '')

    def view(controller, action):
        for ext in ('.html.erb', '.html.haml', '.html.slim', '.erb'):
            candidate = f'app/views/{controller}/{action}{ext}'
            if candidate in files:
                return candidate
        return None
    for match in re.finditer(r'''^\s*root\s+(?:to:\s*)?['"](\w+)#(\w+)['"]''', source, re.M):
        rows.append(row('/', view(*match.groups()) or 'config/routes.rb', 'rails-routes'))
    for match in re.finditer(r'''^\s*get\s+['"]([^'"]+)['"](?:\s*(?:=>|,\s*to:)\s*['"](\w+)#(\w+)['"])?''', source, re.M):
        url, controller, action = match.groups()
        if not controller:
            parts = url.strip('/').split('/')
            controller, action = (parts[0], parts[1]) if len(parts) > 1 else ('pages', parts[0])
        rows.append(row(url, view(controller, action) or 'config/routes.rb', 'rails-routes'))
    for match in re.finditer(r'''^\s*resources?\s+:(\w+)(?:,\s*only:\s*\[([^\]]*)\])?''', source, re.M):
        name, only = match.group(1), match.group(2)
        actions = re.findall(r':(\w+)', only) if only else ['index', 'show']
        if 'index' in actions:
            rows.append(row('/' + name, view(name, 'index') or 'config/routes.rb', 'rails-routes'))
        if 'show' in actions:
            rows.append(row('/' + name + '/:id', view(name, 'show') or 'config/routes.rb', 'rails-routes'))
    return rows


def express_routes(files):
    rows = []
    for path in sorted(p for p in files if re.search(r'\.[cm]?[jt]s$', p) and not TEST_PATH.search(p)):
        for match in re.finditer(r'''\b(?:app|router)\.get\s*\(\s*['"`](/[^'"`]*)['"`]\s*,(.{0,400}?)\bres\.render\s*\(\s*['"`]([^'"`]+)['"`]''',
                                 files[path], re.S):
            name = match.group(3)
            template = next((p for p in files if re.search(r'(?:^|/)views/' + re.escape(name) + r'\.(?:ejs|pug|hbs|handlebars|njk|html)$', p)), None)
            rows.append(row(match.group(1), template or path, 'express-routes', declared_in=path))
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
    from seo_agents.exploration.repo_analysis.tools.element_index import _parser
    return _parser


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
            if path_node is not None and path_node.type == 'string':
                segment = text(path_node)[1:-1]
                here = segment if segment.startswith('/') else (prefix.rstrip('/') + '/' + segment)
            elif path_node is not None:
                return
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
                    segment = text(path_value).strip('{}').strip('\'"`')
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
                if objects and any(prop(o, 'path') is not None for o in objects):
                    for obj in objects:
                        visit_object(obj, prefix)
                    return
            for child in node.named_children:
                walk(child, prefix)
        walk(tree)
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


def adapter_routes(files, fingerprint):
    """Rows from the primary framework's adapter, de-duplicated by (url, template)."""
    adapter = ADAPTERS.get(fingerprint.get('primary'))
    if adapter is None:
        return [], None
    rows = adapter(files)
    if not rows and fingerprint.get('primary') in {'react-spa', 'vue-spa', 'svelte-spa', 'preact-spa'}:
        entry = next((p for p in ('index.html', 'public/index.html', 'src/App.tsx', 'src/App.jsx', 'src/App.vue', 'src/App.svelte')
                      if p in files), None)
        if entry:
            rows = [row('/', entry, 'spa-single-entry')]
    seen, unique = set(), []
    for item in rows:
        key = (item['url'], item['template'])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique, adapter.__name__ if hasattr(adapter, '__name__') else str(fingerprint.get('primary'))


def static_params(files, template, pattern):
    """Concrete instances for a dynamic route from literal params in the template/its data.

    Covers generateStaticParams/getStaticPaths/entries/prerender lists that contain
    literal `{param: 'value'}` objects (Next, Astro, SvelteKit, Nuxt, Gatsby)."""
    names = re.findall(r'\[(?:\.\.\.)?(\w+)\]|:(\w+)|\{(\w+)\}', pattern)
    names = [next(n for n in group if n) for group in names]
    if len(names) != 1:
        return []
    name = names[0]
    source = files.get(template, '')
    if not re.search(r'generateStaticParams|getStaticPaths|entries\s*\(|prerender|staticPaths', source):
        return []
    values = re.findall(r'''\b''' + re.escape(name) + r'''\s*:\s*['"]([\w\-.~%]+)['"]''', source)
    seen = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen[:200]


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
