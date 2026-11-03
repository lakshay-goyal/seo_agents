"""Source lens: what each page declares, resolved from source, with an edit location per value.

Framework-agnostic: it works from the element index (any template language), the
composition graph (layouts, includes, sections, components) and a few metadata
APIs (Next `metadata`, Nuxt `useSeoMeta/useHead`, Remix `meta`, Angular Title/Meta,
front matter, template blocks). Value resolution order for an element's text or
attribute:

1. literal text/attribute                          -> edit.kind = literal
2. template block override (Django/Jinja/Twig/Blade/Hugo/Nunjucks child blocks)
3. component prop passed literally by a parent      -> edit.kind = prop
4. i18n key looked up in the default catalog / gettext literal msgid -> i18n
5. page front matter / site config variable         -> markdown / yaml / toml
6. known CMS/runtime expression (Shopify, WordPress) -> cms (value null)
7. anything else                                    -> computed (value null)

When a rendered lens exists, `attribution.py` uses these candidates to explain the
rendered value; without one, these values are the page model (source-only mode).
"""

from __future__ import annotations

from collections import defaultdict
import posixpath
import re
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from seo_agents.repo_analysis.tools.parsing.element_index import _parser, layout_chain, norm_text, page_composition

HEAD_TAGS = {'title', 'meta', 'link', 'html', 'base'}
LINK_TAGS = {'a', 'Link', 'NuxtLink', 'nuxt-link', 'RouterLink', 'router-link', 'NavLink', 'A', 'Anchor', 'GatsbyLink',
             'inertia-link', 'Inertia.Link', 'ion-router-link', 'sveltekit:link'}
IMAGE_TAGS = {'img', 'Image', 'NuxtImg', 'nuxt-img', 'NuxtPicture', 'nuxt-picture', 'StaticImage', 'GatsbyImage', 'Img',
              'v-img', 'ion-img', 'amp-img', 'Picture'}
