"""Source-backed page inventory. Never executes untrusted application code.

Literal JSX is inspectable; runtime expressions are recorded as coverage gaps.
This is deliberately not a claim about the rendered DOM or crawlability.
"""

import re
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from seo_agents.exploration.checks.tools.helpers import SKIP_DIRS, TEXT_EXTS, rel

IMPORT = re.compile(r"import\s+([^;\n]+?)\s+from\s*['\"]([^'\"]+)['\"]")
TAG = re.compile(r"<([A-Za-z][\w.-]*)\b")
ATTR = re.compile(r'''([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|\{\s*("[^"]*"|'[^']*'|\d+|true|false)\s*\})''')
VOID = {'img', 'image', 'source', 'track', 'input', 'meta', 'link', 'br', 'hr', 'area', 'embed', 'wbr'}
HIDDEN = {'script', 'style', 'head', 'noscript'}
MEDIA = {'img', 'image', 'video', 'audio', 'source', 'track', 'iframe'}


def source_files(repo):
    """Deterministic scan with no silent file/page/match truncation."""
    import os
    for root, dirs, files in os.walk(repo, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS | {'seo_reports'} and not d.startswith('.'))
        for name in sorted(files):
            path = Path(root) / name
            if path.suffix.lower() in TEXT_EXTS and path.resolve().is_relative_to(repo):
                yield path


def routes(repo):
    from seo_agents.exploration.checks.tools.react_source import discover_app
    app = discover_app(repo)
    if app is not None:
        entry, urls, _ = app
        return [{'url': url, 'template': rel(repo, entry), 'is_dynamic': False} for url in urls]
    found = []
    for path in source_files(repo):
        parts = list(path.relative_to(repo).parts)
        url = None
        if 'app' in parts and path.stem == 'page':
            segments = parts[parts.index('app') + 1:-1]
            url = '/' + '/'.join(s for s in segments if not s.startswith(('(', '@')))
        elif 'pages' in parts:
            segments = parts[parts.index('pages') + 1:]
            if segments[0] == 'api' or path.stem.startswith('_'):
                continue
            segments[-1] = path.stem
            if segments[-1] == 'index':
                segments.pop()
            url = '/' + '/'.join(segments)
        elif path.suffix in {'.html', '.htm'} and 'templates' not in parts:
            url = '/' + str(path.relative_to(repo).with_suffix(''))
            url = re.sub(r'/index$', '/', url)
        if url is not None:
            found.append({'url': url, 'template': rel(repo, path), 'is_dynamic': '[' in url})
        # Literal React Router routes retain component bindings, not router source text.
        text = path.read_text(errors='replace')
        imports = import_map(repo, path, text)
        for match in re.finditer(r'<Route\b[^>]*\bpath=["\']([^"\']+)["\'][^>]*\belement=\{\s*<([\w]+)', text):
            target = imports.get(match[2])
            if target and match[1].startswith('/'):
                found.append({'url': match[1], 'template': rel(repo, target), 'is_dynamic': ':' in match[1] or '*' in match[1]})
    if not found:
        for name in ('src/App.tsx', 'src/App.jsx', 'App.tsx', 'App.jsx'):
            entry = repo / name
            if entry.is_file():
                found.append({'url': '/', 'template': name, 'is_dynamic': False})
                break
    return sorted({(r['url'], r['template']): r for r in found}.values(), key=lambda r: (r['url'], r['template']))


def resolve_import(repo, path, name):
    if name.startswith('.'):
        base = path.parent / name
    elif name.startswith('@/'):
        base = (repo / 'src' if (repo / 'src').is_dir() else repo) / name[2:]
    else:
        return None
    for candidate in [base] + [Path(str(base) + ext) for ext in ('.tsx', '.jsx', '.ts', '.js', '.json')] + [base / ('index' + ext) for ext in ('.tsx', '.jsx', '.ts', '.js')]:
        if candidate.is_file() and candidate.resolve().is_relative_to(repo):
            return candidate.resolve()
    return None


def import_map(repo, path, text):
    result = {}
    for match in IMPORT.finditer(text):
        target = resolve_import(repo, path, match[2])
        if target:
            names = re.findall(r'\b[A-Za-z_$][\w$]*\b', match[1])
            for name in names:
                if name not in {'as', 'type'}:
                    result[name] = target
    return result


