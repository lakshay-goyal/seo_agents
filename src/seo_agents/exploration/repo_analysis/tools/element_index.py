"""Unified, framework-agnostic element index over a repository snapshot.

One pass turns every template-bearing file into plain element records with stable
IDs and exact source positions, whatever the stack:

- JSX / TSX / JS (React, Next, Remix, Gatsby, Solid, Preact, Qwik): tree-sitter;
- Vue / Svelte / Astro single-file components: markup with `{{ }}` / `{...}` masked;
- HTML and every server template language (Liquid/Shopify, PHP/WordPress, Blade,
  Twig, Jinja/Django, Nunjucks, Go/Hugo, ERB, EJS, Handlebars, Razor): template
  directives are masked offset-for-offset, then the markup is parsed, so element
  positions stay exact and directive text survives as `expr` parts;
- Markdown / MDX: headings, links, images and front matter.

Alongside elements it records:
- `data`: every user-visible-looking string in JSON / YAML / front matter, with a
  JSON pointer, so a rendered string can be traced to the data row that supplies it;
- `i18n`: translation catalogs (JSON/YAML/PO, Shopify `locales/`, Rails, Laravel,
  WordPress) flattened to `key -> value`;
- `refs`: composition edges (imports, includes, sections, partials, layouts,
  extends, get_template_part ...) resolved to captured files where possible.

`page_composition(index, template)` follows those edges to list the files that
compose one page. The LLM never has to re-implement any of this.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import html
from html.parser import HTMLParser
import json
import posixpath
import re
from pathlib import PurePosixPath

MASK = ''
SEMANTIC = {'main', 'nav', 'header', 'footer', 'aside', 'article', 'section'}
COMPONENT_SECTIONS = {'header': 'header', 'siteheader': 'header', 'topbar': 'header', 'navbar': 'nav', 'nav': 'nav',
                      'navigation': 'nav', 'menu': 'nav', 'mainnav': 'nav', 'sidebar': 'aside', 'footer': 'footer',
                      'sitefooter': 'footer', 'main': 'main', 'article': 'article', 'layout': None}
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
TEXT_TAGS = {'title', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'a', 'button', 'p', 'li', 'label', 'figcaption', 'summary',
             'th', 'td', 'dt', 'dd', 'blockquote', 'span', 'strong', 'em', 'option', 'caption', 'legend', 'small', 'time'}
MAX_ELEMENTS_PER_FILE = 6000
MAX_TEXT = 1500

JSX_SUFFIXES = {'.jsx', '.tsx', '.js', '.mjs', '.cjs'}
TS_SUFFIXES = {'.ts', '.mts', '.cts'}
MARKUP_LANGUAGES = {
    '.html': 'html', '.htm': 'html', '.vue': 'vue', '.svelte': 'svelte', '.astro': 'astro',
    '.liquid': 'liquid', '.php': 'php', '.twig': 'twig', '.jinja': 'jinja', '.jinja2': 'jinja', '.j2': 'jinja',
    '.njk': 'nunjucks', '.erb': 'erb', '.ejs': 'ejs', '.hbs': 'handlebars', '.handlebars': 'handlebars',
    '.mustache': 'handlebars', '.cshtml': 'razor', '.razor': 'razor', '.tpl': 'template', '.gohtml': 'go-template',
    '.tmpl': 'go-template', '.xhtml': 'html', '.shtml': 'html',
}
MARKDOWN = {'.md', '.markdown', '.mdx'}

# Template directives masked before HTML parsing. Order matters: longest delimiters first.
DIRECTIVES = [
    re.compile(r'\{\{\{.*?\}\}\}', re.S),            # Handlebars raw
    re.compile(r'\{\{-?.*?-?\}\}', re.S),            # Liquid/Jinja/Twig/Vue/Hugo/Handlebars/Blade output
    re.compile(r'\{%-?.*?-?%\}', re.S),              # Liquid/Jinja/Twig/Nunjucks tags
    re.compile(r'\{#.*?#\}', re.S),                  # Jinja/Twig comments
    re.compile(r'\{!!.*?!!\}', re.S),                # Blade raw
    re.compile(r'<%.*?%>', re.S),                    # ERB/EJS/ASP
    re.compile(r'<\?(?:php|=)?.*?(?:\?>|\Z)', re.S),  # PHP
]
BLADE_DIRECTIVE = re.compile(r'(?<![\w@])@(?:if|elseif|else|endif|foreach|endforeach|for|endfor|section|endsection|yield|extends|include|'
                             r'component|endcomponent|slot|endslot|php|endphp|auth|endauth|guest|endguest|isset|endisset|unless|'
                             r'endunless|push|endpush|stack|lang|csrf|method|vite|livewire\w*|props|class|style)\b(?:\s*\((?:[^()]|\([^()]*\))*\))?')
RAZOR_EXPR = re.compile(r'(?<![\w@])@(?:\{[^{}]*\}|\([^()]*\)|[A-Za-z_][\w.]*(?:\([^()]*\))?)')
STRING_IN_EXPR = re.compile(r'''(["'])((?:\\.|(?!\1).){1,300})\1''')
I18N_CALL = re.compile(r'''(?:\$?\bt|\bi18n\.t|\btranslate|\b__|\b_e|\besc_html__|\besc_html_e|\besc_attr__|\besc_attr_e|\b_x|\b_n|\bgettext|\b_|\btrans|\b@lang|\bintl\.formatMessage|\bformatMessage|\b\$t)\s*\(\s*(["'`])([^"'`]{1,200})\1''')
LIQUID_T = re.compile(r'''(["'])([\w.\-]+)\1\s*\|\s*t\b''')
TEMPLATE_TRANS = re.compile(r'''\{%-?\s*(?:trans|translate|blocktrans|_)\s+(["'])(.{1,200}?)\1|(["'])([^"']{1,200})\3\s*\|\s*(?:trans|translate|i18n)\b''')
HEADING_COMPONENT_PROPS = (('level', None), ('order', None), ('variant', 'h'), ('as', 'h'), ('component', 'h'), ('size', 'h'), ('tag', 'h'), ('el', 'h'))


def element_id(file, line, col, tag):
    return 'e_' + hashlib.sha1(f'{file}:{line}:{col}:{tag}'.encode()).hexdigest()[:12]


def norm_text(value):
    return ' '.join(str(value).split())


def mask(text, regex_list):
    """Replace every directive span with MASK chars, keeping newlines and offsets."""
    spans = []
    chars = list(text)
    for regex in regex_list:
        for match in regex.finditer(text):
            start, end = match.span()
            if any(chars[i] == MASK for i in (start, end - 1) if i < len(chars)):
                continue
            spans.append((start, end))
            for i in range(start, end):
                if chars[i] != '\n':
                    chars[i] = MASK
    return ''.join(chars), sorted(spans)


def mask_braces(text, start=0, end=None, skip_blocks=('script', 'style')):
    """Mask balanced `{...}` expressions in markup (Svelte/Astro/Angular), skipping script/style."""
    end = len(text) if end is None else end
    chars = list(text)
    spans = []
    i = start
    lower = text.lower()
    while i < end:
        if text[i] == '<':
            for block in skip_blocks:
                if lower.startswith('<' + block, i):
                    close = lower.find('</' + block, i)
                    i = end if close < 0 else close
                    break
        if i >= end:
            break
        if text[i] == '{':
            depth, j, quote = 0, i, None
            while j < end:
                ch = text[j]
                if quote:
                    if ch == '\\':
                        j += 2
                        continue
                    if ch == quote:
                        quote = None
                elif ch in '"\'`':
                    quote = ch
                elif ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            spans.append((i, j + 1))
            for k in range(i, min(j + 1, len(chars))):
                if chars[k] != '\n':
                    chars[k] = MASK
            i = j + 1
            continue
        i += 1
    return ''.join(chars), spans


class LineMap:
    def __init__(self, text):
        self.starts = [0]
        for index, char in enumerate(text):
            if char == '\n':
                self.starts.append(index + 1)

    def offset(self, line, col):
        return self.starts[line - 1] + col if 0 < line <= len(self.starts) else 0

    def position(self, offset):
        from bisect import bisect_right
        line = bisect_right(self.starts, offset)
        return line, offset - self.starts[line - 1]


ATTR = re.compile(r'''([^\s"'>/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?''', re.S)


def expr_info(source):
    """Small, deterministic facts about a template/JS expression."""
    info = {'expr': source[:400]}
    keys = [m.group(2) for m in I18N_CALL.finditer(source)] + [m.group(2) for m in LIQUID_T.finditer(source)] + \
        [m.group(2) or m.group(4) for m in TEMPLATE_TRANS.finditer(source)]
    if keys:
        info['i18n_keys'] = keys[:4]
    strings = [m.group(2) for m in STRING_IN_EXPR.finditer(source) if re.search(r'[^\W\d_]', m.group(2))]
    if strings:
        info['strings'] = strings[:6]
    fragments = [norm_text(piece) for template in re.findall(r'`([^`]*)`', source)
                 for piece in re.split(r'\$\{[^}]*\}', template) if norm_text(piece)]
    if fragments:
        info['fragments'] = fragments[:6]  # literal pieces of a template string: `${title} interface screenshot`
    refs = re.findall(r'\b[A-Za-z_$][\w$]*(?:\??\.[A-Za-z_$][\w$]*)+|\b[A-Za-z_$][\w$]*(?=\s*[}|)])', source)
    if refs:
        info['refs'] = sorted({r.replace('?.', '.').rsplit('.', 1)[-1] for r in refs})[:8]  # last property names: role, company
    return info


class MarkupIndexer(HTMLParser):
    """Element records from masked markup; `original` recovers directive text."""

    def __init__(self, file, original, masked, language, sink):
        # convert_charrefs=False keeps every data chunk the same length as its source text,
        # so slices of `original` line up; entities arrive through handle_entityref/charref.
        super().__init__(convert_charrefs=False)
        self.file, self.original, self.masked, self.language = file, original, masked, language
        self.lines = LineMap(original)
        self.sink, self.stack = sink, []
        self.count = 0
        self.truncated = False

    def _attrs(self, start_offset, length):
        raw = self.original[start_offset:start_offset + length]
        masked_raw = self.masked[start_offset:start_offset + length]
        name_match = re.match(r'<\s*([^\s/>]+)', masked_raw)
        body_start = name_match.end() if name_match else 1
        attrs, dynamic = {}, False
        for match in ATTR.finditer(masked_raw, body_start):
            name = match.group(1)
            if name in {'/', '>'}:
                continue
            original_name = raw[match.start(1):match.end(1)]
            value_group = next((g for g in (2, 3, 4) if match.group(g) is not None), None)
            if MASK in name:
                dynamic = True
                continue
            if value_group is None:
                attrs[original_name] = {'flag': True}
                continue
            value = raw[match.start(value_group):match.end(value_group)]
            bound = original_name.startswith((':', 'v-bind:', 'v-', '@', '[', '(', 'bind:', 'on:', 'x-bind:', 'th:'))
            if MASK in match.group(value_group) or bound:
                attrs[original_name.lstrip(':')] = expr_info(value) | ({'binding': original_name} if bound else {})
            else:
                attrs[original_name] = {'literal': html.unescape(value)}
        return attrs, dynamic

    def handle_starttag(self, tag, _attrs):
        if self.count >= MAX_ELEMENTS_PER_FILE:
            self.truncated = True
            return
        line, col = self.getpos()
        offset = self.lines.offset(line, col)
        raw = self.get_starttag_text() or ''
        attrs, dynamic = self._attrs(offset, len(raw))
        original_tag = re.match(r'<\s*([^\s/>]+)', self.original[offset:offset + len(raw)] or '<x')
        name = original_tag.group(1) if original_tag else tag
        component = bool(re.match(r'[A-Z]', name)) or name.startswith('x-') or (
            '-' in name and self.language in {'vue', 'svelte', 'astro', 'php', 'html', 'liquid'})
        parent = self.stack[-1] if self.stack else None
        record = {'id': element_id(self.file, line, col, name), 'file': self.file, 'line': line, 'col': col,
                  'lang': self.language, 'tag': name if component else tag,
                  'kind': 'component' if component else 'element', 'attrs': attrs,
                  'text': [], 'parent': parent['id'] if parent else None,
                  'section': section_for(name if component else tag, parent)}
        if dynamic:
            record['dynamic_attrs'] = True
        level = heading_level(record)
        if level:
            record['heading_level'], record['heading_method'] = level
        self.sink.append(record)
        self.count += 1
        record['_buffer'] = []
        if tag not in VOID and not raw.rstrip().endswith('/>'):
            self.stack.append(record)
        else:
            self.finish(record, line)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'].lower() == tag.lower():
                line = self.getpos()[0]
                for node in reversed(self.stack[index:]):
                    self.finish(node, line)
                del self.stack[index:]
                return

    def finish(self, record, line):
        record['end_line'] = line
        for part in record['text']:
            part.pop('_raw', None)
        literal = norm_text(''.join(record.pop('_buffer', [])))
        if literal:
            record['text_literal'] = literal[:MAX_TEXT]

    def handle_data(self, data):
        if not self.stack or not data:
            return
        if any(n['tag'].lower() in {'script', 'style'} for n in self.stack):
            node = self.stack[-1]
            if node['tag'].lower() == 'script' and (node['attrs'].get('type') or {}).get('literal', '').lower() == 'application/ld+json':
                line, col = self.getpos()
                start = self.lines.offset(line, col)
                node.setdefault('raw_text', '')
                node['raw_text'] += self.original[start:start + len(data)]
            return
        line, col = self.getpos()
        start = self.lines.offset(line, col)
        original = self.original[start:start + len(data)]
        parts = []
        for match in re.finditer(MASK + '+|[^' + MASK + ']+', data):
            chunk = match.group(0)
            if chunk[0] == MASK:
                parts.append(expr_info(original[match.start():match.end()]))
            elif chunk.strip():
                parts.append({'literal': norm_text(chunk), '_raw': chunk})
                for node in self.stack:
                    node['_buffer'].append(chunk)
            else:
                parts.append({'_space': True})
                for node in self.stack:
                    node['_buffer'].append(' ')
        if parts:
            self._add_parts(parts)

    def _add_parts(self, parts):
        node = self.stack[-1]
        for part in parts:
            previous = node['text'][-1] if node['text'] else None
            if part.get('_space'):
                if previous is not None and '_raw' in previous:
                    previous['_raw'] += ' '
                continue
            if 'literal' in part and previous is not None and '_raw' in previous:
                previous['_raw'] += part.get('_raw', part['literal'])  # "Caf" + "é", "Tom " + "&" + " Jerry"
                previous['literal'] = norm_text(previous['_raw'])
            elif len(node['text']) < 40:
                node['text'].append(part)

    def _entity(self, text):
        if not self.stack or any(n['tag'].lower() in {'script', 'style'} for n in self.stack):
            if self.stack and self.stack[-1]['tag'].lower() == 'script' and 'raw_text' in self.stack[-1]:
                self.stack[-1]['raw_text'] += text
            return
        for node in self.stack:
            node['_buffer'].append(text)
        if text.strip():
            self._add_parts([{'literal': norm_text(text), '_raw': text}])

    def handle_entityref(self, name):
        self._entity(html.unescape('&' + name + ';'))

    def handle_charref(self, name):
        self._entity(html.unescape('&#' + name + ';'))

    def close(self):
        super().close()
        for node in reversed(self.stack):
            self.finish(node, len(self.lines.starts))
        self.stack.clear()


def section_for(tag, parent):
    lowered = tag.lower().replace('-', '').replace('_', '')
    if lowered in SEMANTIC:
        return lowered
    if lowered in COMPONENT_SECTIONS and COMPONENT_SECTIONS[lowered]:
        return COMPONENT_SECTIONS[lowered]
    return parent['section'] if parent else 'body'


def heading_level(record):
    tag = record['tag']
    if re.fullmatch(r'[hH][1-6]', tag):
        return int(tag[1]), 'tag'
    if record['kind'] != 'component' and tag.lower() not in {'div', 'span', 'p'}:
        return None
    for prop, prefix in HEADING_COMPONENT_PROPS:
        value = record['attrs'].get(prop, {})
        raw = value.get('literal') or (value.get('expr', '').strip('{} ') if 'expr' in value else '')
        if prefix:
            match = re.fullmatch(r'["\']?[hH]([1-6])["\']?', raw or '')
        else:
            match = re.fullmatch(r'["\']?([1-6])["\']?', raw or '') if re.search(r'(?i)head|title', tag) else None
        if match:
            return int(match.group(1)), 'component-prop:' + prop
    return None


# ---------------------------------------------------------------- JSX (tree-sitter)
_PARSERS = {}


def _parser(language):
    if language not in _PARSERS:
        from tree_sitter import Language, Parser
        import tree_sitter_javascript
        import tree_sitter_typescript
        factory = {'tsx': tree_sitter_typescript.language_tsx, 'ts': tree_sitter_typescript.language_typescript,
                   'js': tree_sitter_javascript.language}[language]
        _PARSERS[language] = Parser(Language(factory()))
    return _PARSERS[language]


def parse_script(file, source, sink, refs, *, language=None, line_offset=0):
    """JSX elements + imports from a JS/TS(X) source. Returns parse-error flag."""
    suffix = PurePosixPath(file).suffix.lower()
    language = language or ('tsx' if suffix == '.tsx' else 'ts' if suffix in TS_SUFFIXES else 'js')
    data = source.encode()
    root = _parser(language).parse(data).root_node

    def text(node):
        return data[node.start_byte:node.end_byte].decode('utf-8', 'replace')

    def pos(node):
        # tree-sitter columns are byte offsets; report character columns like every other lens
        line_start = node.start_byte - node.start_point.column
        return node.start_point.row + 1 + line_offset, len(data[line_start:node.start_byte].decode('utf-8', 'replace'))

    count = 0

    def visit(node, parent):
        nonlocal count
        kind = node.type
        if kind in {'import_statement', 'export_statement'}:
            target = node.child_by_field_name('source')
            if target is not None:
                names = [text(c) for c in node.named_children if c.type == 'import_clause']
                line, _ = pos(node)
                refs.append({'file': file, 'line': line, 'kind': 'import', 'spec': text(target)[1:-1],
                             'names': names[:1][0][:200] if names else ''})
        elif kind == 'call_expression':
            function = node.child_by_field_name('function')
            name = text(function) if function is not None else ''
            if name in {'import', 'require', 'lazy', 'React.lazy', 'defineAsyncComponent', 'dynamic'}:
                args = node.child_by_field_name('arguments')
                literal = next((c for c in (args.named_children if args else []) if c.type == 'string'), None)
                if literal is not None:
                    line, _ = pos(node)
                    refs.append({'file': file, 'line': line, 'kind': 'dynamic-import', 'spec': text(literal)[1:-1]})
        if kind in {'jsx_element', 'jsx_self_closing_element'} and count < MAX_ELEMENTS_PER_FILE:
            opening = node.child_by_field_name('open_tag') if kind == 'jsx_element' else node
            name_node = opening.child_by_field_name('name') if opening is not None else None
            name = text(name_node) if name_node is not None else '#fragment'
            line, col = pos(opening if opening is not None else node)
            component = bool(re.match(r'[A-Z]', name)) or '.' in name
            attrs, spread = {}, False
            for child in (opening.children if opening is not None else []):
                if child.type == 'jsx_attribute':
                    parts = child.named_children
                    attr_name = text(parts[0]) if parts else ''
                    value = parts[1] if len(parts) > 1 else None
                    if value is None:
                        attrs[attr_name] = {'flag': True}
                    elif value.type == 'string':
                        attrs[attr_name] = {'literal': text(value)[1:-1]}
                    elif value.type == 'jsx_expression':
                        inner = value.named_children[0] if value.named_children else None
                        if inner is not None and inner.type == 'string':
                            attrs[attr_name] = {'literal': text(inner)[1:-1], 'via': 'expression-string'}
                        elif inner is not None and inner.type == 'template_string' and not any(
                                c.type == 'template_substitution' for c in inner.named_children):
                            attrs[attr_name] = {'literal': text(inner)[1:-1], 'via': 'template-string'}
                        else:
                            attrs[attr_name] = expr_info(text(value))
                    else:
                        attrs[attr_name] = expr_info(text(value))
                elif child.type == 'jsx_expression':
                    spread = True
            record = {'id': element_id(file, line, col, name), 'file': file, 'line': line, 'col': col,
                      'end_line': node.end_point.row + 1 + line_offset, 'lang': 'jsx', 'tag': name,
                      'kind': 'component' if component else ('fragment' if name == '#fragment' else 'element'),
                      'attrs': attrs, 'text': [], 'parent': parent['id'] if parent else None,
                      'section': section_for(name.split('.')[-1], parent)}
            if spread:
                record['spread_props'] = True
            level = heading_level(record)
            if level:
                record['heading_level'], record['heading_method'] = level
            if kind == 'jsx_element':
                for child in node.named_children:
                    if child.type == 'jsx_text':
                        value = norm_text(text(child))
                        if value:
                            record['text'].append({'literal': value})
                    elif child.type == 'jsx_expression':
                        inner = child.named_children[0] if child.named_children else None
                        if inner is not None and inner.type in {'string', 'template_string'} and not any(
                                c.type == 'template_substitution' for c in inner.named_children):
                            record['text'].append({'literal': norm_text(text(inner)[1:-1]), 'via': 'expression-string'})
                        elif inner is not None and inner.type != 'comment':
                            record['text'].append(expr_info(text(child)))
                record['text'] = record['text'][:40]
            sink.append(record)
            count += 1
            for child in node.named_children:
                if child.type not in {'jsx_opening_element', 'jsx_closing_element'}:
                    visit(child, record)
            descendant = _descendant_literal(node, text)
            if descendant:
                record['text_literal'] = descendant[:MAX_TEXT]
            return
        for child in node.named_children:
            visit(child, parent)

    import sys
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(limit, 20000))
    try:
        visit(root, None)
    finally:
        sys.setrecursionlimit(limit)
    return root.has_error


def _descendant_literal(node, text):
    parts = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == 'jsx_text':
            parts.append(text(current))
        elif current.type == 'jsx_expression':
            inner = current.named_children[0] if current.named_children else None
            if inner is not None and inner.type == 'string':
                parts.append(text(inner)[1:-1])
            continue
        elif current.type in {'jsx_opening_element', 'jsx_closing_element', 'jsx_attribute'}:
            continue
        stack.extend(reversed(current.named_children))
    return norm_text(' '.join(parts))


# ---------------------------------------------------------------- markup languages
def parse_markup(file, source, language, sink):
    regexes = list(DIRECTIVES)
    if file.endswith('.blade.php'):
        regexes.append(BLADE_DIRECTIVE)
    if language == 'razor':
        regexes.append(RAZOR_EXPR)
    masked, _ = mask(source, regexes)
    if language in {'svelte', 'astro'}:
        body_start = 0
        if language == 'astro':
            fm = re.match(r'\s*---\n(.*?)\n---', source, re.S)
            if fm:
                body_start = fm.end()
                masked = ''.join(c if c == '\n' else ' ' for c in masked[:body_start]) + masked[body_start:]
        masked, _ = mask_braces(masked, body_start)
    if language == 'html' and re.search(r'\bng-|\*ngIf|\[\w+\]=', source):
        masked, _ = mask_braces(masked)
    parser = MarkupIndexer(file, source, masked, language, sink)
    try:
        parser.feed(masked)
        parser.close()
    except (AssertionError, ValueError, IndexError):
        return True, parser.truncated
    return False, parser.truncated


def script_blocks(source):
    """(offset_line, language, code) of <script> blocks and Astro front matter in SFCs."""
    blocks = []
    fm = re.match(r'\s*---\n(.*?)\n---', source, re.S)
    if fm:
        blocks.append((source[:fm.start(1)].count('\n'), 'ts', fm.group(1)))
    for match in re.finditer(r'<script\b([^>]*)>(.*?)</script>', source, re.S | re.I):
        attrs = match.group(1)
        if re.search(r'type\s*=\s*["\']application/(?:ld\+)?json', attrs):
            continue
        language = 'ts' if re.search(r'lang\s*=\s*["\']ts', attrs) else 'js'
        blocks.append((source[:match.start(2)].count('\n'), language, match.group(2)))
    return blocks


# ---------------------------------------------------------------- markdown
FRONT_MATTER = re.compile(r'\A(---|\+\+\+)\s*\n(.*?)\n\1\s*(?:\n|\Z)', re.S)


def parse_markdown(file, source, sink, data_sink):
    fm = FRONT_MATTER.match(source)
    if fm:
        for pointer, value, line in yaml_strings(fm.group(2), line_offset=1) if fm.group(1) == '---' else toml_strings(fm.group(2), 1):
            data_sink.append(data_record(file, pointer, value, line, 'front-matter'))
    in_code = False
    for number, line in enumerate(source.splitlines(), 1):
        if fm and number <= fm.group(0).count('\n'):
            continue
        if line.lstrip().startswith(('```', '~~~')):
            in_code = not in_code
            continue
        if in_code:
            continue
        heading = re.match(r'^(#{1,6})\s+(.+?)\s*#*\s*$', line)
        if heading:
            level = len(heading.group(1))
            sink.append({'id': element_id(file, number, 0, 'h' + str(level)), 'file': file, 'line': number, 'col': 0,
                         'end_line': number, 'lang': 'markdown', 'tag': 'h' + str(level), 'kind': 'element', 'attrs': {},
                         'text': [{'literal': norm_text(heading.group(2))}], 'text_literal': norm_text(heading.group(2)),
                         'parent': None, 'section': 'main', 'heading_level': level, 'heading_method': 'markdown'})
        for match in re.finditer(r'(!?)\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+"([^"]*)")?\s*\)', line):
            image = bool(match.group(1))
            tag = 'img' if image else 'a'
            attrs = {'src' if image else 'href': {'literal': match.group(3)}}
            if image:
                attrs['alt'] = {'literal': match.group(2)}
            if match.group(4):
                attrs['title'] = {'literal': match.group(4)}
            record = {'id': element_id(file, number, match.start(), tag), 'file': file, 'line': number, 'col': match.start(),
                      'end_line': number, 'lang': 'markdown', 'tag': tag, 'kind': 'element', 'attrs': attrs,
                      'text': [] if image else [{'literal': norm_text(match.group(2))}], 'parent': None, 'section': 'main'}
            if not image and match.group(2).strip():
                record['text_literal'] = norm_text(match.group(2))
            sink.append(record)
        stripped = line.strip()
        if stripped and not heading and not stripped.startswith(('<', '|', '-', '*', '>', 'import ', 'export ')) and len(stripped) > 1:
            plain = norm_text(re.sub(r'!?\[([^\]]*)\]\([^)]*\)', r'\1', re.sub(r'[*_`]', '', stripped)))
            if re.search(r'[^\W\d_]{2}', plain):
                sink.append({'id': element_id(file, number, 0, 'p'), 'file': file, 'line': number, 'col': 0, 'end_line': number,
                             'lang': 'markdown', 'tag': 'p', 'kind': 'element', 'attrs': {}, 'text': [{'literal': plain[:MAX_TEXT]}],
                             'text_literal': plain[:MAX_TEXT], 'parent': None, 'section': 'main'})
    return False


# ---------------------------------------------------------------- data / i18n
I18N_PATH = re.compile(r'(?:^|/)(?:locales?|i18n|lang|languages|translations|messages|intl|l10n)(?:/|$)|(?:^|/)[a-z]{2}(?:[-_][A-Za-z]{2})?\.(?:json|ya?ml|po)$')
LOCALE_FROM_PATH = re.compile(r'(?:^|[/._-])([a-z]{2}(?:[-_][A-Z]{2})?)(?:\.default)?(?:\.schema)?\.(?:json|ya?ml|po)$')


NON_COPY_POINTER = re.compile(r'/(?:type|order|id|handle|key|icon|class|className|component|variant|color|template|layout)(?:/\d+)?$|^/order/')


def data_record(file, pointer, value, line, kind, **extra):
    return {'id': 'd_' + hashlib.sha1(f'{file}#{pointer}'.encode()).hexdigest()[:12], 'file': file, 'pointer': pointer,
            'line': line, 'value': value[:MAX_TEXT], 'kind': kind, **extra}


def json_string_lines(text):
    """(pointer, string value, line) for every string scalar, from the token stream (exact lines)."""
    decoder = json.JSONDecoder()
    position, line_starts = 0, [0]
    for index, char in enumerate(text):
        if char == '\n':
            line_starts.append(index + 1)
    from bisect import bisect_right

    def line_of(offset):
        return bisect_right(line_starts, offset)

    def skip(i):
        while i < len(text) and text[i] in ' \t\r\n':
            i += 1
        return i

    def value(i, pointer):
        i = skip(i)
        char = text[i] if i < len(text) else ''
        if char == '{':
            i = skip(i + 1)
            if text[i] == '}':
                return i + 1
            while True:
                key, i = decoder.raw_decode(text, skip(i))
                i = skip(i)
                i = value(i + 1, pointer + '/' + str(key).replace('~', '~0').replace('/', '~1'))  # skip ':'
                i = skip(i)
                if text[i] == ',':
                    i += 1
                    continue
                return i + 1
        if char == '[':
            i = skip(i + 1)
            if text[i] == ']':
                return i + 1
            index = 0
            while True:
                i = skip(value(i, pointer + '/' + str(index)))
                index += 1
                if text[i] == ',':
                    i += 1
                    continue
                return i + 1
        parsed, end = decoder.raw_decode(text, i)
        if isinstance(parsed, str):
            found.append((pointer, parsed, line_of(i)))
        return end

    found = []
    try:
        value(position, '')
    except (ValueError, IndexError, RecursionError):
        return [(pointer, v, None) for pointer, v in json_strings(json.loads(text))]
    return found


def json_strings(value, pointer=''):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from json_strings(child, pointer + '/' + str(key).replace('~', '~0').replace('/', '~1'))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from json_strings(child, pointer + '/' + str(index))
    elif isinstance(value, str):
        yield pointer, value


def yaml_strings(text, line_offset=0):
    """Minimal YAML reader for scalar strings: maps, lists, quoted/plain/block scalars.

    Not a full YAML parser (anchors/flow collections are skipped); good enough to
    locate copy in front matter, i18n catalogs and data files with exact lines.
    """
    stack = [(-1, '', False)]  # (indent, pointer, opened by a key with an empty value)
    list_counters = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        raw = lines[index]
        number = index + 1 + line_offset
        index += 1
        if not raw.strip() or raw.lstrip().startswith('#'):
            continue
        indent = len(raw) - len(raw.lstrip(' '))
        body = raw.strip()
        is_item = body.startswith('- ') or body == '-'
        # `key:` followed by `- item` at the SAME indent is still that key's list (common YAML style).
        while stack and (indent < stack[-1][0] or (indent == stack[-1][0] and not (is_item and stack[-1][2]))):
            stack.pop()
        parent = stack[-1][1] if stack else ''
        if body.startswith('- ') or body == '-':
            key_pointer = parent
            position = list_counters.get((key_pointer, indent), -1) + 1
            list_counters[(key_pointer, indent)] = position
            pointer = key_pointer + '/' + str(position)
            rest = body[2:].strip() if body.startswith('- ') else ''
            stack.append((indent + 1, pointer, False))
            if not rest:
                continue
            item = re.match(r'''^(["']?)([^"':#][^:#]*?)\1\s*:\s*(.*)$''', rest)
            if item:
                key, value = item.group(2).strip(), item.group(3)
                child = pointer + '/' + key.replace('~', '~0').replace('/', '~1')
                scalar = _yaml_scalar(value)
                if scalar is not None:
                    yield child, scalar, number
                else:
                    stack.append((indent + 2, child, True))
                continue
            scalar = _yaml_scalar(rest)
            if scalar is not None:
                yield pointer, scalar, number
            continue
        match = re.match(r'''^(["']?)([^"'#][^#]*?)\1\s*:(?:\s+(.*))?$''', body)
        if not match:
            continue
        key = match.group(2).strip()
        value = (match.group(3) or '').strip()
        pointer = parent + '/' + key.replace('~', '~0').replace('/', '~1')
        if value in {'|', '>', '|-', '>-', '|+', '>+'}:
            block = []
            while index < len(lines) and (not lines[index].strip() or len(lines[index]) - len(lines[index].lstrip(' ')) > indent):
                block.append(lines[index].strip())
                index += 1
            joined = ('\n' if value.startswith('|') else ' ').join(block).strip()
            if joined:
                yield pointer, joined, number
            continue
        scalar = _yaml_scalar(value) if value else None
        if scalar is not None:
            yield pointer, scalar, number
        else:
            stack.append((indent, pointer, not value))


def _yaml_scalar(value):
    value = value.strip()
    if not value or value.startswith(('&', '*', '{', '[', '!')):
        return None
    if value[0] in '"\'' and value[-1:] == value[0] and len(value) >= 2:
        return value[1:-1]
    value = re.split(r'\s+#', value, 1)[0].strip()
    if value.lower() in {'true', 'false', 'null', '~', 'yes', 'no'} or re.fullmatch(r'-?\d+(?:\.\d+)?', value):
        return None
    return value


def toml_strings(text, line_offset=0):
    table = ''
    for number, line in enumerate(text.splitlines(), 1 + line_offset):
        header = re.match(r'^\s*\[\[?([^\]]+)\]\]?\s*$', line)
        if header:
            table = '/' + '/'.join(part.strip().strip('"') for part in header.group(1).split('.'))
            continue
        match = re.match(r'''^\s*([\w\-."]+)\s*=\s*(["'])(.*)\2\s*(?:#.*)?$''', line)
        if match:
            yield table + '/' + match.group(1).strip('"'), match.group(3), number


def po_strings(text):
    msgid, line_of = None, 0
    for number, line in enumerate(text.splitlines(), 1):
        match = re.match(r'^msgid\s+"(.*)"\s*$', line)
        if match:
            msgid, line_of = match.group(1), number
            continue
        match = re.match(r'^msgstr\s+"(.*)"\s*$', line)
        if match and msgid:
            yield msgid, match.group(1) or msgid, line_of
            msgid = None


def first_line(text, value):
    if not value:
        return 1
    needle = json.dumps(value, ensure_ascii=False)[1:-1][:120]
    position = text.find(needle)
    if position < 0:
        position = text.find(value[:120])
    return text.count('\n', 0, position) + 1 if position >= 0 else 1


ASSET_VALUE = re.compile(r'(?:https?://\S+|\.{0,2}/?[\w@%+./\-]+)\.(?:png|jpe?g|gif|svg|webp|avif|ico|pdf|mp4|webm)(?:\?\S*)?', re.I)


def visible_string(value):
    return isinstance(value, str) and 1 < len(value) <= 5000 and bool(re.search(r'[^\W\d_]{2}', value)) \
        and not re.fullmatch(r'[\w.\-/@:#?=&%+]+\.(?:png|jpe?g|svg|webp|gif|css|js|json|woff2?)', value) \
        and not re.fullmatch(r'#[0-9a-fA-F]{3,8}|rgba?\(.*\)|\d+(?:px|rem|em|%)', value)


def index_data_file(file, source, data_sink, i18n):
    suffix = PurePosixPath(file).suffix.lower()
    is_i18n = bool(I18N_PATH.search(file))
    locale = LOCALE_FROM_PATH.search(file)
    locale = locale.group(1) if locale else None
    entries = []
    if suffix == '.json':
        try:
            json.loads(source)
        except ValueError:
            return 'invalid-json'
        entries = list(json_string_lines(source))
    elif suffix in {'.yml', '.yaml'}:
        entries = list(yaml_strings(source))
    elif suffix == '.toml':
        entries = list(toml_strings(source))
    elif suffix == '.po':
        for msgid, msgstr, line in po_strings(source):
            i18n.setdefault(msgid, []).append({'file': file, 'line': line, 'value': msgstr, 'locale': locale, 'pointer': None})
        return 'indexed'
    else:
        return 'unsupported'
    for pointer, value, line in entries:
        if not isinstance(value, str):
            continue
        if ASSET_VALUE.fullmatch(value.strip()) and not NON_COPY_POINTER.search(pointer):
            # image/file paths in data (e.g. "image": "/img/a.png") let attribution find the data row behind a src
            data_sink.append(data_record(file, pointer, value.strip(), line or first_line(source, value), 'asset-ref'))
            continue
        if not visible_string(value) or NON_COPY_POINTER.search(pointer):
            continue
        line = line or first_line(source, value)
        record = data_record(file, pointer, value, line, 'i18n' if is_i18n else 'data')
        if is_i18n:
            tokens = [t.replace('~1', '/').replace('~0', '~') for t in pointer.split('/')[1:]]
            # Rails/Shopify catalogs nest under the locale code; strip it from the key.
            if tokens and re.fullmatch(r'[a-z]{2}(?:[-_][A-Za-z]{2})?', tokens[0]) and suffix in {'.yml', '.yaml'}:
                tokens = tokens[1:]
            key = '.'.join(tokens)
            record.update(i18n_key=key, locale=locale)
            i18n.setdefault(key, []).append({'file': file, 'line': line, 'value': value, 'locale': locale, 'pointer': pointer})
        data_sink.append(record)
    return 'indexed'


# ---------------------------------------------------------------- composition refs
TEMPLATE_REFS = [
    # (languages or None for all markup, regex with group 'spec', kind)
    ({'liquid'}, re.compile(r'''\{%-?\s*(?:render|include)\s+['"](?P<spec>[^'"]+)['"]'''), 'liquid-snippet'),
    ({'liquid'}, re.compile(r'''\{%-?\s*section\s+['"](?P<spec>[^'"]+)['"]'''), 'liquid-section'),
    ({'liquid'}, re.compile(r'''\{%-?\s*sections\s+['"](?P<spec>[^'"]+)['"]'''), 'liquid-section-group'),
    ({'liquid'}, re.compile(r'''\{%-?\s*layout\s+['"](?P<spec>[^'"]+)['"]'''), 'liquid-layout'),
    ({'liquid', 'html', 'markdown'}, re.compile(r'''\{%-?\s*include(?:_relative)?\s+(?P<spec>[\w./\-]+\.\w+)'''), 'jekyll-include'),
    ({'jinja', 'twig', 'nunjucks', 'html', 'liquid'}, re.compile(r'''\{%-?\s*(?:extends|include|import|from|embed)\s+['"](?P<spec>[^'"]+)['"]'''), 'template-include'),
    ({'php'}, re.compile(r'''\bget_(?P<fn>header|footer|sidebar|searchform)\s*\(\s*(?:['"](?P<spec>[^'"]*)['"])?'''), 'wp-part'),
    ({'php'}, re.compile(r'''\bget_template_part\s*\(\s*['"](?P<spec>[^'"]+)['"](?:\s*,\s*['"](?P<name>[^'"]+)['"])?'''), 'wp-template-part'),
    ({'php'}, re.compile(r'''\b(?:include|require)(?:_once)?\s*\(?\s*(?:__DIR__\s*\.\s*|get_template_directory\(\)\s*\.\s*|get_stylesheet_directory\(\)\s*\.\s*)?['"](?P<spec>[^'"]+\.php)['"]'''), 'php-include'),
    ({'php'}, re.compile(r'''\blocate_template\s*\(\s*(?:array\(\s*)?['"](?P<spec>[^'"]+)['"]'''), 'php-include'),
    ({'php'}, re.compile(r'''<!--\s*wp:template-part\s*\{[^}]*"slug"\s*:\s*"(?P<spec>[^"]+)"'''), 'wp-block-part'),
    ({'html'}, re.compile(r'''<!--\s*wp:template-part\s*\{[^}]*"slug"\s*:\s*"(?P<spec>[^"]+)"'''), 'wp-block-part'),
    ({'html'}, re.compile(r'''<!--\s*wp:pattern\s*\{[^}]*"slug"\s*:\s*"(?P<spec>[^"]+)"'''), 'wp-pattern'),
    ({'php'}, re.compile(r'''@(?:extends|include|includeIf|includeWhen|component|each)\s*\(\s*['"](?P<spec>[^'"]+)['"]'''), 'blade-include'),
    ({'php'}, re.compile(r'''<x-(?P<spec>[\w.\-:]+)'''), 'blade-component'),
    ({'erb'}, re.compile(r'''\brender\s*\(?\s*(?:partial:\s*)?['"](?P<spec>[^'"]+)['"]'''), 'rails-partial'),
    ({'ejs'}, re.compile(r'''\binclude\s*\(\s*['"](?P<spec>[^'"]+)['"]'''), 'ejs-include'),
    ({'handlebars'}, re.compile(r'''\{\{>\s*['"]?(?P<spec>[\w./\-]+)'''), 'hbs-partial'),
    ({'handlebars'}, re.compile(r'''\{\{!<\s*(?P<spec>[\w./\-]+)'''), 'hbs-layout'),
    ({'html', 'go-template'}, re.compile(r'''\{\{-?\s*partial(?:Cached)?\s+"(?P<spec>[^"]+)"'''), 'hugo-partial'),
    ({'html', 'go-template'}, re.compile(r'''\{\{-?\s*template\s+"(?P<spec>[^"]+)"'''), 'go-template'),
    ({'razor'}, re.compile(r'''(?:@await\s+Html\.PartialAsync|@Html\.Partial|<partial\s+name=)\s*\(?\s*"(?P<spec>[^"]+)"'''), 'razor-partial'),
    ({'razor'}, re.compile(r'''\bLayout\s*=\s*"(?P<spec>[^"]+)"'''), 'razor-layout'),
]
FRONT_MATTER_LAYOUT = re.compile(r'(?m)^layout:\s*["\']?([\w./\-]+)')


def template_refs(file, source, language, refs):
    for languages, regex, kind in TEMPLATE_REFS:
        if languages and language not in languages:
            continue
        for match in regex.finditer(source):
            spec = match.groupdict().get('spec') or ''
            extra = {key: value for key, value in match.groupdict().items() if key not in {'spec'} and value}
            refs.append({'file': file, 'line': source.count('\n', 0, match.start()) + 1, 'kind': kind, 'spec': spec, **extra})
    fm = FRONT_MATTER.match(source)
    if fm:
        layout = FRONT_MATTER_LAYOUT.search(fm.group(2))
        if layout:
            refs.append({'file': file, 'line': fm.group(2)[:layout.start()].count('\n') + 2, 'kind': 'front-matter-layout', 'spec': layout.group(1)})


def load_aliases(files):
    """Module aliases from tsconfig/jsconfig paths, Vite/webpack aliases and framework defaults."""
    aliases = []
    for path in sorted(files):
        name = PurePosixPath(path).name
        if name in {'tsconfig.json', 'jsconfig.json'} or re.fullmatch(r'tsconfig\.[\w.]+\.json', name):
            text = re.sub(r'(?<![:"\w])//[^\n]*|/\*.*?\*/', '', files[path], flags=re.S)
            text = re.sub(r',(\s*[}\]])', r'\1', text)
            try:
                options = json.loads(text).get('compilerOptions') or {}
            except (ValueError, AttributeError):
                continue
            base = posixpath.normpath(posixpath.join(posixpath.dirname(path), options.get('baseUrl') or '.'))
            for key, targets in (options.get('paths') or {}).items():
                for target in targets[:2] if isinstance(targets, list) else []:
                    aliases.append((key.rstrip('*'), posixpath.normpath(posixpath.join(base, target.rstrip('*'))).rstrip('/') + '/'
                                    if target.endswith('*') or target.endswith('/') else posixpath.normpath(posixpath.join(base, target)), path))
            if options.get('baseUrl'):
                aliases.append(('', base.rstrip('/') + '/' if base != '.' else '', path))
        elif re.fullmatch(r'(?:vite|vitest|webpack|nuxt|astro|svelte|quasar)\.config\.[cm]?[jt]s|vue\.config\.js', name):
            for match in re.finditer(r'''['"]?(@[\w/\-]*|~[\w/\-]*|\$[\w/\-]+)['"]?\s*:\s*(?:path\.)?(?:resolve|join|fileURLToPath)?\(?[^'"\n]*['"]\.?/?([\w./\-]+)['"]''', files[path]):
                root = posixpath.dirname(path)
                aliases.append((match.group(1).rstrip('/') + '/', posixpath.normpath(posixpath.join(root, match.group(2))) + '/', path))
    defaults = [('@/', 'src/'), ('~/', 'src/'), ('@/', ''), ('~/', ''), ('$lib/', 'src/lib/'), ('@components/', 'src/components/'),
                ('~~/', ''), ('@@/', ''), ('#/', 'src/'), ('src/', 'src/')]
    aliases.extend((prefix, target, 'framework-default') for prefix, target in defaults)
    return aliases


SCRIPT_EXTS = ('.tsx', '.ts', '.jsx', '.js', '.mjs', '.vue', '.svelte', '.astro', '.mdx', '.json')


def resolve_module(file, spec, files, aliases):
    if not spec:
        return None
    candidates = []
    if spec.startswith('.'):
        candidates.append(posixpath.normpath(posixpath.join(posixpath.dirname(file), spec)))
    elif spec.startswith('/'):
        candidates.append(spec.lstrip('/'))
    else:
        for prefix, target, _ in aliases:
            if prefix and spec.startswith(prefix):
                candidates.append(posixpath.normpath(target + spec[len(prefix):]))
            elif prefix and spec == prefix.rstrip('/'):
                candidates.append(target.rstrip('/'))
            elif not prefix and target is not None:
                candidates.append(posixpath.normpath(posixpath.join(target, spec)))
    for base in candidates:
        for candidate in [base] + [base + ext for ext in SCRIPT_EXTS] + [base + '/index' + ext for ext in SCRIPT_EXTS]:
            if candidate in files:
                return candidate
    return None


def resolve_template(file, ref, files, by_name):
    """Resolve a template-language reference to a captured file (best effort, unique matches only)."""
    spec, kind = ref['spec'], ref['kind']
    root = _theme_root(file)
    candidates = []
    if kind == 'liquid-snippet':
        candidates = [root + 'snippets/' + spec + '.liquid', root + '_includes/' + spec, root + '_includes/' + spec + '.liquid']
    elif kind == 'liquid-section':
        candidates = [root + 'sections/' + spec + '.liquid']
    elif kind == 'liquid-section-group':
        candidates = [root + 'sections/' + spec + '.json']
    elif kind == 'liquid-layout':
        candidates = [root + 'layout/' + spec + '.liquid']
    elif kind == 'jekyll-include':
        candidates = [root + '_includes/' + spec, '_includes/' + spec]
    elif kind == 'front-matter-layout':
        candidates = [root + '_layouts/' + spec + ext for ext in ('.html', '.liquid', '.njk', '.md')] + \
                     ['_layouts/' + spec + '.html', '_includes/layouts/' + spec + '.njk', '_includes/' + spec + '.njk',
                      '_includes/' + spec, 'src/_includes/' + spec, 'src/_includes/layouts/' + spec + '.njk', 'src/layouts/' + spec + '.astro']
    elif kind == 'wp-part':
        part = ref.get('fn', 'header')
        candidates = [root + part + ('-' + spec if spec else '') + '.php', root + part + '.php',
                      root + 'parts/' + part + '.html', root + 'block-template-parts/' + part + '.html']
    elif kind == 'wp-template-part':
        name = ref.get('name')
        candidates = ([root + spec + '-' + name + '.php'] if name else []) + [root + spec + '.php']
    elif kind == 'wp-block-part':
        candidates = [root + 'parts/' + spec + '.html', root + 'block-template-parts/' + spec + '.html']
    elif kind == 'wp-pattern':
        slug = spec.split('/')[-1]
        candidates = [root + 'patterns/' + slug + '.php']
    elif kind == 'php-include':
        candidates = [posixpath.normpath(posixpath.join(posixpath.dirname(file), spec)), root + spec.lstrip('/')]
    elif kind in {'blade-include'}:
        dotted = spec.replace('.', '/')
        candidates = ['resources/views/' + dotted + '.blade.php', 'resources/views/' + dotted + '/index.blade.php']
    elif kind == 'blade-component':
        dotted = spec.replace('.', '/').replace('::', '/')
        candidates = ['resources/views/components/' + dotted + '.blade.php', 'resources/views/components/' + dotted + '/index.blade.php']
    elif kind == 'rails-partial':
        directory, _, name = spec.rpartition('/')
        base = 'app/views/' + (directory + '/' if directory else posixpath.relpath(posixpath.dirname(file), 'app/views') + '/')
        candidates = [base + '_' + name + ext for ext in ('.html.erb', '.erb', '.html.haml', '.html.slim')]
    elif kind == 'ejs-include':
        candidates = [posixpath.normpath(posixpath.join(posixpath.dirname(file), spec)) + ext for ext in ('', '.ejs')] + ['views/' + spec + '.ejs']
    elif kind in {'hbs-partial'}:
        candidates = [root + 'partials/' + spec + '.hbs', 'views/partials/' + spec + '.hbs', root + spec + '.hbs']
    elif kind == 'hbs-layout':
        candidates = [root + spec + '.hbs', root + 'layouts/' + spec + '.hbs']
    elif kind == 'hugo-partial':
        candidates = [root + 'layouts/partials/' + spec, root + 'layouts/partials/' + spec + '.html', 'layouts/partials/' + spec,
                      'layouts/_partials/' + spec]
    elif kind == 'razor-partial' or kind == 'razor-layout':
        candidates = ['Views/Shared/' + spec + '.cshtml', 'Pages/Shared/' + spec + '.cshtml', spec.lstrip('~/')]
    elif kind == 'template-include':
        candidates = [posixpath.normpath(posixpath.join(posixpath.dirname(file), spec))]
    for candidate in candidates:
        if candidate in files:
            return candidate
    # Template directories differ per project (Django app/templates/, Twig templates/, Nunjucks _includes/):
    # accept a unique suffix match.
    tail = spec.lstrip('./')
    if kind in {'template-include', 'jekyll-include', 'php-include', 'ejs-include', 'hbs-partial', 'hugo-partial', 'front-matter-layout'} and tail:
        matches = [p for p in by_name.get(PurePosixPath(tail).name, []) if p.endswith(tail) or p.endswith(tail + '.html')
                   or p.endswith(tail + '.njk') or p.endswith(tail + '.hbs')]
        if len(matches) == 1:
            return matches[0]
    return None


def _theme_root(file):
    """Directory that holds a theme's conventional folders (Shopify/WordPress/Hugo/Jekyll/Ghost)."""
    parts = PurePosixPath(file).parts
    markers = {'layout', 'sections', 'snippets', 'templates', 'layouts', 'parts', 'patterns', '_includes', '_layouts',
               'partials', 'blocks', 'config', 'locales', 'assets', 'template-parts', 'inc'}
    for index, part in enumerate(parts[:-1]):
        if part in markers:
            return '/'.join(parts[:index]) + '/' if index else ''
    return posixpath.dirname(file) + '/' if posixpath.dirname(file) else ''


