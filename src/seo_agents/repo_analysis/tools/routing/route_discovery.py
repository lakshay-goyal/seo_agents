"""Route discovery from the framework-agnostic routes profile (`profiles/routes.json`).

Two sources, both checked for every repository:

1. File-system conventions: a path regex per framework adapter (Next app/pages,
   SvelteKit, Remix flat routes, Nuxt/Astro pages, Shopify theme templates,
   WordPress classic + block template hierarchy, Ghost, Hugo, Jekyll, server
   template/view folders, plain HTML/PHP). A convention can be gated on a marker
   path (`requires_path`) and can map a captured template name to a public URL
   pattern (`route_map`, `route_template`).
2. Code declarations: router configs and server handlers. Nested React Router
   `<Route>` trees, `children: [...]` route objects (React Router, Vue Router,
   Angular), Django `include()` prefixes and Rails `namespace`/`resources` blocks
   are resolved by small structural parsers so child routes keep their parents.

Every row: {route, kind, adapter, method, is_dynamic, source, ...}. Next.js
`basePath` and i18n locales from next.config are applied to Next routes.
"""

import re

from seo_agents.repo_analysis.tools.repository.source_index import glob_match
from seo_agents.repo_analysis.tools.signal_profiles.signal_profile_runner import signal_profile

DYNAMIC_SEGMENT = re.compile(r'\[|\]|(?:^|/):|\$|\{|\*|<\w')
NEXT_ADAPTERS = {'next-app', 'file-pages'}


def normalize_route(raw, dot_segments=False):
    route = raw.strip('/')
    if dot_segments:
        route = re.sub(r'\.(?![^\[]*\])', '/', route)
        route = re.sub(r'(^|/)\$(\w+)', r'\1:\2', route)
        route = re.sub(r'(^|/)\$$', r'\1*', route)
    parts = []
    for part in route.split('/'):
        if re.match(r'^\((?:\.{1,3}|\.\.\)\(\.\.)\)', part):
            return None  # Next intercepting route `(.)photo`: not a URL of its own
        if not part or re.fullmatch(r'\([^()]*\)', part) or part.startswith('@'):
            continue
        if dot_segments and part.startswith('_') and part != '_index':
            continue
        parts.append(part)
    if parts and parts[-1] in {'index', '_index', '+page', 'page'}:
        parts.pop()
    return '/' + '/'.join(parts)


def join_route(parent, child):
    child = (child or '').strip()
    if child.startswith('/'):
        return '/' + child.strip('/') if child.strip('/') else '/'
    combined = (parent or '').rstrip('/') + '/' + child
    combined = re.sub(r'/+', '/', combined)
    return combined.rstrip('/') or '/'


def _convention_route(rule, match):
    groups = {k: v for k, v in match.groupdict().items() if v is not None}
    if rule.get('route_map') is not None:
        route = rule['route_map'].get(groups.get('name', ''), rule.get('route_default'))
        return route
    if rule.get('route_template'):
        values = dict(groups)
        if 'route' in values:
            normalized = normalize_route(values['route'], rule.get('dot_segments', False))
            if normalized is None:
                return None
            values['route'] = normalized.strip('/')
        try:
            route = rule['route_template'].format(**values)
        except (KeyError, IndexError):
            return None
        route = re.sub(r'/+', '/', route)
        return route.rstrip('/') or '/'
    if 'route' in groups:
        return normalize_route(groups['route'], rule.get('dot_segments', False))
    return rule.get('route_default')


def _row(index, route, kind, adapter, method, source, **extra):
    return {'route': route, 'kind': kind, 'adapter': adapter, 'method': method,
            'is_dynamic': bool(DYNAMIC_SEGMENT.search(route)), 'source': source, **extra}


# ------------------------------------------------------------------ structural parsers
def _tag_end(text, start):
    """End offset of a JSX/HTML start tag, skipping `{...}` and quoted attribute values."""
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
        elif char == '{':
            depth += 1
        elif char == '}':
            depth = max(0, depth - 1)
        elif char == '>' and depth == 0:
            return index + 1
        index += 1
    return len(text)