def attributes(raw):
    result = {}
    for m in ATTR.finditer(raw):
        value = m[2] if m[2] is not None else m[3] if m[3] is not None else m[4]
        if m[4] is not None:
            value = value.strip('"\'')
        result[m[1].lower()] = unescape(value)
    for key in ('priority', 'preload', 'fill', 'controls', 'autoplay', 'muted', 'loop', 'hidden'):
        if re.search(r'\b' + key + r'\b(?!\s*=)', raw):
            result.setdefault(key, 'true')
    return result


class PageParser(HTMLParser):
    def __init__(self, file):
        super().__init__(convert_charrefs=True)
        self.file = file
        self.stack = []
        self.blocks = []
        self.links = []
        self.media = []
        self.headings = []
        self.metadata = []
        self.landmarks = {}
        self.element_count = 0

    def handle_starttag(self, tag, attrs):
        raw = self.get_starttag_text()
        a = attributes(raw)
        unresolved = [key for key, value in a.items() if value == '__SEO_UNRESOLVED__']
        a = {key: value for key, value in a.items() if key not in unresolved}
        self.element_count += 1
        node = {'source_id': f'{self.file}:{self.element_count}', 'tag': tag, 'attrs': a, 'parts': [], 'line': self.getpos()[0], 'section': next((n['tag'] for n in reversed(self.stack) if n['tag'] in {'nav', 'header', 'footer', 'main', 'article', 'aside', 'section'}), 'body')}
        if tag in {'main', 'article', 'section', 'nav', 'header', 'footer', 'aside'}:
            node['section'] = tag
            self.landmarks[tag] = self.landmarks.get(tag, 0) + 1
        if tag in {'input', 'button', 'textarea', 'select'}:
            for key in ('placeholder', 'aria-label', 'title'):
                if a.get(key):
                    self.blocks.append({**self.evidence(node), 'tag': tag, 'text': a[key], 'text_source': key})
        if tag in {'img', 'image'} and a.get('alt'):
            for ancestor in self.stack:
                if ancestor['tag'] == 'a':
                    ancestor.setdefault('image_labels', []).append(a['alt'])
        if tag in MEDIA:
            a['unresolved_attributes'] = unresolved
            self.media.append({**self.evidence(node), 'kind': 'next-image' if tag == 'image' else tag, **a})
        if tag in {'meta', 'link'}:
            self.metadata.append({**self.evidence(node), **a})
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def evidence(self, node):
        return {'source_id': node['source_id'], 'file': self.file, 'line': node['line'], 'section': node['section']}

    def handle_data(self, data):
        if self.stack and self.stack[-1]['tag'] == 'title':
            self.stack[-1]['parts'].append(re.sub(r'\s+', ' ', data).strip())
            return
        if any(n['tag'] in HIDDEN or n['attrs'].get('hidden') == 'true' or n['attrs'].get('aria-hidden') == 'true' for n in self.stack):
            return
        text = re.sub(r'\s+', ' ', data).strip()
        if text and self.stack:
            node = self.stack[-1]
            self.blocks.append({**self.evidence(node), 'tag': node['tag'], 'text': text})
            for ancestor in self.stack:
                ancestor['parts'].append(text)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]['tag'] == tag:
                closing = self.stack[i:]
                del self.stack[i:]
                for node in reversed(closing):
                    text = ' '.join(node['parts'])
                    a = node['attrs']
                    if node['tag'] in {'p', 'li', 'blockquote', 'address', 'figcaption'}:
                        for block in self.blocks:
                            if block['source_id'] == node['source_id']:
                                block['context_text'] = text
                    if node['tag'] in {'a', 'link'} and 'href' in a:
                        self.links.append({**self.evidence(node), 'href': a['href'], 'anchor_text': text, 'accessible_name': a.get('aria-label') or text or ' '.join(node.get('image_labels', [])), 'rel': a.get('rel'), 'target': a.get('target')})
                    if re.fullmatch(r'h[1-6]', node['tag']):
                        self.headings.append({**self.evidence(node), 'level': int(node['tag'][1]), 'text': text})
                    if node['tag'] == 'title':
                        self.metadata.append({**self.evidence(node), 'title': text})
                break