BLOCK_TAGS = {'p', 'li', 'blockquote', 'td', 'th', 'dd', 'dt', 'figcaption', 'summary', 'caption', 'button', 'label',
              'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
CHROME = {'header', 'nav', 'footer', 'aside'}
CMS_EXPR = re.compile(r'\b(?:page_title|page_description|shop\.name|product\.|collection\.|article\.|blog\.|settings\.|section\.settings|'
                      r'block\.settings|the_title|the_content|the_excerpt|wp_title|get_the_title|bloginfo|the_post_thumbnail|'
                      r'get_the_post_thumbnail|the_permalink|get_permalink|single_post_title|the_author|get_field|the_field|'
                      r'@post\.|\$post->|\{\{\s*@?(?:post|page|site|blog)\.\w|\.Content\b|content_for_layout|wp_head)', re.I)
HEADING_TEXT_PROPS = ('title', 'heading', 'label', 'text', 'name', 'headline', 'children')
STATIC_URL = [
    (re.compile(r'''\b(?:home_url|site_url|get_home_url|get_site_url|url|asset|secure_url|secure_asset|static|url_for)\s*\(\s*['"]([^'"]*)['"]'''), None),
    (re.compile(r'''\{%-?\s*static\s+['"]([^'"]+)['"]'''), '/static/'),
    (re.compile(r'''['"]([^'"]+)['"]\s*\|\s*(?:relative_url|absolute_url|relURL|absURL|relLangURL|absLangURL|url)\b'''), ''),
    (re.compile(r'''\broutes\.root_url\b'''), '/'),
]
TERNARY = re.compile(r'''^\s*\{?\s*[^?{}]+\?\s*(["'`])([^"'`]*)\1\s*:\s*(["'`])([^"'`]*)\3\s*\}?\s*$''')
NAMED_ROUTE = re.compile(r'''\{%-?\s*url\s+['"]([\w:\-]+)['"]|\broute\s*\(\s*['"]([\w.\-]+)['"]|\burl_for\s*\(\s*['"]([\w.\-]+)['"]''')


def field(value=None, method='none', edit=None, confidence=0.0, **extra):
    return {'value': value, 'method': method, 'edit': edit, 'confidence': confidence, **extra}


SPAN_KEYS = ('line', 'col', 'end_line', 'end_col')
SCOPE_KEYS = ('in_loop', 'template_n', 'data_source', 'shared', 'used_by')


def edit_of(element, kind='literal', **extra):
    """Edit location for an element value, with the exact value span when the IR has one.

    - attribute values (`attr=`) point at the attribute's value characters, or at the constant that
      supplies them (`const`), not at the tag;
    - literal text points at the contiguous text run (`text_span`); text split by child elements or
      expressions gets `contiguous: false` + `segments` so an editor never searches for a string that
      does not exist in the source;
    - loop/shared context (`in_loop`, `template_n`, `data_source`, `shared`, `used_by`) is copied so the
      consumer can tell a per-instance edit from a template-wide or site-wide one.
    """
    edit = {'kind': kind, 'file': element['file'], 'line': element['line'], 'col': element.get('col', 0)}
    attr = extra.get('attr')
    value = (element.get('attrs') or {}).get(attr) if attr else None
    if value and kind != 'add' and 'line' in value:
        const = value.get('const')
        if const:
            edit.update(file=const['file'], pointer=const['pointer'], via='const ' + const['name'])
        edit.update({k: value[k] for k in SPAN_KEYS if k in value})
    elif not attr and (kind == 'literal' or extra.get('prop') == 'children') and element.get('text_literal'):
        if element.get('contiguous') and element.get('text_span'):
            edit.update(element['text_span'])
        elif element.get('contiguous') is False:
            edit['contiguous'] = False
            edit['segments'] = element.get('segments', [])
    for key in SCOPE_KEYS:
        if element.get(key) is not None and key not in extra:
            edit[key] = element[key]
    edit.update(extra)
    return edit


def instance_edit(instance, element, via='loop instance'):
    """Edit for one rendered instance of a looped template: the data row (JSON pointer / array literal)."""
    edit = {k: instance[k] for k in ('kind', 'file', 'line', 'col', 'end_line', 'end_col', 'pointer') if instance.get(k) is not None}
    edit.update(in_loop=True, via=via, template={'file': element['file'], 'line': element['line'], 'col': element.get('col', 0)})
    for key in ('template_n', 'data_source'):
        if element.get(key) is not None:
            edit[key] = element[key]
    return edit


class Context:
    """Per-repository lookups shared by every page."""

    def __init__(self, snapshot, index, fingerprint):
        self.files = snapshot['files']
        self.assets = snapshot.get('assets', {})
        self.index = index
        self.fingerprint = fingerprint
        self.by_file = defaultdict(list)
        for element in index['elements']:
            self.by_file[element['file']].append(element)
        self.children = defaultdict(list)
        self.by_id = {}
        for element in index['elements']:
            self.by_id[element['id']] = element
            if element.get('parent'):
                self.children[element['parent']].append(element)
        self.usages = defaultdict(list)  # component file -> usage elements
        for element in index['elements']:
            if element.get('component_ref'):
                self.usages[element['component_ref']].append(element)
        self.default_locale = self._default_locale()
        self.named_routes = self._named_routes()
        self.site_config = self._site_config()
        self.imports = defaultdict(dict)
        for ref in index['refs']:
            if ref['kind'] == 'import':
                for name in re.findall(r'[A-Za-z_$][\w$]*', ref.get('names', '')):
                    self.imports[ref['file']][name] = ref.get('resolved') or ref['spec']
        self._meta_cache = {}
        self._compositions = {}
        self.page_templates = set()
        self.shells = set(index.get('shells') or [])
        self.file_pages = {}
        from seo_agents.repo_analysis.site_model.url_manifest import site_hosts
        self.own_hosts = site_hosts(snapshot)

    def composition(self, page):
        key = (page.get('id'), page.get('template'))
        if key not in self._compositions:
            self._compositions[key] = composition_for(self, page)
        return self._compositions[key]

    def _default_locale(self):
        locales = [entry.get('locale') for entries in self.index['i18n'].values() for entry in entries if entry.get('locale')]
        if any('default' in entry['file'] for entries in self.index['i18n'].values() for entry in entries):
            default = next(entry['locale'] for entries in self.index['i18n'].values() for entry in entries if 'default' in entry['file'])
            return default
        return 'en' if 'en' in locales else (locales[0] if locales else None)

    def i18n(self, key):
        entries = self.index['i18n'].get(key) or []
        if not entries:
            return None
        preferred = [e for e in entries if 'default' in e['file']] or [e for e in entries if e.get('locale') == self.default_locale] or entries
        return preferred[0]

    def _named_routes(self):
        routes = {}
        for path, text in self.files.items():
            if path.endswith('urls.py'):
                for match in re.finditer(r'''path\s*\(\s*r?['"]([^'"]*)['"][^)]*?name\s*=\s*['"]([\w\-]+)['"]''', text):
                    routes[match.group(2)] = '/' + match.group(1)
            elif path.endswith('web.php'):
                for match in re.finditer(r'''Route::\w+\s*\(\s*['"]([^'"]+)['"].*?->name\s*\(\s*['"]([\w.\-]+)['"]''', text):
                    routes[match.group(2)] = match.group(1)
            elif path.endswith('.py'):
                for match in re.finditer(r'''@\w+\.(?:route|get)\s*\(\s*['"]([^'"]+)['"][^)]*\)\s*\n(?:@[^\n]+\n)*\s*def\s+(\w+)''', text):
                    routes[match.group(2)] = match.group(1)
        return routes

    def _site_config(self):
        config = {}
        for name in ('config.toml', 'hugo.toml', 'config.yaml', 'hugo.yaml', '_config.yml', '_config.yaml', 'config.yml'):
            text = self.files.get(name)
            if not text:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                match = re.match(r'''^\s*(title|description|baseURL|baseurl|url|languageCode)\s*[=:]\s*["']?([^"'\n#]+?)["']?\s*$''', line)
                if match and match.group(1).lower() not in config:
                    config[match.group(1).lower()] = {'value': match.group(2).strip(), 'file': name, 'line': number}
        return config


# ---------------------------------------------------------------- JS metadata APIs
def char_col(node, data):
    line_start = node.start_byte - node.start_point.column
    return len(data[line_start:node.start_byte].decode('utf-8', 'replace'))


def js_literal(node, data):
    """tree-sitter node -> python value with positions for strings."""
    text = lambda n: data[n.start_byte:n.end_byte].decode('utf-8', 'replace')
    kind = node.type
    if kind == 'string':
        return {'__str__': text(node)[1:-1], 'line': node.start_point.row + 1, 'col': char_col(node, data)}
    if kind == 'template_string' and not any(c.type == 'template_substitution' for c in node.named_children):
        return {'__str__': text(node)[1:-1], 'line': node.start_point.row + 1, 'col': char_col(node, data)}
    if kind == 'object':
        result = {'__obj__': True, 'line': node.start_point.row + 1}
        for pair in node.named_children:
            if pair.type == 'pair':
                key = pair.child_by_field_name('key')
                value = pair.child_by_field_name('value')
                if key is not None and value is not None:
                    result[text(key).strip('\'"')] = js_literal(value, data)
            elif pair.type == 'shorthand_property_identifier':
                result[text(pair)] = {'__expr__': text(pair), 'line': pair.start_point.row + 1, 'col': char_col(pair, data)}
        return result
    if kind == 'array':
        return [js_literal(child, data) for child in node.named_children if child.type != 'comment']
    if kind in {'satisfies_expression', 'as_expression', 'parenthesized_expression'} and node.named_children:
        return js_literal(node.named_children[0], data)
    return {'__expr__': text(node)[:300], 'line': node.start_point.row + 1, 'col': char_col(node, data)}


def metadata_objects(path, source, cache):
    """Literal metadata declarations in one script/SFC file."""
    if path in cache:
        return cache[path]
    found = []
    suffix = PurePosixPath(path).suffix
    blocks = []
    if suffix in {'.vue', '.svelte', '.astro'}:
        for match in re.finditer(r'<script\b[^>]*>(.*?)</script>', source, re.S):
            blocks.append((match.group(1), source[:match.start(1)].count('\n')))
        fm = re.match(r'\s*---\n(.*?)\n---', source, re.S)
        if fm:
            blocks.append((fm.group(1), source[:fm.start(1)].count('\n')))
    elif suffix in {'.js', '.jsx', '.ts', '.tsx', '.mjs'}:
        blocks.append((source, 0))
    for code, offset in blocks:
        if not re.search(r'metadata|useSeoMeta|useHead|definePageMeta|useServerSeoMeta|\bmeta\b|setTitle|updateTag|addTag|head\s*\(', code, re.I):
            continue
        language = 'tsx' if suffix in {'.tsx', '.astro'} else 'ts' if suffix in {'.ts', '.vue'} else 'js'
        data = code.encode()
        tree = _parser(language).parse(data).root_node
        text = lambda n: data[n.start_byte:n.end_byte].decode('utf-8', 'replace')
        stack = [tree]
        while stack:
            node = stack.pop()
            stack.extend(node.named_children)
            if node.type == 'variable_declarator':
                name = node.child_by_field_name('name')
                value = node.child_by_field_name('value')
                if name is not None and value is not None and text(name) in {'metadata', 'meta', 'head', 'seo'}:
                    found.append(('next-metadata' if text(name) == 'metadata' else 'export-' + text(name), _first_literal(value, data), offset))
            elif node.type == 'call_expression':
                function = node.child_by_field_name('function')
                fname = text(function) if function is not None else ''
                args = node.child_by_field_name('arguments')
                first = args.named_children[0] if args is not None and args.named_children else None
                short = fname.split('.')[-1]
                if short in {'useSeoMeta', 'useServerSeoMeta', 'useHead', 'useServerHead', 'definePageMeta', 'updateTag', 'addTag', 'setTitle'} and first is not None:
                    found.append((short, js_literal(first, data), offset))
            elif node.type in {'function_declaration', 'method_definition'}:
                name = node.child_by_field_name('name')
                if name is not None and text(name) in {'generateMetadata', 'meta', 'head', 'Head'}:
                    body = node.child_by_field_name('body')
                    returned = _returned_literal(body, data) if body is not None else None
                    found.append(('function-' + text(name), returned or {'__expr__': text(name) + '()', 'line': node.start_point.row + 1, 'col': 0}, offset))
    cache[path] = found
    return found


def _first_literal(node, data):
    if node.type in {'arrow_function', 'function_expression', 'function'}:
        body = node.child_by_field_name('body')
        if body is not None and body.type in {'object', 'array', 'parenthesized_expression'}:
            return js_literal(body, data)
        return _returned_literal(body, data) if body is not None else None
    return js_literal(node, data)


def _returned_literal(body, data):
    stack = [body]
    while stack:
        node = stack.pop()
        if node.type == 'return_statement' and node.named_children:
            return js_literal(node.named_children[0], data)
        if node.type not in {'function_declaration', 'arrow_function', 'function_expression'} or node is body:
            stack.extend(reversed(node.named_children))
    return None


def _shift(value, offset):
    if isinstance(value, dict) and 'line' in value:
        return {**value, 'line': value['line'] + offset}
    return value


def string_at(obj, *keys):
    value = obj
    for key in keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return None
    return value if isinstance(value, dict) and ('__str__' in value or '__expr__' in value) else None


# ---------------------------------------------------------------- template blocks
BLOCK_DEF = [
    re.compile(r'\{%-?\s*block\s+(\w+)\s*-?%\}(.*?)\{%-?\s*endblock(?:\s+\w+)?\s*-?%\}', re.S),       # Django/Jinja/Twig/Nunjucks
    re.compile(r'''@section\s*\(\s*['"](\w+)['"]\s*,\s*['"]([^'"]*)['"]\s*\)'''),                     # Blade inline
    re.compile(r'''@section\s*\(\s*['"](\w+)['"]\s*\)(.*?)@(?:endsection|show|stop)''', re.S),          # Blade block
    re.compile(r'\{\{-?\s*define\s+"(\w+)"\s*-?\}\}(.*?)\{\{-?\s*end\s*-?\}\}', re.S),                 # Hugo/Go
]
BLOCK_USE = re.compile(r'''\{%-?\s*block\s+(\w+)|@yield\s*\(\s*['"](\w+)['"](?:\s*,\s*['"]([^'"]*)['"])?|\{\{-?\s*block\s+"(\w+)"''')


def template_blocks(files, composition):
    """name -> (literal text or None, file, line). Child templates (earlier in composition) win."""
    blocks = {}
    for file in reversed(composition):  # composition lists the page template before the bases it extends
        source = files.get(file, '')
        for position, regex in enumerate(BLOCK_DEF):
            for match in regex.finditer(source):
                name, body = match.group(1), match.group(2)
                if position == 1:      # Blade inline @section('name', 'value')
                    literal = body
                elif re.search(r'\{\{|\{%|<\?|<[a-zA-Z/]|@\w', body):
                    literal = None     # markup or directives: elements are indexed separately
                else:
                    literal = norm_text(body) or None
                blocks[name] = (literal, file, source.count('\n', 0, match.start(2)) + 1)
    return blocks


# ---------------------------------------------------------------- resolution
class PageResolver:
    def __init__(self, ctx, page, composition, page_files, front):
        self.ctx, self.page, self.composition = ctx, page, composition
        self.page_files = page_files    # template subtree (rank 2); others are layouts/shared
        self.front = front              # page front matter {key: (value, line)}
        self.blocks = template_blocks(ctx.files, composition)
        self.limitations = []

    def rank(self, element):
        return 2 if element['file'] in self.page_files else 1

    def prop_usages(self, element):
        """Usages of the component file that defines `element`, restricted to this page."""
        usages = [u for u in self.ctx.usages.get(element['file'], []) if u['file'] in self.composition]
        defined_in = element.get('component_fn')
        if defined_in and any(u.get('component_fn_ref') for u in usages):
            # several components in one file: only call sites of the component that renders `element`
            usages = [u for u in usages if u.get('component_fn_ref') == defined_in or
                      (not u.get('component_fn_ref') and u['file'] != element['file'])]
        return usages

    def resolve_expr(self, expr, element, attr=None):
        """Resolve one expression part to a field (value, method, edit)."""
        source = expr.get('expr', '')
        instances = expr.get('instances') or []
        if instances:
            # Looped template (`items.map(item => ...)`, v-for, {#each}): one value per data row.
            return field(None, 'source-expression', edit_of(element, 'computed', attr=attr, via=source[:120]), 0.3,
                         candidates=[{'value': i['value'], **instance_edit(i, element)} for i in instances], loop_ref=expr.get('loop_ref'))
        if expr.get('data_ref'):
            ref = expr['data_ref']
            return field(ref['value'], 'source-literal', {'kind': 'json', 'file': ref['file'], 'line': ref.get('line'),
                         'pointer': ref['pointer'], 'via': source[:120]}, 0.9)
        keys = expr.get('i18n_keys') or []
        for key in keys:  # catalog lookups first (namespaced keys come before bare ones)
            entry = self.ctx.i18n(key)
            if entry:
                return field(entry['value'], 'source-i18n', {'kind': 'i18n', 'file': entry['file'], 'line': entry['line'],
                             'i18n_key': key, 'pointer': entry.get('pointer'), 'via': source[:120]}, 0.9)
        for key in keys:
            if ' ' in key or not re.fullmatch(r'[\w.\-:]+', key):
                # gettext style: the literal msgid is the visible default-language copy.
                return field(key, 'source-literal', edit_of(element, 'i18n', i18n_key=key, via='gettext msgid'), 0.85)
        branches = TERNARY.match(source)
        if branches:
            # Both branches are candidates; the rendered lens (or a human) picks the live one.
            edit = edit_of(element, 'literal', attr=attr) if attr else edit_of(element, 'computed')
            return field(None, 'source-expression', edit | {'via': source[:120]}, 0.4, conditional=True,
                         candidates=[{'value': branches.group(2), 'branch': 'then', **edit}, {'value': branches.group(4), 'branch': 'else', **edit}])
        block = BLOCK_USE.search(source)
        if block:
            name = block.group(1) or block.group(2) or block.group(4)
            if name in self.blocks:
                literal, file, line = self.blocks[name]
                if literal is not None:
                    return field(literal, 'source-literal', {'kind': 'literal', 'file': file, 'line': line, 'via': 'template block ' + name}, 0.9)
                return field(None, 'source-expression', {'kind': 'computed', 'file': file, 'line': line, 'via': 'template block ' + name}, 0.4)
            if block.group(3) is not None:
                return field(block.group(3), 'source-literal', edit_of(element, 'literal', via='@yield default'), 0.8)
        name = prop_name(source)
        if name:
            usages = self.prop_usages(element)
            values = []
            for usage in usages:
                attr_value = usage['attrs'].get(name)
                if attr_value and 'literal' in attr_value:
                    values.append((attr_value['literal'], usage))
                elif name == 'children' and usage.get('text_literal'):
                    values.append((usage['text_literal'], usage))
            def call_site(usage):
                extra = {'attr': name} if name in usage['attrs'] else {}
                return edit_of(usage, 'prop', prop=name, via=f'<{usage["tag"]} {name}=...> -> {element["file"]}',
                               prop_flow={'component': usage['tag'], 'prop': name, 'defined_in': element['file'],
                                          'call_site': {'file': usage['file'], 'line': usage['line'], 'col': usage.get('col', 0)}}, **extra)
            if len({v for v, _ in values}) == 1:
                value, usage = values[0]
                edit = call_site(usage)
                if len(values) > 1:
                    edit['also_at'] = [{'file': u['file'], 'line': u['line']} for _, u in values[1:10]]
                return field(value, 'source-literal', edit, 0.9)
            if len(values) > 1:
                return field(None, 'source-expression', edit_of(element, 'prop', prop=name), 0.3,
                             candidates=[{'value': v, **call_site(u)} for v, u in values[:10]])
        fm = front_matter_var(source)
        if fm:
            scope, key = fm
            if scope == 'page' and key in self.front:
                value, line = self.front[key]
                return field(value, 'front-matter', {'kind': 'markdown', 'file': self.page['template'], 'line': line, 'pointer': '/' + key}, 0.9)
            if scope == 'site' and key in self.ctx.site_config:
                entry = self.ctx.site_config[key]
                return field(entry['value'], 'front-matter', {'kind': 'yaml' if entry['file'].endswith(('.yml', '.yaml')) else 'toml',
                             'file': entry['file'], 'line': entry['line'], 'pointer': '/' + key}, 0.85)
        if CMS_EXPR.search(source):
            return field(None, 'source-expression', edit_of(element, 'cms', via=source[:120]), 0.5)
        url = static_url(source, self.ctx)
        if url is not None and attr in {'href', 'src', 'to', 'action', 'content'}:
            return field(url, 'source-literal', edit_of(element, 'literal', via=source[:120]), 0.85)
        imported = imported_asset(source, element, self.ctx)
        if imported:
            return field(imported, 'source-literal', {'kind': 'literal', 'file': imported, 'line': 1, 'via': 'imported asset'}, 0.8)
        return field(None, 'source-expression', edit_of(element, 'computed', via=source[:160]), 0.3)

    def text_field(self, element, _depth=0):
        parts = element.get('text', [])
        if element.get('text_literal') and not any('expr' in part or part.get('via') == 'const' for part in parts):
            return field(element['text_literal'], 'source-literal', edit_of(element), 0.95 if element.get('contiguous', True) else 0.85)
        if not parts and not element.get('text_literal') and _depth < 6:
            # `<a><span>{item.label}</span></a>`: the visible text lives in exactly one descendant.
            bearing = [c for c in self.ctx.children.get(element['id'], []) if c.get('text') or c.get('text_literal')
                       or self.ctx.children.get(c['id'])]
            if len(bearing) == 1:
                return self.text_field(bearing[0], _depth + 1)
        exprs = [p for p in parts if 'expr' in p]
        if len(exprs) == 1 and exprs[0].get('instances') and not any('literal' in p for p in parts):
            return self.resolve_expr(exprs[0], element)
        resolved, edits, unresolved = [], [], []
        skipping = False
        for part in parts:
            source = part.get('expr', '')
            if skipping:
                if re.search(r'endblock|@endsection|@show|\{\{-?\s*end\s*-?\}\}', source):
                    skipping = False
                continue
            if 'literal' in part and part.get('via') == 'const':
                const = part.get('const') or {}
                resolved.append(part['literal'])
                edits.append(field(part['literal'], 'source-literal',
                                   {'kind': 'literal', 'file': const.get('file', element['file']), 'pointer': const.get('pointer'),
                                    'via': 'const ' + const.get('name', ''), **{k: part[k] for k in SPAN_KEYS if k in part}}, 0.9))
            elif 'literal' in part:
                resolved.append(part['literal'])
            else:
                result = self.resolve_expr(part, element)
                if result['value'] is not None and 'template block' in (result['edit'] or {}).get('via', '') \
                        and re.search(r'\{%-?\s*block\b', source):
                    skipping = True  # the block's default body is replaced by the resolved override
                if result['value'] is not None:
                    resolved.append(result['value'])
                    edits.append(result)
                elif not re.fullmatch(r'\s*(?:\{%-?\s*(?:end\w*|else|block\s+\w+|endblock)\b.*|@(?:end)?\w+.*)', part.get('expr', '')):
                    unresolved.append(result)
        if not parts and element.get('text_literal'):
            return field(element['text_literal'], 'source-literal', edit_of(element), 0.8, via='descendant text')
        if resolved and not unresolved:
            value = norm_text(' '.join(resolved))
            if len(edits) == 1 and not any('literal' in p and p.get('via') != 'const' for p in parts):
                return edits[0] | {'value': value}
            return field(value, 'source-literal', (edits[0]['edit'] if edits else edit_of(element)), 0.8 if edits else 0.95,
                         **({'composed_from': [e['edit'] for e in edits]} if edits else {}))
        if unresolved:
            primary = next((u for u in unresolved if u['edit']['kind'] in {'markdown', 'prop', 'cms'}), unresolved[0])
            partial = norm_text(' '.join(resolved)) or None
            return primary | ({'partial_literal': partial} if partial else {})
        return field(element.get('text_literal'), 'source-literal' if element.get('text_literal') else 'none', edit_of(element), 0.5)

    def attr_field(self, element, name):
        value = element['attrs'].get(name)
        if value is None:
            return None
        if 'literal' in value:
            return field(value['literal'], 'source-literal', edit_of(element, attr=name), 0.95)
        if value.get('flag'):
            return field('', 'source-literal', edit_of(element, attr=name), 0.9)
        return self.resolve_expr(value, element, attr=name)


def prop_name(source):
    """`{title}`, `{props.title}`, `{{ title }}`, `{this.props.title}`, `Astro.props.title`, `{$props.title}` -> title."""
    match = re.fullmatch(r'\s*\{\{?\s*(?:(?:this\.)?props\.|\$props\.|Astro\.props\.|data\.)?([A-Za-z_]\w*)\s*\}?\}\s*', source)
    if match and match.group(1) not in {'true', 'false', 'null', 'undefined', 'children'} | {'content'}:
        return match.group(1)
    if re.fullmatch(r'\s*\{\s*(?:props\.)?children\s*\}\s*', source):
        return 'children'
    return None


def front_matter_var(source):
    match = re.search(r'''\{\{-?\s*(page|site|\.Site|\.Params|\.?)\.?(title|description|Title|Description|summary|Summary)\b''', source)
    if not match:
        match = re.search(r'''\{\{-?\s*\.(Title|Description|Summary)\b''', source)
        return ('page', match.group(1).lower()) if match else None
    scope = match.group(1).lower()
    key = match.group(2).lower()
    if scope in {'site', '.site'}:
        return 'site', key
    return 'page', key


def static_url(source, ctx):
    for regex, prefix in STATIC_URL:
        match = regex.search(source)
        if match:
            if prefix == '/':
                return '/'
            value = match.group(1)
            if prefix and not value.startswith('/'):
                return prefix + value
            return value if value.startswith(('/', 'http', '#', 'mailto:', 'tel:')) else '/' + value
    named = NAMED_ROUTE.search(source)
    if named:
        name = next(g for g in named.groups() if g)
        if name in ctx.named_routes:
            route = ctx.named_routes[name]
            return route if route.startswith('/') else '/' + route
    match = re.search(r'''^\s*\{\s*['"`](/[^'"`$]*)['"`]\s*\}\s*$''', source)
    return match.group(1) if match else None


def imported_asset(source, element, ctx):
    match = re.fullmatch(r'\s*\{\s*([A-Za-z_$][\w$]*)(?:\.src)?\s*\}\s*', source)
    if not match:
        return None
    target = ctx.imports.get(element['file'], {}).get(match.group(1))
    if target and target in ctx.assets:
        return target
    if target and re.search(r'\.(?:png|jpe?g|gif|webp|avif|svg)$', target or ''):
        candidate = posixpath.normpath(posixpath.join(posixpath.dirname(element['file']), target))
        return candidate
    return None


def page_front_matter(source):
    match = re.match(r'\A---\s*\n(.*?)\n---', source, re.S)
    fields = {}
    if match:
        for number, line in enumerate(match.group(1).splitlines(), 2):
            pair = re.match(r'''^(\w+)\s*:\s*["']?(.*?)["']?\s*$''', line)
            if pair and pair.group(2):
                fields[pair.group(1).lower()] = (pair.group(2), number)
    toml = re.match(r'\A\+\+\+\s*\n(.*?)\n\+\+\+', source, re.S)
    if toml:
        for number, line in enumerate(toml.group(1).splitlines(), 2):
            pair = re.match(r'''^(\w+)\s*=\s*["'](.*?)["']\s*$''', line)
            if pair:
                fields[pair.group(1).lower()] = (pair.group(2), number)
    return fields


def hugo_layouts(page, files):
    template = page['template']
    if not template.startswith('content/'):
        return []
    section = template.split('/')[1] if template.count('/') > 1 else ''
    is_list = PurePosixPath(template).stem == '_index'
    chain = ['layouts/_default/baseof.html']
    if page['url'] == '/' and 'layouts/index.html' in files:
        chain.append('layouts/index.html')
    else:
        kind = 'list' if is_list else 'single'
        chain.append(next((c for c in (f'layouts/{section}/{kind}.html', f'layouts/_default/{kind}.html') if c in files), f'layouts/_default/{kind}.html'))
    return [c for c in chain if c in files]


def composition_for(ctx, page):
    template = page['template']
    layouts = list(layout_chain(template, ctx.files, ctx.fingerprint))
    if ctx.fingerprint.get('primary') == 'hugo':
        layouts = hugo_layouts(page, ctx.files) + layouts
    others = ctx.page_templates - {template}
    page_files = set(page_composition(ctx.index, template, exclude=others))
    composition = page_composition(ctx.index, template, layouts=layouts, exclude=others)
    # Inheritance parents found through refs (extends/front-matter layout) are shared shells.
    shells = {ref['resolved'] for ref in ctx.index['refs'] if ref['file'] in page_files and ref.get('resolved')
              and ref['kind'] in {'template-include', 'front-matter-layout', 'blade-include', 'hbs-layout', 'liquid-layout', 'razor-layout', 'wp-part'}
              and re.search(r'extends|layout|header|footer|base', ref['kind'] + ' ' + (ref['spec'] or '') + ' ' + ref['resolved'])}
    page_files -= shells
    return composition, page_files


def scope_to_entry(ctx, page, elements):
    """Pathname-switch / in-file dispatch SPAs (PR #8 App.jsx: `path === '/about' ? <AboutPage/> : ...`):
    several routes share one template file, each rendering its own component. Keep only elements of the
    route's entry component (plus the components it renders) and of shared non-entry components (App
    shell, header), so /about does not inherit /'s headings and links."""
    entries = getattr(ctx, 'entry_components', {}).get(page.get('template')) or set()
    mine = page.get('entry_component')
    if not mine or len(entries) < 2:
        return elements
    template = page['template']
    local = [e for e in elements if e['file'] == template]
    fns = {e['component_fn'] for e in local if e.get('component_fn')}
    renders = defaultdict(set)
    for element in local:
        if element.get('component_fn') and element['tag'] in fns:
            renders[element['component_fn']].add(element['tag'])

    def closure(starts, stop=frozenset()):
        seen, todo = set(), list(starts)
        while todo:
            fn = todo.pop()
            if fn in seen or fn in stop:
                continue
            seen.add(fn)
            todo.extend(renders.get(fn, ()))
        return seen

    from_entries = closure(entries)
    roots = fns - from_entries
    allowed = closure({mine}) | closure(roots, stop=frozenset(entries))

    def dispatch_branch(element):
        """Inline JSX in the dispatcher that only renders for other paths (the not-found fallback)."""
        condition = element.get('condition') or ''
        return bool(element.get('conditional') and ROUTE_TEST.search(condition))

    return [e for e in elements if e['file'] != template or (
        (not e.get('component_fn') or e['component_fn'] not in fns or e['component_fn'] in allowed)
        and not (e.get('component_fn') in roots and dispatch_branch(e)))]


ROUTE_TEST = re.compile(r'''(?:pathname|\bpath|\broute|\burl)\b[^'"`]*(?:===?|startsWith\s*\(|match\s*\()\s*['"`]/''')


def extract_page(ctx, page):
    """Source-only page model with edit locations."""
    composition, page_files = ctx.composition(page)
    front = page_front_matter(ctx.files.get(page['template'], ''))
    resolver = PageResolver(ctx, page, composition, page_files, front)
    order = {file: index for index, file in enumerate(composition)}
    elements = [e for f in composition for e in ctx.by_file.get(f, [])]
    elements = scope_to_entry(ctx, page, elements)
    elements.sort(key=lambda e: (order[e['file']], e['line'], e['col']))
    in_head = set()
    for element in elements:
        parent = ctx.by_id.get(element.get('parent'))
        if element['tag'].lower() in {'head', 'svelte:head', 'helmet', 'head'} or element['tag'] in {'Head', 'Helmet', 'Html', 'MetaTags'} \
                or (parent and parent['id'] in in_head):
            in_head.add(element['id'])
    head = head_fields(ctx, page, resolver, elements, in_head, front)
    headings, links, images, blocks = [], [], [], []
    for element in elements:
        if element['id'] in in_head or element['tag'].lower() in {'script', 'style', 'template', 'svg', 'noscript'}:
            continue
        if element.get('heading_level'):
            for text in expand_usages(resolver.text_field(element)):
                headings.append({'level': element['heading_level'], 'text': text['value'], 'el': element['id'], 'method': text['method'],
                                 'edit': text['edit'], 'confidence': text['confidence'], 'section': element['section'],
                                 **({'heading_method': element['heading_method']} if element.get('heading_method') != 'tag' else {})})
        if element['tag'] in LINK_TAGS and ('href' in element['attrs'] or 'to' in element['attrs']):
            href_attr = 'href' if 'href' in element['attrs'] else 'to'
            href = resolver.attr_field(element, href_attr)
            anchor = resolver.text_field(element)
            if anchor['value'] is None and not anchor.get('candidates'):
                label_attr = 'aria-label' if 'aria-label' in element['attrs'] else 'title'
                label = element['attrs'].get(label_attr)
                if label and 'literal' in label:
                    anchor = field(label['literal'], 'source-literal', edit_of(element, attr=label_attr), 0.8)
            for href_i, anchor_i in paired(href, anchor):
                links.append({'href': href_i['value'], 'anchor': anchor_i['value'], 'rel': literal_attr(element, 'rel'),
                              'target': literal_attr(element, 'target'), 'section': element['section'],
                              'in_main': element['section'] not in CHROME,
                              'inside_main_landmark': inside_tag(ctx, element, 'main', composition),
                              'kind': link_kind(href_i['value'], ctx.own_hosts),
                              'el': element['id'], 'edit': href_i['edit'], 'anchor_edit': anchor_i['edit'], 'method': href_i['method'],
                              **({'aria_label': literal_attr(element, 'aria-label')} if literal_attr(element, 'aria-label') else {})})
        if element['tag'] in IMAGE_TAGS:
            src = resolver.attr_field(element, 'src') or field(None, 'none', edit_of(element, 'add', attr='src'))
            alt = resolver.attr_field(element, 'alt')
            for src_i, alt_i in paired(src, alt or field(None, 'none', None)):
                asset = asset_for(ctx, src_i['value'], src_i.get('edit'))
                images.append({'src': src_i['value'], 'alt': alt_i['value'] if alt else None,
                               'alt_missing': alt is None, 'alt_status': alt_status(alt, alt_i),
                               'width': literal_attr(element, 'width'), 'height': literal_attr(element, 'height'),
                               'loading': literal_attr(element, 'loading'), 'priority': 'priority' in element['attrs'] or literal_attr(element, 'fetchpriority') == 'high',
                               'srcset': literal_attr(element, 'srcset') is not None, 'section': element['section'],
                               'el': element['id'], 'edit': src_i['edit'], 'alt_edit': alt_i['edit'] if alt else edit_of(element, 'add', attr='alt'),
                               'component': element['tag'] if element['kind'] == 'component' else None, **asset})
        if element['tag'] in BLOCK_TAGS:
            for text in expand_usages(resolver.text_field(element)):
                if text['value']:
                    blocks.append({'el': element['id'], 'tag': element['tag'], 'text': text['value'], 'section': element['section'],
                                   'edit': text['edit'], 'method': text['method']})
    # Markdown pages: the body is content even without wrapping elements.
    main_words = sum(len(b['text'].split()) for b in blocks if b['section'] not in CHROME)
    limitations = list(resolver.limitations)
    if any(e.get('conditional') for e in elements):
        limitations.append('Source lens: conditionally rendered elements (ternary / &&) are listed from every branch; '
                           'their `condition` is recorded on the element. Use a rendered lens to see the live branch.')
    if any(e.get('in_loop') and not e.get('data_source') for e in elements):
        limitations.append('Source lens: some looped templates iterate runtime/CMS data; their per-instance values are unknown.')
    mark_scope(ctx, page, composition, page_files, [head, headings, links, images, blocks])
    if any(e['kind'] == 'component' and not e.get('component_ref') and e['tag'][:1].isupper() for e in elements):
        limitations.append('Some components could not be resolved to source (external packages or dynamic imports).')
    return {
        'composition': composition,
        'head': head,
        'headings': headings,
        'content': {'main_words': main_words, 'blocks': blocks[:400],
                    'add_target': body_add_location(ctx, page, composition, page_files, elements, in_head)},
        'links': {'out': links, 'in_count': None, 'click_depth': None},
        'images': images,
        'limitations': limitations,
    }


def expand_usages(result):
    """A component rendered N times with different literal props, or a looped template over N data rows,
    yields N values. Conditional branches (ternaries) stay one field with candidates."""
    candidates = result.get('candidates') or []
    if result.get('conditional'):
        return [result]
    if result.get('value') is None and candidates and all(c.get('value') is not None for c in candidates):
        return [field(c['value'], 'source-literal', {k: v for k, v in c.items() if k != 'value'}, 0.85,
                      via='loop instance' if c.get('in_loop') and c.get('pointer') else 'component usage')
                for c in candidates]
    return [result]


def paired(first, second):
    """Expand two fields of one element (href + anchor, src + alt) into aligned per-instance pairs."""
    a, b = expand_usages(first), expand_usages(second)
    if len(a) == len(b):
        return list(zip(a, b))
    if len(b) == 1:
        return [(x, b[0]) for x in a]
    if len(a) == 1:
        return [(a[0], y) for y in b]
    return [(first, second)]


def alt_status(alt, value):
    if alt is None:
        return 'missing'
    if value.get('value') == '':
        return 'empty'
    if value.get('value') is None:
        return 'expression'
    return 'literal'


def _edits(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in {'edit', 'anchor_edit', 'alt_edit'} and isinstance(value, dict) and value.get('file'):
                yield value
            elif isinstance(value, (dict, list)) and key not in {'add_target'}:
                yield from _edits(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _edits(item)


def mark_scope(ctx, page, composition, page_files, groups):
    """Flag edits whose file is rendered on more than this page (layouts, shells, shared components)."""
    users = ctx.file_pages
    for edit in _edits(groups):
        file = edit['file']
        pages = users.get(file) or []
        if file in composition and (file not in page_files or len(pages) > 1 or file in ctx.shells):
            edit['shared'] = True
            if len(pages) > 1:
                edit['used_by_pages'] = pages[:10]


def inside_tag(ctx, element, tag, composition):
    """True when `element` renders inside <tag>, following parents and then component call sites."""
    seen = set()
    current = element
    while current is not None and current['id'] not in seen:
        seen.add(current['id'])
        if current['tag'].lower() == tag and current is not element:
            return True
        parent = ctx.by_id.get(current.get('parent') or '')
        if parent is None:
            usage = next((u for u in ctx.usages.get(current['file'], []) if u['file'] in composition), None)
            parent = usage
        current = parent
    return False


def ancestor_tag(ctx, element, tags):
    parent = ctx.by_id.get(element.get('parent') or '')
    while parent is not None:
        if parent['tag'].lower() in tags:
            return True
        parent = ctx.by_id.get(parent.get('parent') or '')
    return False


def literal_attr(element, name):
    value = element['attrs'].get(name)
    if value is None:
        return None
    return value.get('literal', True if value.get('flag') else None)


def link_kind(href, own_hosts=()):
    if href is None:
        return 'unresolved'
    if href.startswith(('mailto:', 'tel:', 'javascript:', 'sms:')):
        return 'action'
    if href.startswith('#'):
        return 'fragment'
    if re.match(r'^[a-z][a-z0-9+.-]*://', href, re.I) or href.startswith('//'):
        host = (urlsplit(href if '://' in href else 'https:' + href).hostname or '').lower()
        return 'internal' if host in own_hosts else 'absolute'
    if re.search(r'\.(?:pdf|png|jpe?g|gif|svg|webp|zip|mp4|mp3|css|js)$', href.split('?')[0], re.I):
        return 'resource'
    return 'internal'


def asset_for(ctx, src, edit):
    if not src:
        return {}
    candidates = []
    clean = src.split('?')[0].lstrip('/')
    if edit and edit.get('via') == 'imported asset':
        candidates.append(edit['file'])
    candidates += [clean, 'public/' + clean, 'static/' + clean, 'assets/' + clean, 'src/assets/' + clean, 'images/' + clean,
                   re.sub(r'^static/', 'static/', clean)]
    for candidate in candidates:
        if candidate in ctx.assets:
            asset = ctx.assets[candidate]
            return {'asset_file': candidate, 'size_bytes': asset.get('size_bytes'),
                    'intrinsic': [asset.get('width'), asset.get('height')] if asset.get('width') else None,
                    'format': PurePosixPath(candidate).suffix.lstrip('.').lower()}
    return {}


def head_fields(ctx, page, resolver, elements, in_head, front):
    head = {'title': field(), 'description': field(), 'canonical': field(), 'robots': field(), 'lang': field(),
            'viewport': field(), 'og': {}, 'twitter': {}, 'hreflang': [], 'json_ld': [], 'keywords': field()}
    best = {}

    def offer(key, value, element=None):
        """Keep the most specific declaration: page subtree beats shared layouts; later beats earlier."""
        rank = resolver.rank(element) if element is not None else 2
        if value is None or (value['value'] is None and value['edit'] is None):
            return
        current = best.get(key)
        if current is None or rank >= current[0]:
            best[key] = (rank, value)
    for element in elements:
        tag = element['tag'].lower()
        if tag == 'title' and not ancestor_tag(ctx, element, {'svg'}):
            offer('title', resolver.text_field(element), element)
        elif tag == 'meta':
            name = (literal_attr(element, 'name') or literal_attr(element, 'property') or '')
            if not isinstance(name, str):
                continue
            name = name.lower()
            content = resolver.attr_field(element, 'content')
            if name in {'description', 'robots', 'viewport', 'keywords'}:
                offer(name, content, element)
            elif name.startswith('og:'):
                offer('og:' + name[3:], content, element)
            elif name.startswith('twitter:'):
                offer('twitter:' + name[8:], content, element)
        elif tag == 'link':
            rel = literal_attr(element, 'rel')
            if isinstance(rel, str) and 'canonical' in rel.lower().split():
                offer('canonical', resolver.attr_field(element, 'href'), element)
            elif isinstance(rel, str) and 'alternate' in rel.lower() and element['attrs'].get('hreflang'):
                href = resolver.attr_field(element, 'href')
                head['hreflang'].append({'hreflang': literal_attr(element, 'hreflang'), 'href': href['value'], 'edit': href['edit']})
        elif tag == 'html' and element['attrs'].get('lang'):
            offer('lang', resolver.attr_field(element, 'lang'), element)
        elif tag == 'script' and (literal_attr(element, 'type') or '') == 'application/ld+json':
            raw = element.get('raw_text') or ''
            types = sorted(set(re.findall(r'''["']@type["']\s*:\s*["'](\w+)["']''', raw)))
            head['json_ld'].append({'types': types, 'templated': bool(re.search(r'\{\{|\{%|<\?', raw)),
                                    'edit': edit_of(element, 'literal' if raw and not re.search(r'\{\{|\{%|<\?', raw) else 'computed')})
    # JS metadata APIs, layouts first so page declarations override.
    template_obj = {}
    for file in resolver.composition:
        source = ctx.files.get(file, '')
        for api, obj, offset in metadata_objects(file, source, ctx._meta_cache):
            rank = 2 if file in resolver.page_files else 1
            apply_metadata(api, obj, offset, file, rank, offer, template_obj, head)
    if 'title' in best and template_obj.get('template') and best['title'][0] == 2 and best['title'][1]['value']:
        rank, value = best['title']
        pattern, source_edit = template_obj['template']
        if '%s' in pattern and not template_obj.get('absolute'):
            best['title'] = (rank, value | {'value': pattern.replace('%s', value['value']), 'via_template': {'pattern': pattern, **source_edit}})
    elif 'title' not in best and template_obj.get('default'):
        best['title'] = (1, template_obj['default'])
    # Markdown/content pages: front matter supplies title/description to layouts.
    for key in ('title', 'description'):
        if key in front and (key not in best or best[key][1]['value'] is None or best[key][1]['edit']['kind'] == 'markdown'):
            value, line = front[key]
            composite = best.get(key, (0, None))[1]
            edit = {'kind': 'markdown', 'file': page['template'], 'line': line, 'pointer': '/' + key}
            if composite and composite.get('edit', {}).get('kind') == 'markdown':
                best[key] = (2, composite)
            elif key not in best:
                best[key] = (2, field(value, 'front-matter', edit, 0.7, note='front matter value; the layout may decorate it'))
    for key, (_, value) in best.items():
        if ':' in key:
            group, name = key.split(':', 1)
            head[group][name] = value
        else:
            head[key] = value
    head['add_target'] = add_location(ctx, page, resolver)  # where to add any missing head tag (og:*, canonical, ...)
    if head['description']['value'] is None and head['description']['edit'] is None:
        head['description'] = field(None, 'none', add_location(ctx, page, resolver), 0.6)
    if head['title']['value'] is None and head['title']['edit'] is None:
        head['title'] = field(None, 'none', add_location(ctx, page, resolver), 0.6)
    return head


def apply_metadata(api, obj, offset, file, rank, offer, template_obj, head):
    if obj is None:
        return
    if isinstance(obj, list):  # Remix meta / useHead meta arrays
        for item in obj:
            if not isinstance(item, dict):
                continue
            if string_at(item, 'title'):
                offer_value('title', string_at(item, 'title'), file, offset, rank, offer, api)
            name = (string_at(item, 'name') or string_at(item, 'property') or {}).get('__str__', '').lower()
            content = string_at(item, 'content')
            if name and content:
                key = name if name in {'description', 'robots', 'keywords'} else ('og:' + name[3:] if name.startswith('og:') else
                      'twitter:' + name[8:] if name.startswith('twitter:') else None)
                if key:
                    offer_value(key, content, file, offset, rank, offer, api)
        return
    if not isinstance(obj, dict):
        return
    if '__str__' in obj or '__expr__' in obj:
        if api == 'setTitle':
            offer_value('title', obj, file, offset, rank, offer, api)
        elif api.startswith('function-'):
            offer('title', field(None, 'source-expression', {'kind': 'computed', 'file': file, 'line': obj['line'] + offset, 'via': api}, 0.3), {'file': file})
        return
    if api in {'updateTag', 'addTag'}:
        name = (string_at(obj, 'name') or string_at(obj, 'property') or {}).get('__str__', '').lower()
        if name in {'description', 'robots', 'keywords'} or name.startswith(('og:', 'twitter:')):
            key = name if ':' not in name else name.split(':')[0] + ':' + name.split(':', 1)[1]
            offer_value(key, string_at(obj, 'content'), file, offset, rank, offer, api)
        return
    title = obj.get('title')
    if isinstance(title, dict) and title.get('__obj__'):
        if string_at(title, 'template'):
            template_obj['template'] = (string_at(title, 'template').get('__str__', ''),
                                        {'file': file, 'line': title['template']['line'] + offset})
        if string_at(title, 'default'):
            template_obj['default'] = value_field(string_at(title, 'default'), file, offset, api)
        if string_at(title, 'absolute'):
            offer_value('title', string_at(title, 'absolute'), file, offset, rank, offer, api)
            template_obj['absolute'] = True
    elif isinstance(title, dict):
        offer_value('title', title, file, offset, rank, offer, api)
    elif api == 'useHead' and isinstance(obj.get('titleTemplate'), dict) and '__str__' in obj['titleTemplate']:
        template_obj['template'] = (obj['titleTemplate']['__str__'], {'file': file, 'line': obj['titleTemplate']['line'] + offset})
    mapping = {'description': 'description', 'keywords': 'keywords', 'ogTitle': 'og:title', 'ogDescription': 'og:description',
               'ogImage': 'og:image', 'ogUrl': 'og:url', 'twitterCard': 'twitter:card', 'twitterTitle': 'twitter:title',
               'twitterDescription': 'twitter:description', 'twitterImage': 'twitter:image', 'robots': 'robots'}
    for key, target in mapping.items():
        if isinstance(obj.get(key), dict) and ('__str__' in obj[key] or '__expr__' in obj[key]):
            offer_value(target, obj[key], file, offset, rank, offer, api)
    canonical = string_at(obj, 'alternates', 'canonical')
    if canonical:
        offer_value('canonical', canonical, file, offset, rank, offer, api)
    for group, prefix in (('openGraph', 'og:'), ('twitter', 'twitter:')):
        nested = obj.get(group)
        if isinstance(nested, dict) and nested.get('__obj__'):
            for key in ('title', 'description', 'url', 'type', 'siteName', 'card', 'site', 'creator'):
                if string_at(nested, key):
                    offer_value(prefix + re.sub(r'([A-Z])', r'_\1', key).lower(), string_at(nested, key), file, offset, rank, offer, api)
            images = nested.get('images')
            first = images[0] if isinstance(images, list) and images else images
            if isinstance(first, dict) and ('__str__' in first or string_at(first, 'url')):
                offer_value(prefix + 'image', first if '__str__' in first else string_at(first, 'url'), file, offset, rank, offer, api)
    robots = obj.get('robots')
    if isinstance(robots, dict) and robots.get('__obj__'):
        parts = [('index' if v.get('__expr__') == 'true' else 'noindex') if k == 'index' else
                 ('follow' if v.get('__expr__') == 'true' else 'nofollow') if k == 'follow' else None
                 for k, v in robots.items() if isinstance(v, dict) and k in {'index', 'follow'}]
        if any(parts):
            offer('robots', field(', '.join(p for p in parts if p), 'source-literal', {'kind': 'literal', 'file': file, 'line': robots['line'] + offset, 'via': api}, 0.85), {'file': file})
    if isinstance(obj.get('meta'), list):
        apply_metadata(api, obj['meta'], offset, file, rank, offer, template_obj, head)


def value_field(value, file, offset, api):
    if '__str__' in value:
        return field(value['__str__'], 'source-literal', {'kind': 'literal', 'file': file, 'line': value['line'] + offset,
                     'col': value.get('col', 0), 'via': api}, 0.95)
    return field(None, 'source-expression', {'kind': 'computed', 'file': file, 'line': value['line'] + offset, 'via': api + ': ' + value['__expr__'][:120]}, 0.3)


def offer_value(key, value, file, offset, rank, offer, api):
    # A stand-in element carrying only the file lets `offer` rank layout vs page declarations.
    offer(key, value_field(value, file, offset, api), {'file': file})


HEAD_CLOSE = re.compile(r'</head\s*>', re.I)
HEAD_BLOCK = re.compile(r'''\{%-?\s*block\s+(?:extra_?head|head(?:_extra)?|meta|extrahead|seo|title)\b[^%]*%\}|@section\s*\(\s*['"](?:head|meta|seo|title)['"]\s*\)|@push\s*\(\s*['"](?:head|meta|seo)['"]\s*\)|\{\{-?\s*define\s+"(?:head|meta)"\s*-?\}\}''')
SPA_FAMILIES = {'react-spa', 'vue-spa', 'svelte-spa', 'preact-spa', 'solid-spa', 'angular', 'spa'}
CMS_FAMILIES = {'shopify-theme', 'wordpress-theme', 'wordpress-block-theme', 'ghost-theme', 'drupal', 'magento'}
CONTENT_SUFFIXES = ('.md', '.markdown', '.mdx')


def line_col(source, offset):
    line = source.count('\n', 0, offset) + 1
    return line, offset - (source.rfind('\n', 0, offset) + 1)


def after_imports(source):
    """(line, col) just after the last top-level import statement, or None when the file has none."""
    last, in_import = None, False
    for number, line in enumerate(source.splitlines(), 1):
        stripped = line.strip()
        if not in_import and re.match(r'import\b', stripped):
            in_import = True
        if in_import:
            if re.search(r'''(?:from\s+)?["'][^"']+["']\s*;?\s*$''', stripped) or re.match(r'''import\s+["']''', stripped):
                in_import, last = False, (number, len(line))
        elif stripped and not stripped.startswith(('//', '/*', '*', "'use ", '"use ')):
            if last is not None:
                break
    return last


def target(kind, file, line, col, position, anchor, scope, recipe):
    return {'kind': kind, 'file': file, 'line': line, 'col': col, 'position': position, 'anchor': anchor,
            'scope': scope, 'recipe': recipe}


def api_target(source, file, regex, anchor, recipe):
    """Existing metadata declaration -> insert inside it; else after imports; else at file start."""
    match = re.search(regex, source, re.M)
    if match:
        line, col = line_col(source, match.start())
        return target('add', file, line, col, 'inside-start', anchor, 'route', recipe)
    imports = after_imports(source)
    if imports:
        return target('add', file, imports[0], imports[1], 'after', 'last import', 'route', recipe)
    return target('add', file, 1, 0, 'before', 'start-of-file', 'route', recipe)


def front_matter_target(source, file):
    match = re.match(r'\A(---|\+\+\+)\s*\n.*?\n(\1)\s*(?:\n|\Z)', source, re.S)
    if match:
        line, col = line_col(source, match.start(2))
        return target('markdown', file, line, col, 'before', match.group(1), 'route', 'add the key to the page front matter')
    return target('markdown', file, 1, 0, 'before', 'start-of-file', 'route', 'add a front matter block (---) with the key')


def head_close_target(ctx, files, kind, scope_of, recipe):
    for file in files:
        source = ctx.files.get(file, '')
        match = HEAD_CLOSE.search(source)
        if match:
            line, col = line_col(source, match.start())
            return target(kind, file, line, col, 'before', '</head>', scope_of(file), recipe)
    return None


def add_location(ctx, page, resolver):
    """Where to ADD a missing head value (title, description, canonical, og:*, JSON-LD) for this stack.

    Always a real insertion point (file + line + col + position relative to an anchor), never a
    `line: 1` placeholder. `scope` says who else is affected: `route` (this page only), `layout`
    (pages sharing the layout) or `shell` (every route: SPA index.html, theme.liquid, header.php).
    """
    primary = ctx.fingerprint.get('primary') or ''
    template = page.get('template') or ''
    source = ctx.files.get(template, '')
    composition = list(resolver.composition)
    page_files = resolver.page_files

    def scope_of(file):
        if file == template or (file in page_files and len(ctx.file_pages.get(file, [])) <= 1):
            return 'route'
        if file in ctx.shells or primary in SPA_FAMILIES or file.endswith(('index.html', 'app.html')):
            return 'shell'
        return 'layout'

    if template.endswith(CONTENT_SUFFIXES):
        return front_matter_target(source, template)
    if primary == 'next' and re.search(r'(?:^|/)app/', template):
        return api_target(source, template, r'^export\s+(?:const\s+metadata\b|(?:async\s+)?function\s+generateMetadata\b)',
                          'export const metadata', 'export const metadata: Metadata = { title, description, alternates: { canonical }, openGraph }')
    if primary == 'next':
        head = next((e for e in ctx.by_file.get(template, []) if e['tag'] == 'Head'), None)
        if head is not None:
            return target('add', template, head['line'], head.get('col', 0), 'inside-start', '<Head>', 'route', '<meta> / <link> children of next/head <Head>')
        return api_target(source, template, r'^import\s+Head\b', 'import Head', "import Head from 'next/head' and render <Head> in the page")
    if primary == 'remix':
        return api_target(source, template, r'^export\s+(?:const|function|let)\s+meta\b', 'export const meta', 'export const meta: MetaFunction = () => [...]')
    if primary == 'gatsby':
        return api_target(source, template, r'^export\s+(?:const|function)\s+Head\b', 'export const Head', 'export const Head = () => <><title/>...</>')
    if primary == 'nuxt' and template.endswith('.vue'):
        match = re.search(r'\b(?:useSeoMeta|useHead|useServerSeoMeta)\s*\(', source) or re.search(r'<script\b[^>]*\bsetup\b[^>]*>', source)
        if match:
            line, col = line_col(source, match.end() if match.group(0).startswith('<script') else match.start())
            position = 'after' if match.group(0).startswith('<script') else 'inside-start'
            return target('add', template, line, col, position, match.group(0)[:40], 'route', 'useSeoMeta({ title, description, ogTitle, ... })')
        tpl = re.search(r'<template\b', source)
        line, col = line_col(source, tpl.start()) if tpl else (1, 0)
        return target('add', template, line, col, 'before', '<template>', 'route', '<script setup>useSeoMeta({ ... })</script>')
    if primary == 'sveltekit' and template.endswith('.svelte'):
        head = next((e for e in ctx.by_file.get(template, []) if e['tag'] == 'svelte:head'), None)
        if head is not None:
            return target('add', template, head['line'], head.get('col', 0), 'inside-start', '<svelte:head>', 'route', 'tags inside <svelte:head>')
        close = re.search(r'</script>', source)
        line, col = line_col(source, close.end()) if close else (1, 0)
        return target('add', template, line, col, 'after' if close else 'before', '</script>' if close else 'start-of-file', 'route',
                      '<svelte:head><title>...</title><meta name="description" ...></svelte:head>')
    if primary == 'astro' and template.endswith('.astro'):
        own = head_close_target(ctx, [template], 'add', scope_of, 'tags before </head>')
        if own:
            return own
        fm = re.match(r'\A\s*---\n.*?\n(---)', source, re.S)
        if fm:
            line, col = line_col(source, fm.start(1))
            return target('add', template, line, col, 'before', '---', 'route', 'pass title/description props to the layout component')
    if primary in CMS_FAMILIES:
        found = head_close_target(ctx, [template] + composition[::-1] + sorted(ctx.shells), 'cms', scope_of,
                                  'platform SEO fields first; otherwise tags before </head> (affects every page using it)')
        if found:
            return found
    block = HEAD_BLOCK.search(source)
    if block:  # Django/Jinja/Twig/Blade/Hugo child template that already overrides a head block
        line, col = line_col(source, block.end())
        return target('add', template, line, col, 'after', block.group(0)[:40], 'route', 'tags inside the head block override')
    ordered = [template] + [f for f in composition[::-1] if f != template] + [f for f in sorted(ctx.shells) if f not in composition]
    recipe = 'tags before </head>'
    if primary in SPA_FAMILIES:
        recipe = ('shared SPA shell: per-route values need prerendering (vite-ssg/Vike/react-snap) or a runtime head manager '
                  '(react-helmet-async / @unhead / Angular Title+Meta) in the route component; a static tag here applies to every route')
    found = head_close_target(ctx, ordered, 'add', scope_of, recipe)
    if found:
        return found
    if primary in SPA_FAMILIES and source:
        imports = after_imports(source)
        if imports:
            return target('add', template, imports[0], imports[1], 'after', 'last import', 'route', recipe)
    return {'kind': 'add', 'file': template or None, 'line': None, 'col': None, 'position': 'unknown', 'anchor': None,
            'scope': 'route', 'recipe': 'no <head> or head API found in the composition; locate the document shell manually',
            'verify': 'unresolved'}


def tag_end(source, line, col):
    """(line, col) just after the start tag that begins at (line, col); quotes and {} are skipped."""
    lines = source.splitlines(keepends=True)
    if not 0 < line <= len(lines):
        return None
    offset = sum(len(l) for l in lines[:line - 1]) + col
    depth, quote = 0, None
    for index in range(offset, min(len(source), offset + 4000)):
        char = source[index]
        if quote:
            if char == quote:
                quote = None
        elif char in '"\'`':
            quote = char
        elif char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
        elif char == '>' and depth <= 0:
            return line_col(source, index + 1)
    return None


def body_add_location(ctx, page, composition, page_files, elements, in_head):
    """Where to ADD body content (H1, intro/direct answer, FAQ) for this page: inside the page's own
    <main>/<article> or root element, never in <head>; front matter-driven pages get the content file."""
    template = page.get('template') or ''
    source = ctx.files.get(template, '')
    if template.endswith(CONTENT_SUFFIXES):
        match = re.match(r'\A(---|\+\+\+)\s*\n.*?\n\1[ \t]*\n?', source, re.S)
        line = source.count('\n', 0, match.end()) + 1 if match else 1
        return target('markdown', template, line, 0, 'before', 'body start', 'route', 'markdown body (first lines after front matter)')
    skip = {'html', 'head', 'title', 'meta', 'link', 'script', 'style', 'template', 'svelte:head', 'Head', 'Helmet', 'noscript', '#fragment'}
    own = [e for e in elements if e['file'] in page_files and e['id'] not in in_head and e['tag'] not in skip
           and e['kind'] != 'component' and not e.get('in_loop')]
    url = page.get('url') or ''
    routed = []
    for e in elements:  # pathname-switch / conditional routing: the element rendered for this URL
        if e['file'] in page_files and e.get('condition') and e.get('branch') == 'then' and \
                re.search(r'''["'`]''' + re.escape(url) + r'''["'`]''', e['condition']):
            if e['kind'] == 'component' and e.get('component_fn_ref'):
                roots = sorted((r for r in ctx.by_file.get(e['file'], []) if r.get('component_fn') == e['component_fn_ref']
                                and r['tag'] not in skip and r['kind'] != 'component' and not r.get('in_loop')),
                               key=lambda r: (r['line'], r.get('col', 0)))
                routed.extend(roots[:1])
            elif e['kind'] != 'component':
                routed.append(e)
    for pool in (routed, [e for e in own if e['tag'].lower() in {'main', 'article'}],
                 [e for e in own if e['tag'].lower() == 'body'], [e for e in own if not e.get('parent')]):
        if pool:
            chosen = pool[0]
            end = tag_end(ctx.files.get(chosen['file'], ''), chosen['line'], chosen.get('col', 0))
            if end:
                # an element selected by this URL's route condition renders only here, even in a shared file
                shared = pool is not routed and (chosen.get('shared') or len(ctx.file_pages.get(chosen['file'], [])) > 1)
                return target('add', chosen['file'], end[0], end[1], 'after', '<' + chosen['tag'] + '>',
                              'layout' if shared else 'route', 'insert body content (H1 first) as the first child')
    return {'kind': 'add', 'file': template or None, 'line': None, 'col': None, 'position': 'unknown', 'anchor': None,
            'scope': 'route', 'recipe': 'no page-owned body element found', 'verify': 'unresolved'}


def source_lens(snapshot, index, fingerprint, pages):
    ctx = Context(snapshot, index, fingerprint)
    ctx.page_templates = {page['template'] for page in pages if page.get('template')}
    ctx.shells = set(index.get('shells') or [])
    ctx.entry_components = defaultdict(set)
    route_entry = {}
    for page in pages:
        if page.get('template') and page.get('entry_component'):
            ctx.entry_components[page['template']].add(page['entry_component'])
            route_entry[(page['template'], page.get('route') or page['url'])] = page['entry_component']
    # Concrete instances (/blog/post-1) inherit the entry component of their route pattern (/blog/:slug).
    pages = [page if page.get('entry_component') or (page.get('template'), page.get('route')) not in route_entry
             else {**page, 'entry_component': route_entry[(page['template'], page['route'])]} for page in pages]
    file_pages = defaultdict(list)
    for page in pages:
        if page.get('template'):
            composition, _ = ctx.composition(page)
            for file in composition:
                file_pages[file].append(page.get('url'))
    ctx.file_pages = dict(file_pages)
    return {page['id']: extract_page(ctx, page) for page in pages}, ctx


class SourceLens:
    """Tool: per-page values and exact edit locations read from source (props, blocks, i18n, front matter, head APIs)."""

    name = 'source_lens'

    def extract(self, snapshot, index, fingerprint, pages):
        return source_lens(snapshot, index, fingerprint, pages)
