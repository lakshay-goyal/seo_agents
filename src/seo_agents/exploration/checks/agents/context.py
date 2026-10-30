"""Deterministic repository context, built once; candidates retain their method."""

from collections import Counter
from html.parser import HTMLParser
import json
import posixpath
from pathlib import PurePosixPath
import xml.etree.ElementTree as ET

from tree_sitter import Language, Parser
import tree_sitter_javascript
import tree_sitter_typescript


def strict_json(source):
    def reject_constant(value):
        raise ValueError('Non-JSON numeric constant: ' + value)
    return json.loads(source, parse_constant=reject_constant)


class SourceHTML(HTMLParser):
    """Literal HTML evidence; never simulates templates or a rendered DOM."""
    def __init__(self, file):
        super().__init__(convert_charrefs=True)
        self.file, self.records, self.stack = file, [], []
        self.script = None

    def ref(self, kind):
        line, column = self.getpos()
        return {'file': self.file, 'line': line, 'column': column,
                'source_id': f'{self.file}:{line}:{column}:{kind}', 'method': 'literal-html-source'}

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'script' and attrs.get('type', '').lower() == 'application/ld+json':
            self.script = {**self.ref('json-ld'), 'kind': 'structured-data', 'raw': ''}
        if tag in {'meta', 'link', 'base', 'html'}:
            self.records.append({**self.ref(tag), 'kind': 'head-declaration', 'tag': tag, 'attributes': attrs})
        if tag not in {'meta', 'link', 'base', 'img', 'br', 'hr', 'input', 'source', 'area', 'embed', 'wbr'}:
            self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        if tag == 'script' and self.script is not None:
            try:
                self.script['value'] = strict_json(self.script['raw'])
                self.script['parse_status'] = 'valid-json'
            except (ValueError, RecursionError) as error:
                self.script.update(parse_status='invalid-json', parse_error=str(error))
            self.script['validation_scope'] = 'JSON syntax only; schema/search-feature eligibility unverified'
            self.records.append(self.script)
            self.script = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if self.script is not None:
            self.script['raw'] += data
        if not data.strip() or any(tag in {'script', 'style'} for tag, _ in self.stack):
            return
        if self.stack:
            self.records.append({**self.ref('text'), 'kind': 'literal-text', 'text': data,
                                 'tag': self.stack[-1][0], 'ancestors': [tag for tag, _ in self.stack],
                                 'visibility': 'unverified'})

    def close(self):
        super().close()
        if self.script is not None:
            self.script.update(parse_status='unterminated-script', validation_scope='not-validated')
            self.records.append(self.script)
            self.script = None


def json_values(value, pointer=''):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from json_values(child, pointer + '/' + key.replace('~', '~0').replace('/', '~1'))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from json_values(child, pointer + '/' + str(index))
    else:
        yield pointer, value


def resolve_import(file, specifier, files):
    if not specifier.startswith('.'):
        return None
    base = posixpath.normpath(posixpath.join(posixpath.dirname(file), specifier))
    candidates = [base] + [base + ext for ext in ('.ts', '.tsx', '.js', '.jsx', '.json', '.vue', '.svelte', '.astro')]
    candidates += [base + '/index' + ext for ext in ('.ts', '.tsx', '.js', '.jsx')]
    return next((path for path in candidates if path in files), None)


def syntax_context(file, source, files, parsers):
    suffix = PurePosixPath(file).suffix
    language = 'tsx' if suffix == '.tsx' else 'ts' if suffix in {'.ts', '.mts', '.cts'} else 'js'
    if language not in parsers:
        factory = tree_sitter_typescript.language_tsx if language == 'tsx' else tree_sitter_typescript.language_typescript if language == 'ts' else tree_sitter_javascript.language
        parsers[language] = Parser(Language(factory()))
    root = parsers[language].parse(source.encode()).root_node
    records, stack = [], [root]
    while stack:
        node = stack.pop()
        stack.extend(reversed(node.named_children))
        target = None
        kind = None
        if node.type in {'import_statement', 'export_statement'}:
            target = node.child_by_field_name('source')
            kind = 'module-dependency'
        elif node.type == 'call_expression':
            function = node.child_by_field_name('function')
            name = function.text.decode() if function else ''
            if name in {'require', 'import', 'fetch', 'axios.get'}:
                args = node.child_by_field_name('arguments')
                target = args.named_children[0] if args and args.named_children else None
                kind = 'module-dependency' if name in {'require', 'import'} else 'data-source-call'
        if target is not None:
            literal = target.type == 'string'
            specifier = target.text.decode()[1:-1] if literal else None
            records.append({'kind': kind, 'file': file, 'line': node.start_point.row + 1,
                            'column': node.start_point.column, 'specifier': specifier,
                            'source_id': f'{file}:{node.start_point.row + 1}:{node.start_point.column}:{kind}',
                            'expression': target.text.decode() if not literal else None,
                            'resolved_file': resolve_import(file, specifier, files) if literal and kind == 'module-dependency' else None,
                            'method': 'tree-sitter', 'syntax_has_errors': root.has_error,
                            'availability': 'source-reference-only; no package, API or CMS request performed'})
    return records, root.has_error