def literal_markup(text):
    """Keep markup only and preserve line numbers; mask JS expressions explicitly."""
    start = re.search(r'<(?:[A-Za-z]|>)', text)
    if not start:
        return '', []
    text = '\n' * text[:start.start()].count('\n') + text[start.start():]
    last = text.rfind('>')
    text = text[:last + 1]
    gaps = []
    # Brace scanner respects strings and nested object/JSX expressions.
    out, i = [], 0
    while i < len(text):
        if text[i] != '{':
            out.append(text[i])
            i += 1
            continue
        begin, depth, quote, escape = i, 1, None, False
        i += 1
        while i < len(text) and depth:
            c = text[i]
            if quote:
                if escape:
                    escape = False
                elif c == '\\':
                    escape = True
                elif c == quote:
                    quote = None
            elif c in "\"'`":
                quote = c
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
            i += 1
        raw = text[begin:i]
        value = raw[1:-1].strip()
        if re.fullmatch(r'''(?:"[^"\n]*"|'[^'\n]*'|\d+|true|false)''', value):
            in_tag = text.rfind('<', 0, begin) > text.rfind('>', 0, begin)
            if in_tag:
                out.append(value if value.startswith(('"', "'")) else '"' + value + '"')
            else:
                out.append(value.strip('"\''))
        elif value == 'children':
            out.append('')
        else:
            gaps.append({'line': text[:begin].count('\n') + 1, 'expression': value, 'reason': 'runtime_expression_not_evaluated'})
            in_tag = text.rfind('<', 0, begin) > text.rfind('>', 0, begin)
            out.append(('"__SEO_UNRESOLVED__"' if in_tag else '') + '\n' * raw.count('\n'))
    markup = ''.join(out)
    markup = re.sub(r'<Link\b', '<a', markup).replace('</Link>', '</a>')
    return markup, gaps