ROUTE_PATH_ATTR = re.compile(r'''\bpath\s*=\s*\{?\s*(['"`])([^'"`]*)\1''')


def parse_jsx_routes(text):
    """(start, end, full_path) for nested <Route path> trees; index routes take the parent path."""
    found, stack = [], []
    for match in re.finditer(r'<Route\b|</Route\s*>', text):
        if match.group(0).startswith('</'):
            if stack:
                stack.pop()
            continue
        end = _tag_end(text, match.end())
        tag = text[match.start():end]
        self_closing = tag.rstrip('>').rstrip().endswith('/')
        attr = ROUTE_PATH_ATTR.search(tag)
        parent = stack[-1] if stack else ''
        full = None
        if attr:
            full = join_route(parent, attr.group(2))
        elif re.search(r'\bindex\b', tag):
            full = parent or '/'
        if full is not None:
            found.append((match.start(), end, full))
        if not self_closing:
            stack.append(full if full is not None else parent)
    return found


OBJECT_TOKENS = re.compile(r'''children\s*:\s*\[|\bpath\s*:\s*(['"`])([^'"`]*)\1|[\[\]{}]''')
ROUTE_OBJECT_KEYS = re.compile(r'''\b(?:element|component|Component|lazy|loadComponent|loadChildren|children|redirectTo|redirect|index|errorElement|components)\s*:''')


def parse_object_routes(text):
    """(start, end, full_path) for `{ path, children: [...] }` route objects at any depth.

    Only router-shaped objects count: `{ path: '/api/v1' }` without element/component/children/... is
    configuration, not a page."""
    found = []
    objects = []      # stack of {'prefix': str, 'path': full or None, 'start': offset, 'hit': (s, e, full) | None}
    brackets = []     # stack of ('children', prefix) | ('plain', None)
    for match in OBJECT_TOKENS.finditer(text):
        token = match.group(0)
        if token.startswith('children'):
            current = objects[-1] if objects else None
            prefix = (current['path'] if current and current['path'] is not None
                      else current['prefix'] if current else '')
            brackets.append(('children', prefix))
        elif token == '[':
            brackets.append(('plain', None))
        elif token == ']':
            if brackets:
                brackets.pop()
        elif token == '{':
            prefix = next((p for kind, p in reversed(brackets) if kind == 'children'), '')
            objects.append({'prefix': prefix, 'path': None, 'start': match.start(), 'hit': None})
        elif token == '}':
            if objects:
                current = objects.pop()
                if current['hit'] and ROUTE_OBJECT_KEYS.search(text[current['start']:match.end()]):
                    found.append(current['hit'])
        else:
            value = match.group(2)
            current = objects[-1] if objects else {'prefix': '', 'path': None, 'start': 0, 'hit': None}
            if current['path'] is not None:
                continue  # only the object's own first `path:` key
            full = join_route(current['prefix'], value)
            current['path'] = full
            current['hit'] = (match.start(), match.end(), full)
    found.sort()
    return found


DJANGO_PATH = re.compile(r'''\b(?:re_)?path\s*\(\s*r?(['"])([^'"]*)\1\s*,\s*(include\s*\(\s*(?:\(\s*)?(['"])([\w.]+)\4)?''')


def django_routes(index, files):
    """{file: [(start, end, route, is_include)]} with include() prefixes applied."""
    declared = {}
    module_file = {}
    for path in files:
        module_file[path[:-3].replace('/', '.')] = path
    includes = {}  # child file -> [(parent file, prefix)]
    for path in files:
        rows = []
        for match in DJANGO_PATH.finditer(index.files[path]):
            rows.append((match.start(), match.end(), match.group(2), match.group(5)))
            if match.group(5):
                child = next((f for module, f in module_file.items() if module == match.group(5) or module.endswith('.' + match.group(5))), None)
                if child:
                    includes.setdefault(child, []).append((path, match.group(2)))
        declared[path] = rows
    prefixes = {}

    def prefixes_of(path, depth=0):
        if path in prefixes:
            return prefixes[path]
        if path not in includes or depth > 6:
            result = ['']
        else:
            result = [parent_prefix + prefix for parent, prefix in includes[path] for parent_prefix in prefixes_of(parent, depth + 1)]
        prefixes[path] = result
        return result

    output = {}
    for path, rows in declared.items():
        out = []
        for start, end, value, include in rows:
            for prefix in prefixes_of(path):
                route = '/' + (prefix + value).lstrip('^').lstrip('/')
                out.append((start, end, route, bool(include)))
        output[path] = out
    return output