def build_context(snapshot):
    files = snapshot['files']
    records, packages, parse_errors, parsers, coverage = [], [], [], {}, []
    parsed = Counter()
    for file, source in files.items():
        suffix = PurePosixPath(file).suffix.lower()
        before = len(records)
        try:
            if suffix == '.json':
                document = strict_json(source)
                parsed['json'] += 1
                if PurePosixPath(file).name == 'package.json' and isinstance(document, dict):
                    dependencies = {**document.get('dependencies', {}), **document.get('devDependencies', {})}
                    packages.append({'file': file, 'root': posixpath.dirname(file) or '.', 'name': document.get('name'),
                                     'frameworks': sorted(set(dependencies) & {'next', 'react', 'vue', 'nuxt', 'svelte', '@sveltejs/kit', 'astro', '@angular/core', 'gatsby', '@11ty/eleventy'}),
                                     'workspaces': document.get('workspaces')})
                for pointer, value in json_values(document):
                    records.append({'kind': 'json-value', 'file': file, 'pointer': pointer,
                                    'value': value, 'source_id': file + '#' + pointer,
                                    'method': 'json-parser', 'page_visibility': 'unestablished'})
            elif suffix in {'.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx', '.mts', '.cts'}:
                found, errors = syntax_context(file, source, files, parsers)
                records.extend(found)
                parsed['javascript-typescript'] += 1
                if errors:
                    parse_errors.append({'file': file, 'reason': 'syntax-errors; partial AST candidates retained'})
            elif suffix in {'.html', '.htm'}:
                parser = SourceHTML(file)
                parser.feed(source)
                parser.close()
                records.extend(parser.records)
                parsed['html'] += 1
            elif suffix in {'.md', '.mdx'}:
                records.append({'kind': 'content-document', 'file': file, 'line': 1,
                                'corpus_ref': '/source_corpus/' + file.replace('~', '~0').replace('/', '~1'),
                                'method': 'raw-markdown-reference', 'page_visibility': 'unestablished'})
            elif 'sitemap' in PurePosixPath(file).name and suffix == '.xml':
                root = ET.fromstring(source)
                for element in root.iter():
                    if element.tag.rsplit('}', 1)[-1] == 'loc' and element.text:
                        records.append({'kind': 'sitemap-child' if root.tag.rsplit('}', 1)[-1] == 'sitemapindex' else 'sitemap-url',
                                        'file': file, 'value': element.text.strip(), 'method': 'xml-parser'})
                parsed['sitemap'] += 1
            elif PurePosixPath(file).name == 'robots.txt':
                records.append({'kind': 'robots-declaration', 'file': file, 'line': 1,
                                'raw': source, 'method': 'raw-source', 'deployed_behavior': 'unverified'})
        except (ValueError, RecursionError, ET.ParseError, TypeError, AttributeError) as error:
            parse_errors.append({'file': file, 'reason': type(error).__name__, 'detail': str(error)[:300]})
        coverage.append({'file': file, 'records': len(records) - before,
                         'status': 'partial-or-invalid' if parse_errors and parse_errors[-1]['file'] == file else
                         'indexed' if suffix in {'.json', '.js', '.jsx', '.mjs', '.cjs', '.ts', '.tsx', '.mts', '.cts', '.html', '.htm', '.md', '.mdx'}
                         or len(records) > before else 'source-available-unparsed'})
    return {'records': records, 'packages': packages, 'parse_errors': parse_errors,
            'file_coverage': coverage,
            'parse_counts': dict(parsed), 'limitations': [
                'Dependencies are literal module edges, not a complete execution/data-flow graph.',
                'JSON values and Markdown are source candidates; page usage requires composition evidence.',
                'Vue/Svelte/Astro and server template semantics are explored by agents, not this JS/HTML index.',
                'Sitemap/robots files describe source intentions; no remote child sitemap is fetched.']}