def parse_file(repo, path, seen=None):
    seen = set() if seen is None else seen
    if path in seen:
        return [], [{'file': rel(repo, path), 'reason': 'component_cycle'}]
    seen = seen | {path}
    text = path.read_text(errors='replace')
    # Resolve only literal constants and asset imports; never evaluate JS.
    literals = {m[1]: m[2] for m in re.finditer(r'''\bconst\s+(\w+)\s*=\s*("[^"\n]*"|'[^'\n]*')''', text)}
    for name, target in import_map(repo, path, text).items():
        if target.suffix.lower() in {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.avif', '.mp4', '.webm'}:
            literals[name] = repr('/' + rel(repo, target))
    for name, value in literals.items():
        text = re.sub(r'\{\s*' + re.escape(name) + r'\s*\}', lambda match: '{' + value + '}', text)
    markup, gaps = literal_markup(text)

    parser = PageParser(rel(repo, path))
    parser.feed(markup)
    parser.close()
    parsers = [parser]
    unknown = [{'file': rel(repo, path), **gap} for gap in gaps]
    imports = import_map(repo, path, text)
    for name in dict.fromkeys(TAG.findall(markup)):
        if not name[0].isupper() or name in {'Image', 'Link', 'Head'}:
            continue
        target = imports.get(name)
        if target and target.suffix in {'.tsx', '.jsx', '.ts', '.js'}:
            children, missing = parse_file(repo, target, seen)
            parsers.extend(children)
            unknown.extend(missing)
        else:
            unknown.append({'file': rel(repo, path), 'component': name, 'reason': 'unresolved_component'})
    # Keep exact metadata literals; never infer metadata from unrelated title fields.
    if re.search(r'export\s+(?:const\s+metadata|(?:async\s+)?function\s+generateMetadata)', text):
        for key in ('title', 'description'):
            match = re.search(r'\b' + key + r'''\s*:\s*(["'])(.*?)\1''', text, re.S)
            if match:
                parser.metadata.append({'file': rel(repo, path), 'line': text[:match.start()].count('\n') + 1, key: match[2], 'source': 'metadata-export-literal'})
        unknown.append({'file': rel(repo, path), 'reason': 'metadata_export_requires_runtime_resolution_for_nonliteral_fields'})
    return parsers, unknown


def pages(repo):
    from seo_agents.exploration.checks.tools.react_source import application_pages
    composed = application_pages(repo)
    if composed is not None:
        return composed
    result = []
    for route in routes(repo):
        path = repo / route['template']
        roots = []
        # Ancestor App Router layouts and Pages Router shared app wrapper.
        if 'app' in path.relative_to(repo).parts:
            for parent in reversed(path.parents):
                if parent.is_relative_to(repo) and 'app' in parent.relative_to(repo).parts:
                    for ext in ('.tsx', '.jsx', '.ts', '.js'):
                        layout = parent / ('layout' + ext)
                        if layout.is_file():
                            roots.append(layout)
        elif 'pages' in path.relative_to(repo).parts:
            parent = path
            while parent.name != 'pages':
                parent = parent.parent
            roots.extend(p for p in parent.glob('_app.*') if p.suffix in {'.tsx', '.jsx', '.ts', '.js'})
        roots.append(path)
        parsers, gaps = [], []
        for root in roots:
            p, g = parse_file(repo, root)
            parsers.extend(p)
            gaps.extend(g)
        if route['is_dynamic']:
            gaps.append({'file': route['template'], 'reason': 'dynamic_route_instances_not_enumerated'})
        result.append({**route, 'parsers': parsers, 'coverage': {'mode': 'static-source', 'complete': False, 'limitations': gaps, 'note': 'Runtime data, conditions, component props, CSS visibility and rendered order require browser verification.'}})
    return result


def page_identity(page):
    return {key: page[key] for key in ('url', 'template', 'is_dynamic', 'coverage')}


def normalize_link(href, page_url):
    if not href or href.startswith(('mailto:', 'tel:', 'javascript:', 'data:')):
        return {'target_url': href, 'kind': 'action'}
    if href.startswith(('http://', 'https://', '//')):
        return {'target_url': href, 'kind': 'absolute-unclassified'}
    target = urljoin(page_url, href)
    resource = bool(re.search(r'\.(?:pdf|png|jpe?g|gif|svg|webp|avif|mp4|webm|json|md|zip)$', urlsplit(target).path, re.I))
    return {'target_url': target, 'kind': 'resource' if resource else 'internal'}


def media_details(repo, item):
    item = dict(item)
    src = item.get('src')
    path = None
    if src and not urlsplit(src).scheme and not src.startswith('//'):
        clean = unquote(urlsplit(src).path)
        candidates = [repo / 'public' / clean.lstrip('/'), (repo / item['file']).parent / clean, repo / clean.lstrip('/')]
        path = next((p for p in candidates if p.is_file() and p.resolve().is_relative_to(repo)), None)
    from seo_agents.exploration.checks.tools.asset_probe import probe_asset
    measurements = probe_asset(path) if path else {}
    size = measurements.get('size_bytes')
    width = measurements.get('intrinsic_width')
    height = measurements.get('intrinsic_height')
    loading = item.get('loading')
    unknown_loading = bool({'loading', 'priority', 'preload'} & set(item.get('unresolved_attributes', [])))
    if not loading and not unknown_loading and item['kind'] in {'img', 'next-image', 'iframe'}:
        loading = 'eager' if item.get('priority') == 'true' or item.get('preload') == 'true' or item['kind'] != 'next-image' else 'lazy'
    return {**item, 'src': src, 'format': Path(urlsplit(src).path).suffix.lstrip('.').lower() if src else None, 'asset_file': rel(repo, path) if path else None,
            'size_bytes': size, 'intrinsic_width': width, 'intrinsic_height': height,
            'duration_seconds': measurements.get('duration_seconds'), 'probe_status': measurements.get('measurement_status', 'unresolved-or-remote'),
            'declared_width': item.get('width'), 'declared_height': item.get('height'),
            'effective_loading': loading, 'loading_basis': 'unknown-dynamic' if unknown_loading else 'declared' if 'loading' in item else 'framework-or-browser-default' if loading else 'not-applicable',
            'measurement_status': 'local-source-file' if path else 'unresolved-or-remote',
            'size_review': 'not-an-image-budget' if item['kind'] not in {'img', 'next-image'} else 'over_200_kib_review' if size is not None and size > 200 * 1024 else 'within_200_kib_budget' if size is not None else 'unknown',
            'size_budget_note': 'Review budget only; source bytes are not optimized transfer bytes. Display size and placement are needed to assess undersizing.'}


def inventory(repo, category, *, page_inventory=None):
    """Category-specific JSON contract; observations only, with explicit coverage."""
    output = []
    for page in page_inventory if page_inventory is not None else pages(repo):
        parsers = page['parsers']
        row = page_identity(page)
        blocks = [b for p in parsers for b in p.blocks]
        links = [{**link, **normalize_link(link['href'], page['url'])} for p in parsers for link in p.links]
        if category == 'content':
            row['sections'] = [section for p in parsers for section in getattr(p, 'sections', [])]
            row.update(blocks=blocks, word_count=sum(len(b['text'].split()) for b in blocks), link_refs=[{'source_id': l['source_id'], 'report': 'internal_links.json'} for l in links], media_refs=[{'source_id': m['source_id'], 'report': 'media.json'} for p in parsers for m in p.media])
        elif category == 'page_structure':
            headings = [h for p in parsers for h in p.headings]
            row.update(headings=headings, landmarks=[{'file': p.file, 'landmarks': p.landmarks, 'sections': getattr(p, 'sections', [])} for p in parsers])
        elif category == 'internal_links':
            row['links'] = links
        elif category == 'metadata':
            row['declarations'] = [m for p in parsers for m in p.metadata]
            row['inheritance_note'] = 'Declarations include ancestor layouts; effective metadata merging is not evaluated.'
        elif category == 'media':
            row['items'] = [media_details(repo, m) for p in parsers for m in p.media]
        elif category == 'eeat':
            signals = []
            patterns = {'authorship': r'\b(written by|reviewed by|author|byline)\b', 'experience': r'\b(work experience|selected work|how i got here|our experience|we tested|case study|first.hand)\b', 'expertise': r'\b(skills|certified|qualification|credential|phd|licensed)\b', 'trust': r'\b(privacy policy|terms of service|contact us|editorial policy|correction)\b', 'reviews': r'\b(testimonial|verified review|customer review)\b', 'dates': r'\b(published|updated|modified)\b'}
            for signal, pattern in patterns.items():
                evidence = [b for b in blocks if re.search(pattern, b.get('section_heading', '') + ' ' + b['text'], re.I)]
                signals.append({'signal': signal, 'status': 'candidate_evidence' if evidence else 'not_observed_in_static_content', 'evidence': evidence})
            row.update(signals=signals, trust_links=[l for l in links if re.search(r'about|contact|privacy|terms|editorial|author|mailto:|linkedin|github.com/[^/]+/?$', l['href'] + ' ' + l.get('accessible_name', l['anchor_text']), re.I)], citations=[l for l in links if l['kind'] == 'absolute-unclassified'], interpretation='Candidate evidence does not verify experience, credentials, reputation, or review authenticity. Absence is not proof of absence at runtime.')
        elif category == 'urls':
            row.update(depth=len([p for p in page['url'].split('/') if p]), query_links=[l for l in links if '?' in l['href']])
        else:
            raise ValueError('Unknown report category: ' + category)
        output.append(row)
    from seo_agents.exploration.checks.tools.contracts import validate_report
    report = {'schema_version': '2.0', 'category': category, 'extraction': {'mode': 'static-source', 'route_count': len(output), 'limitations': ['File routes, literal React Router routes and supported pathname dispatch are enumerated; arbitrary routing remains unresolved.', 'No application code is executed; runtime content is unknown.', 'Supported React composition follows source-selected component order; browser layout and visibility are unverified.']}, 'pages': output}
    if category == 'media':
        report['assets'] = asset_manifest(repo, output)
    return validate_report(category, report)


def asset_manifest(repo, pages):
    """Keep unused media distinct from page-visible or interaction-only media."""
    import os
    from seo_agents.exploration.checks.tools.asset_probe import probe_asset
    usages = {}
    for page in pages:
        for item in page['items']:
            if item.get('asset_file'):
                usages.setdefault(item['asset_file'], []).append({'url': page['url'], 'source_id': item['source_id'], 'ui_state': item.get('ui_state', 'initial')})
    assets = []
    extensions = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif', '.svg', '.mp4', '.webm', '.mov', '.mp3', '.wav', '.ogg'}
    for root, dirs, files in os.walk(repo, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS | {'seo_reports'} and not d.startswith('.'))
        for name in sorted(files):
            path = Path(root) / name
            if path.suffix.lower() not in extensions or not path.resolve().is_relative_to(repo):
                continue
            filename = rel(repo, path)
            references = usages.get(filename, [])
            assets.append({'asset_file': filename, 'format': path.suffix.lower().lstrip('.'), **probe_asset(path), 'page_references': references, 'usage_status': 'page-referenced' if references else 'not-in-page-media-inventory'})
    return assets