def rails_routes(text):
    """(start, end, route) from config/routes.rb with namespace/scope/nested resources."""
    found, stack = [], []
    offset = 0
    for line in text.splitlines(keepends=True):
        start, offset = offset, offset + len(line)
        code = line.split('#', 1)[0].strip()
        if not code:
            continue
        prefix = next((p for p in reversed(stack) if p is not None), '')
        opens_block = bool(re.search(r'\bdo\b(?:\s*\|[^|]*\|)?\s*$', code))
        pushed = None
        match = re.match(r'''root\b\s*\(?\s*(?:to:\s*)?['"]?''', code)
        if match and code.startswith('root'):
            found.append((start, offset, prefix or '/'))
        elif re.match(r'''(?:get|match|post)\s*\(?\s*['"]''', code):
            path = re.match(r'''(?:get|match|post)\s*\(?\s*['"]([^'"]+)['"]''', code).group(1)
            if code.startswith('get') or 'via: :get' in code or 'via: [:get' in code:
                found.append((start, offset, join_route(prefix, path)))
        elif re.match(r'resources\s+:(\w+)', code):
            name = re.match(r'resources\s+:(\w+)', code).group(1)
            only = re.search(r'only:\s*\[([^\]]*)\]|only:\s*:(\w+)', code)
            actions = set(re.findall(r':(\w+)', only.group(1) or '')) | ({only.group(2)} if only and only.group(2) else set()) if only else {'index', 'show'}
            base = join_route(prefix, name)
            if 'index' in actions:
                found.append((start, offset, base))
            if 'show' in actions:
                found.append((start, offset, base + '/:id'))
            pushed = base + '/:' + name.rstrip('s') + '_id'
        elif re.match(r'resource\s+:(\w+)', code):
            name = re.match(r'resource\s+:(\w+)', code).group(1)
            base = join_route(prefix, name)
            found.append((start, offset, base))
            pushed = base
        elif re.match(r'namespace\s+:(\w+)', code):
            pushed = join_route(prefix, re.match(r'namespace\s+:(\w+)', code).group(1))
        elif re.match(r'''scope\s*\(?\s*(?:path:\s*)?['"]([^'"]+)['"]''', code):
            pushed = join_route(prefix, re.match(r'''scope\s*\(?\s*(?:path:\s*)?['"]([^'"]+)['"]''', code).group(1))
        if opens_block:
            stack.append(pushed)
        elif code == 'end' and stack:
            stack.pop()
    return found


def next_config(index):
    """basePath and i18n locales declared in next.config.* (literal values only)."""
    for path in index.paths():
        if re.search(r'(?:^|/)next\.config\.[cm]?[jt]s$', path):
            text = index.files[path]
            base = re.search(r'''\bbasePath\s*:\s*['"`]([^'"`]*)['"`]''', text)
            locales = re.search(r'''\blocales\s*:\s*\[([^\]]*)\]''', text)
            default = re.search(r'''\bdefaultLocale\s*:\s*['"`]([^'"`]+)['"`]''', text)
            return {'file': path, 'base_path': base.group(1).rstrip('/') if base else '',
                    'locales': re.findall(r'''['"`]([^'"`]+)['"`]''', locales.group(1)) if locales else [],
                    'default_locale': default.group(1) if default else None}
    return None