# ---------------------------------------------------------------- build
def language_for(file):
    path = PurePosixPath(file.lower())
    if file.lower().endswith('.blade.php'):
        return 'php'
    if path.suffix in JSX_SUFFIXES or path.suffix in TS_SUFFIXES:
        return 'script'
    if path.suffix in MARKDOWN:
        return 'markdown'
    if path.suffix == '.html' and re.search(r'(?:^|/)layouts?/', file):
        return 'html'
    if file.endswith(('.html.erb', '.erb')):
        return 'erb'
    if file.endswith('.html.twig'):
        return 'twig'
    return MARKUP_LANGUAGES.get(path.suffix)


def build_element_index(snapshot):
    """Index every captured file. Pure function of the snapshot; deterministic output order."""
    files = snapshot['files']
    elements, data, refs = [], [], []
    i18n = {}
    file_status = {}
    aliases = load_aliases(files)
    for file in sorted(files):
        source = files[file]
        language = language_for(file)
        suffix = PurePosixPath(file).suffix.lower()
        before = len(elements)
        status, error, truncated = 'skipped', False, False
        try:
            if language == 'script':
                if 'node_modules' in file or file.endswith(('.d.ts', '.min.js')) or len(source) > 400_000:
                    status = 'skipped-generated'
                else:
                    error = parse_script(file, source, elements, refs)
                    status = 'indexed'
            elif language == 'markdown':
                parse_markdown(file, source, elements, data)
                if suffix == '.mdx':
                    for match in re.finditer(r'''^import\s+[^;\n]+?from\s+['"]([^'"]+)['"]''', source, re.M):
                        refs.append({'file': file, 'line': source.count('\n', 0, match.start()) + 1, 'kind': 'import', 'spec': match.group(1)})
                template_refs(file, source, 'markdown', refs)
                status = 'indexed'
            elif language:
                error, truncated = parse_markup(file, source, language, elements)
                template_refs(file, source, language, refs)
                if language in {'vue', 'svelte', 'astro', 'html'}:
                    for line_offset, script_language, code in script_blocks(source):
                        sink = []
                        parse_script(file, code, sink, refs, language='tsx' if script_language == 'ts' and language == 'astro' else script_language,
                                     line_offset=line_offset)
                        elements.extend(e for e in sink if e['kind'] != 'fragment')
                status = 'indexed'
            if suffix in {'.json', '.yml', '.yaml', '.toml', '.po'} and 'package' not in PurePosixPath(file).name \
                    and not re.search(r'(?:^|/)(?:tsconfig|jsconfig|composer|\.eslintrc|\.prettierrc|renovate|turbo|vercel|netlify|firebase)[\w.]*\.json$', file):
                status = index_data_file(file, source, data, i18n)
        except RecursionError:
            status, error = 'partial', True
        entry = {'language': language or suffix.lstrip('.') or 'other', 'status': status, 'elements': len(elements) - before}
        if error:
            entry['syntax_errors'] = True
        if truncated:
            entry['truncated'] = True
        file_status[file] = entry
    by_name = defaultdict(list)
    for path in files:
        by_name[PurePosixPath(path).name].append(path)
    for ref in refs:
        if ref['kind'] in {'import', 'dynamic-import'}:
            ref['resolved'] = resolve_module(ref['file'], ref['spec'], files, aliases)
            ref['external'] = ref['resolved'] is None and not ref['spec'].startswith(('.', '/'))
        else:
            ref['resolved'] = resolve_template(ref['file'], ref, files, by_name)
    # Components point at the file that defines them (import name -> resolved file).
    imported = defaultdict(dict)
    for ref in refs:
        if ref['kind'] == 'import' and ref.get('resolved'):
            for name in re.findall(r'[A-Za-z_$][\w$]*', ref.get('names', '')):
                imported[ref['file']][name] = ref['resolved']
    for element in elements:
        if element['kind'] == 'component':
            head = element['tag'].split('.')[0]
            target = imported[element['file']].get(head)
            if target is None and element['tag'].startswith('x-'):
                target = resolve_template(element['file'], {'spec': element['tag'][2:], 'kind': 'blade-component'}, files, by_name)
            if target is None and element['lang'] in {'vue', 'html'}:
                pascal = ''.join(part.capitalize() for part in re.split(r'[-_]', element['tag']))
                target = next((p for p in by_name.get(pascal + '.vue', []) + by_name.get(element['tag'] + '.vue', [])), None)
            if target:
                element['component_ref'] = target
    shopify_sections(files, refs)
    return {
        'schema': 'element-index/1.0',
        'elements': elements,
        'data': data,
        'i18n': i18n,
        'refs': refs,
        'files': file_status,
        'aliases': [{'prefix': p, 'target': t, 'source': s} for p, t, s in aliases if s != 'framework-default'],
        'limitations': [
            'Template directives are masked, not evaluated: loops, conditionals and filters keep their source text as expr parts.',
            'Expressions inside Svelte/Astro braces are opaque; elements created inside them are not indexed.',
            'YAML support covers scalar maps/lists/block scalars only (no anchors or flow collections).',
            'Composition edges are literal references; runtime-selected templates/components remain unresolved.',
        ],
    }