# ------------------------------------------------------------------ discovery
def discover_routes(index):
    profile = signal_profile('routes')['profile']
    all_paths = set(index.files) | set(index.assets) | {row['path'] for row in index.snapshot.get('inventory', [])}
    gate_cache = {}

    def gated(rule):
        pattern = rule.get('requires_path')
        if not pattern:
            return True
        if pattern not in gate_cache:
            regex = re.compile(pattern)
            gate_cache[pattern] = any(regex.search(p) for p in all_paths)
        return gate_cache[pattern]

    routes, claimed = [], set()
    for rule in profile['conventions']:
        if not gated(rule):
            continue
        pattern = re.compile(rule['pattern'])
        exclude = re.compile(rule['exclude']) if rule.get('exclude') else None
        for path in index.paths():
            if path in claimed:
                continue  # first (most specific) convention wins
            match = pattern.match(path)
            if not match:
                continue
            raw = match.groupdict().get('route')
            if exclude and exclude.search((raw if raw is not None else match.groupdict().get('name') or path).strip('/')):
                continue
            route = _convention_route(rule, match)
            if route is None:
                continue
            claimed.add(path)
            extra = {}
            if match.groupdict().get('variant'):
                extra['template_variant'] = match.group('variant').lstrip('.')
            routes.append(_row(index, route, rule['kind'], rule['adapter'], 'file-system-convention',
                               index.evidence_lines('route-file', path, 1, len(index.lines(path)), route=route, route_kind=rule['kind']),
                               **extra))
    seen = set()
    django_cache = None
    for rule in profile['declarations']:
        regex = re.compile(rule['regex'], re.M)
        requires = re.compile(rule['requires']) if rule.get('requires') else None
        parser = rule.get('parser')
        for path in index.paths():
            if not glob_match(path, rule['glob']):
                continue
            text = index.files[path]
            if requires and not requires.search(text):
                continue
            if parser == 'jsx-routes':
                matches = [(s, e, r, rule['kind']) for s, e, r in parse_jsx_routes(text)]
            elif parser == 'object-routes':
                matches = [(s, e, r, rule['kind']) for s, e, r in parse_object_routes(text)]
            elif parser == 'django-urls':
                if django_cache is None:
                    django_cache = django_routes(index, [p for p in index.paths() if glob_match(p, rule['glob'])])
                matches = [(s, e, r, 'route-prefix' if inc else rule['kind']) for s, e, r, inc in django_cache.get(path, [])]
            elif parser == 'rails-routes':
                matches = [(s, e, r, rule['kind']) for s, e, r in rails_routes(text)]
            else:
                matches = []
                for match in regex.finditer(text):
                    value = match.group(1).strip()
                    route = value if value.startswith(('/', '*', '^')) else '/' + value
                    matches.append((match.start(), match.end(), route, rule['kind']))
                    if rule.get('resource'):
                        param = '{' + value.strip('/').split('/')[-1].split('.')[-1].rstrip('s') + '}'
                        matches.append((match.start(), match.end(), route.rstrip('/') + '/' + param, rule['kind']))
            for start, end, route, kind in matches:
                key = (path, route, kind)
                if key in seen:
                    continue
                seen.add(key)
                routes.append(_row(index, route, kind, rule['adapter'], 'code-declaration',
                                   index.evidence('route-declaration', path, start, end, route=route, route_kind=kind)))
    config = next_config(index)
    if config and (config['base_path'] or config['locales']):
        for row in routes:
            if row['adapter'] not in NEXT_ADAPTERS:
                continue
            row['file_route'] = row['route']
            if config['base_path']:
                row['route'] = join_route(config['base_path'], row['route'].lstrip('/')) if row['route'] != '/' else config['base_path'] or '/'
            if config['locales']:
                row['locale_variants'] = [join_route(config['base_path'] + '/' + locale, row['file_route'].lstrip('/'))
                                          for locale in config['locales'] if locale != config['default_locale']]
            row['route_config'] = config['file']
    routes.sort(key=lambda r: (r['kind'] not in {'page', 'content-page'}, r['route'], r['source']['location']['file']))
    return routes


__all__ = ['discover_routes', 'normalize_route', 'join_route', 'parse_jsx_routes', 'parse_object_routes', 'rails_routes',
           'DYNAMIC_SEGMENT']


class RouteDiscoveryTool:
    """Tool: generic route discovery (file conventions, JSX/object routers, Django/Rails, next.config)."""

    name = 'route_discovery'

    def discover(self, index):
        return discover_routes(index)