def shopify_sections(files, refs):
    """Shopify JSON templates/section groups reference sections by `type`."""
    for file in sorted(files):
        if not re.search(r'(?:^|/)(?:templates(?:/customers)?|sections)/[^/]+\.json$', file):
            continue
        try:
            document = json.loads(re.sub(r'^\s*/\*.*?\*/', '', files[file], flags=re.S))
        except ValueError:
            continue
        sections = document.get('sections') if isinstance(document, dict) else None
        if not isinstance(sections, dict):
            continue
        order = document.get('order') if isinstance(document.get('order'), list) else list(sections)
        root = _theme_root(file)
        for key in order:
            section = sections.get(key)
            if not isinstance(section, dict) or not isinstance(section.get('type'), str):
                continue
            target = root + 'sections/' + section['type'] + '.liquid'
            refs.append({'file': file, 'line': first_line(files[file], key), 'kind': 'shopify-json-section', 'spec': section['type'],
                         'resolved': target if target in files else None, 'section_key': key})


def page_composition(index, template, *, layouts=(), exclude=(), max_files=80, max_depth=8):
    """Files composing one page: layouts first, then the template and what it pulls in (BFS order).

    `exclude` stops the walk at other routes' templates, so a router shell that imports
    every page (SPA main.jsx / App.vue) does not leak sibling pages into this one.
    """
    exclude = set(exclude) - {template}
    edges = defaultdict(list)
    for ref in index['refs']:
        if ref.get('resolved') and ref['resolved'] != ref['file']:
            edges[ref['file']].append(ref['resolved'])
    for element in index['elements']:
        if element.get('component_ref'):
            edges[element['file']].append(element['component_ref'])
    order, seen = [], set()
    queue = [(file, 0) for file in list(layouts) + [template]]
    while queue and len(order) < max_files:
        file, depth = queue.pop(0)
        if file in seen or file in exclude:
            continue
        seen.add(file)
        order.append(file)
        if depth < max_depth:
            queue.extend((child, depth + 1) for child in dict.fromkeys(edges.get(file, [])) if child not in seen)
    return order


def layout_chain(template, files, fingerprint=None):
    """Framework layout shells that wrap a page template (outermost first)."""
    chain = []
    parts = PurePosixPath(template).parts
    family = (fingerprint or {}).get('primary')
    # Next app router / SvelteKit / Nuxt nested layouts by directory ancestry.
    for index in range(len(parts)):
        directory = '/'.join(parts[:index])
        for name in ('layout.tsx', 'layout.jsx', 'layout.ts', 'layout.js', '+layout.svelte', 'template.tsx'):
            candidate = (directory + '/' if directory else '') + name
            if candidate in files and candidate != template:
                chain.append(candidate)
    shells = {
        'next': ['pages/_app.tsx', 'pages/_app.jsx', 'pages/_app.js', 'src/pages/_app.tsx', 'src/pages/_app.jsx', 'src/pages/_app.js',
                 'pages/_document.tsx', 'pages/_document.js', 'src/pages/_document.tsx'],
        'nuxt': ['app.vue', 'layouts/default.vue', 'src/app.vue', 'src/layouts/default.vue'],
        'sveltekit': ['src/app.html'],
        'remix': ['app/root.tsx', 'app/root.jsx'],
        'gatsby': ['src/components/layout.js', 'src/components/layout.tsx', 'src/components/Layout.tsx'],
        'react-spa': ['index.html', 'src/main.tsx', 'src/main.jsx', 'src/index.tsx', 'src/index.jsx', 'src/App.tsx', 'src/App.jsx', 'public/index.html'],
        'vue-spa': ['index.html', 'src/main.ts', 'src/main.js', 'src/App.vue', 'public/index.html'],
        'svelte-spa': ['index.html', 'src/main.ts', 'src/main.js', 'src/App.svelte', 'public/index.html'],
        'preact-spa': ['index.html', 'src/main.tsx', 'src/index.tsx', 'src/app.tsx'],
        'angular': ['src/index.html', 'src/app/app.component.html'],
        'shopify-theme': [_theme_root(template) + 'layout/theme.liquid'],
        'hugo': ['layouts/_default/baseof.html'],
        'rails': ['app/views/layouts/application.html.erb'],
        'django': [],
    }
    for candidate in shells.get(family, []):
        if candidate in files and candidate != template and candidate not in chain:
            chain.append(candidate)
    if family == 'shopify-theme':
        root = _theme_root(template)
        for group in ('header-group', 'footer-group'):
            candidate = root + 'sections/' + group + '.json'
            if candidate in files:
                chain.append(candidate)
    return chain
