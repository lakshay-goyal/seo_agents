"""Stage E: attribute every measured (rendered or raw) value to an edit location.

For each observed value the attributor tries, in order:
1. lens agreement  - the source lens already produced the same value for this field -> reuse its edit;
2. exact match     - a literal text/attribute in the element index (page files, then composition, then repo);
3. data match      - a JSON/YAML/front-matter string (edit.kind json|yaml|markdown + pointer);
4. i18n match      - a translation catalog value (edit.kind i18n + key);
5. template match  - the source value decorated by a title template / composed expression (computed + via);
6. otherwise       - `computed` (or `cms` on CMS stacks) with the source candidate as `via`.

Several equally good candidates -> the field keeps the best guess with low confidence
and a `candidates` list, and a gap item is queued for a small-model worker. The
model chooses a candidate ID; code fills file/line/col (doc §13 "constrain the answer").
"""

from __future__ import annotations

from collections import defaultdict
import copy
import re
from urllib.parse import urlsplit

from seo_agents.exploration.repo_analysis.source_lens import field

NON_COPY_ATTRS = {'id', 'class', 'classname', 'key', 'href', 'src', 'srcset', 'type', 'rel', 'role', 'for', 'htmlfor', 'style',
                  'target', 'name', 'variant', 'size', 'color', 'as', 'ref', 'slot', 'lang', 'loading', 'width', 'height', 'to'}
CMS_STACKS = {'shopify-theme', 'wordpress-theme', 'wordpress-block-theme', 'ghost-theme', 'drupal', 'magento'}


def norm(value):
    import html
    return ' '.join(html.unescape(str(value or '')).split()).casefold()


def path_of(url):
    if url is None:
        return None
    parsed = urlsplit(url)
    return (parsed.path or '/').rstrip('/') or '/'


class Attributor:
    def __init__(self, ctx):
        self.ctx = ctx
        self.gaps = []
        self.text = defaultdict(list)
        self.attrs = defaultdict(list)
        for element in ctx.index['elements']:
            if element.get('text_literal'):
                self.text[norm(element['text_literal'])].append(element)
            for name, value in element['attrs'].items():
                if 'literal' in value and value['literal']:
                    self.attrs[norm(value['literal'])].append((element, name))
        self.data = defaultdict(list)
        self.assets = defaultdict(list)
        self.data_values = []
        for record in ctx.index['data']:
            if record['kind'] == 'asset-ref':
                self.assets[asset_key(record['value'])].append(record)
                continue
            self.data[norm(record['value'])].append(record)
            if 3 <= len(record['value']) <= 300:
                self.data_values.append((norm(record['value']), record))
        # Elements whose text/attribute mixes literals and expressions: candidate templates for composed values.
        self.templates = []
        for element in ctx.index['elements']:
            for name, value in [(None, {'parts': element.get('text', [])})] + list(element['attrs'].items()):
                parts = value['parts'] if name is None else [value]
                exprs = [p for p in parts if 'expr' in p]
                if not exprs:
                    continue
                fragments = [norm(p['literal']) for p in parts if 'literal' in p] + \
                            [norm(f) for p in exprs for f in p.get('fragments', [])]
                refs = {r.casefold() for p in exprs for r in p.get('refs', [])}
                self.templates.append((element, name, [f for f in fragments if f], refs))
        self.i18n = defaultdict(list)
        for key, entries in ctx.index['i18n'].items():
            for entry in entries:
                self.i18n[norm(entry['value'])].append((key, entry))
        self.cms = ctx.fingerprint.get('primary') in CMS_STACKS

    # ------------------------------------------------------------ candidate search
    def snippet(self, file, line):
        lines = self.ctx.files.get(file, '').splitlines()
        return lines[line - 1].strip()[:200] if 0 < line <= len(lines) else ''

    def search(self, value, composition, page_files, *, attr_names=None):
        """Exact candidates for an observed value, best tier first."""
        key = norm(value)
        if not key or len(key) < 2:
            return []
        found = []
        for element in self.text.get(key, []):
            found.append({'kind': 'literal', 'file': element['file'], 'line': element['line'], 'col': element['col'], 'el': element['id']})
        for element, name in self.attrs.get(key, []):
            if (attr_names is None and name.lower() not in NON_COPY_ATTRS and not name.lower().startswith(('data-', 'on', 'aria-hidden'))) \
                    or (attr_names is not None and name in attr_names):
                found.append({'kind': 'prop' if element['kind'] == 'component' else 'literal', 'file': element['file'],
                              'line': element['line'], 'col': element['col'], 'el': element['id'], 'attr': name})
        for record in self.data.get(key, []):
            suffix = record['file'].rsplit('.', 1)[-1].lower()
            kind = 'markdown' if record['kind'] == 'front-matter' else 'json' if suffix == 'json' else 'yaml' if suffix in {'yml', 'yaml'} else 'toml' if suffix == 'toml' else 'json'
            found.append({'kind': kind, 'file': record['file'], 'line': record['line'], 'pointer': record['pointer']})
        for i18n_key, entry in self.i18n.get(key, []):
            found.append({'kind': 'i18n', 'file': entry['file'], 'line': entry['line'], 'i18n_key': i18n_key, 'pointer': entry.get('pointer')})

        def tier(candidate):
            if candidate['file'] in page_files:
                return 0
            if candidate['file'] in composition:
                return 1
            if candidate['kind'] in {'i18n', 'json', 'yaml', 'markdown', 'toml'}:
                return 2
            return 3
        # An ancestor whose descendant text equals the value (e.g. <html> of an empty SPA shell
        # containing only <title>) is not the location; keep the innermost element.
        ids = {c['el'] for c in found if c.get('el')}
        ancestors = set()
        for element_id in ids:
            parent = self.ctx.by_id.get(element_id, {}).get('parent')
            while parent:
                ancestors.add(parent)
                parent = self.ctx.by_id.get(parent, {}).get('parent')
        found = [c for c in found if not c.get('el') or c['el'] not in ancestors or c.get('attr')]
        found.sort(key=lambda c: (tier(c), c['file'], c['line']))
        unique, seen = [], set()
        for candidate in found:
            identity = (candidate['file'], candidate['line'], candidate.get('pointer'), candidate.get('attr'))
            if identity not in seen:
                seen.add(identity)
                candidate['tier'] = tier(candidate)
                unique.append(candidate)
        return unique[:10]

    def composed(self, value, composition, page_files, attr=None):
        """Values assembled from data rows and a template (e.g. `{exp.role} at {exp.company}`)."""
        key = norm(value)
        if len(key) < 4:
            return None
        groups = defaultdict(list)
        for text, record in self.data_values:
            if text in key and len(text) >= 3:
                parent = record['pointer'].rsplit('/', 1)[0]
                groups[(record['file'], parent)].append(record)
        best = None
        for (file, parent), records in groups.items():
            covered = sum(len(norm(r['value'])) for r in records)
            if best is None or covered > best[0]:
                best = (covered, file, parent, records)
        letters = len(re.sub(r'\W', '', key))
        data_part = None
        if best and best[0] >= 0.5 * letters:
            covered, file, parent, records = best
            data_part = {'kind': data_kind(records[0]), 'file': file, 'line': min(r['line'] for r in records), 'pointer': parent,
                         'composed_from': sorted(r['pointer'] for r in records)}
            keys = {r['pointer'].rsplit('/', 1)[-1].casefold() for r in records}
        else:
            keys = set()
        template = None
        for element, name, fragments, refs in self.templates:
            if attr is not None and name not in (attr if isinstance(attr, set) else {attr}):
                continue
            if attr is None and name is not None:
                continue
            if not all(fragment in key for fragment in fragments):
                continue
            score = sum(len(f) for f in fragments) + 5 * len(keys & refs) + (3 if element['file'] in composition else 0)
            if (fragments or keys & refs) and (template is None or score > template[0]):
                template = (score, element)
        if data_part is None and template is None:
            return None
        if data_part is not None:
            edit = data_part
            if template:
                edit['template'] = {'file': template[1]['file'], 'line': template[1]['line'], 'col': template[1]['col']}
            return field(value, 'rendered', edit, 0.75 if template else 0.65, composed=True)
        element = template[1]
        return field(value, 'rendered', {'kind': 'computed', 'file': element['file'], 'line': element['line'], 'col': element['col'],
                                         'via': 'template with expressions; literal parts match'}, 0.55, composed=True)

    def decide(self, page, pointer, value, candidates, fallback=None, method='rendered'):
        """Pick an edit; queue a gap when the best tier is ambiguous."""
        if not candidates:
            if value and (fallback is None or not fallback.get('edit') or fallback['edit'].get('kind') in {'computed', 'cms', 'add'}):
                composed = self.composed(value, self.current_composition, self.current_page_files,
                                         attr=self.current_attr)
                if composed is not None:
                    return composed | {'method': method}
            if fallback and fallback.get('edit'):
                edit = dict(fallback['edit'])
                if edit.get('kind') == 'add':
                    # The page has a value the source lens could not find: it is generated somewhere.
                    edit = {'kind': 'cms' if self.cms else 'computed', 'file': edit.get('file'), 'line': edit.get('line', 1),
                            'via': 'served value has no literal source; ' + str(edit.get('anchor') or '')}
                if edit.get('kind') == 'literal' and norm(fallback.get('value')) != norm(value):
                    edit['kind'] = 'cms' if self.cms else 'computed'
                    edit['via'] = 'source literal differs from served value: ' + str(fallback.get('value'))[:80]
                return field(value, method, edit, 0.5, lens_agreement=False)
            return field(value, method, {'kind': 'cms' if self.cms else 'computed', 'file': page['template'], 'line': 1,
                                         'via': 'no source location matched the served value'} if page.get('template') else None, 0.3,
                         lens_agreement=False)
        best = [c for c in candidates if c['tier'] == candidates[0]['tier']]
        chosen = {k: v for k, v in best[0].items() if k not in {'tier', 'el'}}
        if len(best) == 1:
            return field(value, method, chosen, 0.9 if best[0]['tier'] <= 1 else 0.75, el=best[0].get('el'))
        options = [{'id': 'c' + str(index + 1), **{k: v for k, v in c.items() if k != 'tier'}, 'snippet': self.snippet(c['file'], c['line'])}
                   for index, c in enumerate(candidates)]
        self.gaps.append({'task': 'attribute_value', 'page_id': page['id'], 'url': page['url'], 'field': pointer,
                          'observed': value, 'candidates': options, 'template': page.get('template')})
        return field(value, method, chosen, 0.45, candidates=options, gap='attribution-ambiguous')

    # ------------------------------------------------------------ fields
    def attribute(self, page, source, summary, lens):
        values = copy.deepcopy(source)
        composition = set(source.get('composition', []))
        page_files = {page['template']} | {f for f in composition if f.startswith(page['template'].rsplit('/', 1)[0])} if page.get('template') else set()
        self.current_composition, self.current_page_files, self.current_attr = composition, page_files, None
        head = values['head']
        method = 'rendered' if lens == 'rendered' else 'raw-html'

        def head_value(key, observed, attr_names=None):
            current = head.get(key) or field()
            if observed is None:
                if current.get('value') is not None:
                    head[key] = field(None, method, current.get('edit'), 0.6, source_declared=current.get('value'),
                                      lens_agreement=False, note='declared in source but absent from the served page')
                else:
                    head[key] = field(None, method, current.get('edit'), current.get('confidence') or 0.6)
                return
            if norm(current.get('value')) == norm(observed) and current.get('edit'):
                head[key] = current | {'value': observed, 'method': method, 'confidence': 0.97, 'lens_agreement': True}
                return
            via = current.get('via_template')
            if via and current.get('edit') and norm(observed).find(norm(current.get('value') or '').split('|')[0].strip()) >= 0:
                head[key] = current | {'value': observed, 'method': method, 'confidence': 0.85, 'lens_agreement': True}
                return
            candidates = self.search(observed, composition, page_files, attr_names=attr_names)
            head[key] = self.decide(page, '/head/' + key, observed, candidates, current, method)
        head_value('title', summary.get('title'))
        head_value('description', summary.get('description'), {'content'})
        head_value('canonical', summary.get('canonical'), {'href'})
        head_value('robots', summary.get('robots'), {'content'})
        head_value('lang', summary.get('lang'), {'lang'})
        head_value('viewport', summary.get('viewport'), {'content'})
        for group in ('og', 'twitter'):
            declared = head.get(group) or {}
            served = summary.get(group) or {}
            merged = {}
            for name in sorted(set(declared) | set(served)):
                current = declared.get(name) or field()
                observed = served.get(name)
                if observed is not None and norm(current.get('value')) == norm(observed):
                    merged[name] = current | {'value': observed, 'method': method, 'confidence': 0.95, 'lens_agreement': True}
                elif observed is not None:
                    merged[name] = self.decide(page, f'/head/{group}/{name}', observed,
                                               self.search(observed, composition, page_files, attr_names={'content'}), current, method)
                else:
                    merged[name] = field(None, method, current.get('edit'), 0.6, source_declared=current.get('value'), lens_agreement=False)
            head[group] = merged
        head['hreflang'] = [{'hreflang': h.get('hreflang'), 'href': h.get('href'),
                             'edit': next((s['edit'] for s in source['head'].get('hreflang', []) if norm(s.get('href')) == norm(h.get('href'))), None)}
                            for h in summary.get('hreflang', [])]
        head['json_ld'] = [{'types': item.get('types', []), 'valid': item.get('valid'), 'error': item.get('error'),
                            'edit': next((s['edit'] for s in source['head'].get('json_ld', []) if set(s.get('types', [])) & set(item.get('types', []))), None)}
                           for item in summary.get('json_ld', [])]
        head['canonical_count'] = summary.get('canonical_count')
        # Headings: reuse source edits by text, otherwise search.
        unused = list(source['headings'])
        headings = []
        for observed in summary.get('headings', []):
            match = next((s for s in unused if s['level'] == observed['level'] and norm(s['text']) == norm(observed['text'])), None)
            if match:
                unused.remove(match)
                headings.append({**match, 'text': observed['text'], 'method': method, 'confidence': 0.95, 'lens_agreement': True,
                                 'path': observed.get('path'), 'visible': observed.get('visible')})
                continue
            decided = self.decide(page, f'/headings/{len(headings)}', observed['text'],
                                  self.search(observed['text'], composition, page_files), None, method)
            headings.append({'level': observed['level'], 'text': observed['text'], 'el': decided.get('el'), 'method': method,
                             'edit': decided['edit'], 'confidence': decided['confidence'], 'section': observed.get('section'),
                             'path': observed.get('path'), 'visible': observed.get('visible'),
                             **({'candidates': decided['candidates']} if decided.get('candidates') else {})})
        values['headings'] = headings
        values['source_only_headings'] = [{'level': s['level'], 'text': s['text'], 'edit': s['edit']} for s in unused][:30]
        # Links
        source_links = list(source['links']['out'])
        links = []
        for observed in summary.get('links', []):
            href, anchor = observed.get('href'), observed.get('text') or ''
            match = next((s for s in source_links if norm(s.get('anchor')) == norm(anchor) and
                          (path_of(s.get('href')) == path_of(href) or s.get('href') is None)), None)
            if match is None:
                match = next((s for s in source_links if path_of(s.get('href')) == path_of(href) and s.get('href')), None)
            if match is not None:
                source_links.remove(match)
                edit, anchor_edit, confidence = match['edit'], match.get('anchor_edit'), 0.9
            else:
                decided = self.decide(page, f'/links/out/{len(links)}', anchor or href,
                                      self.search(anchor, composition, page_files) if anchor else [], None, method)
                edit, anchor_edit, confidence = decided['edit'], decided['edit'], decided['confidence']
            links.append({'href': href, 'resolved': observed.get('resolved'), 'anchor': anchor, 'aria_label': observed.get('aria_label'),
                          'rel': observed.get('rel'), 'target': observed.get('target'), 'section': observed.get('section'),
                          'in_main': observed.get('in_main'), 'kind': link_kind(href, observed.get('resolved'), page, self.ctx.own_hosts),
                          'visible': observed.get('visible'), 'img_alt': observed.get('img_alt') or None,
                          'edit': edit, 'anchor_edit': anchor_edit, 'method': method, 'confidence': confidence})
        values['links'] = {'out': links, 'in_count': None, 'click_depth': None}
        # Images
        source_images = list(source['images'])
        images = []
        for observed in summary.get('images', []):
            name = (observed.get('src') or '').split('?')[0].rsplit('/', 1)[-1]
            match = next((s for s in source_images if s.get('alt') is not None and norm(s.get('alt')) == norm(observed.get('alt'))
                          and observed.get('alt')), None) or \
                next((s for s in source_images if s.get('src') and s['src'].split('?')[0].rsplit('/', 1)[-1] == name), None)
            if match is not None:
                source_images.remove(match)
            data_src = self.assets.get(asset_key(observed.get('src'))) if match is None else None
            alt_edit = (match or {}).get('alt_edit')
            if match is None and observed.get('alt'):
                self.current_attr = {'alt'}
                alt_field = self.composed(observed['alt'], composition, page_files, attr={'alt'})
                self.current_attr = None
                alt_edit = alt_field['edit'] if alt_field else None
            record = {'src': observed.get('src'), 'resolved': observed.get('resolved'), 'alt': observed.get('alt'),
                      'alt_missing': observed.get('alt') is None, 'width': observed.get('width'), 'height': observed.get('height'),
                      'loading': observed.get('loading'), 'fetchpriority': observed.get('fetchpriority'),
                      'srcset': bool(observed.get('srcset')), 'natural': observed.get('natural'), 'rendered': observed.get('rendered'),
                      'above_fold': observed.get('above_fold'), 'transfer_bytes': observed.get('transfer_bytes'),
                      'format': (name.rsplit('.', 1)[-1].lower() if '.' in name else None), 'section': observed.get('section'),
                      'edit': (match or {}).get('edit') or ({'kind': data_kind(data_src[0]), 'file': data_src[0]['file'],
                                                             'line': data_src[0]['line'], 'pointer': data_src[0]['pointer']} if data_src else None)
                              or ({'kind': 'cms' if self.cms else 'computed', 'file': page.get('template'), 'line': 1}
                                  if page.get('template') else None),
                      'alt_edit': alt_edit, 'method': method,
                      **({k: match[k] for k in ('asset_file', 'size_bytes', 'intrinsic') if k in match} if match else {})}
            if record['natural'] and record['natural'][0] and record['rendered'] and record['rendered'][0]:
                record['oversized_ratio'] = round(record['natural'][0] / max(1, record['rendered'][0]), 2)
            images.append(record)
        values['images'] = images
        # Content
        source_blocks = {norm(b['text']): b for b in source['content']['blocks']}
        blocks = []
        for observed in summary.get('blocks', []):
            match = source_blocks.get(norm(observed['text']))
            edit = match['edit'] if match else None
            if edit is None:
                candidates = self.search(observed['text'], composition, page_files)
                edit = {k: v for k, v in candidates[0].items() if k not in {'tier', 'el'}} if len(candidates) == 1 else None
            blocks.append({'tag': observed['tag'], 'text': observed['text'], 'section': observed.get('section'),
                           'in_main': observed.get('in_main'), 'edit': edit, 'method': method})
        values['content'] = {'main_words': summary.get('main_word_count'), 'total_words': summary.get('word_count'),
                             'blocks': blocks[:400], 'attributed_blocks': sum(1 for b in blocks if b['edit'])}
        values['limitations'] = [l for l in values.get('limitations', []) if not l.startswith('Source lens:')]
        return values


def asset_key(src):
    """Comparable asset path: no origin, query or leading ./"""
    if not src:
        return None
    path = urlsplit(src).path if '://' in src else src.split('?')[0]
    return path.lstrip('./').lstrip('/').casefold()


def data_kind(record):
    suffix = record['file'].rsplit('.', 1)[-1].lower()
    if record.get('kind') == 'front-matter' or suffix in {'md', 'mdx', 'markdown'}:
        return 'markdown'
    return {'json': 'json', 'yml': 'yaml', 'yaml': 'yaml', 'toml': 'toml', 'po': 'i18n'}.get(suffix, 'json')


def link_kind(href, resolved, page, own_hosts=()):
    if href is None:
        return 'unresolved'
    if href.startswith(('mailto:', 'tel:', 'javascript:', 'sms:')):
        return 'action'
    if href.startswith('#'):
        return 'fragment'
    if re.match(r'^https?://', href, re.I) or href.startswith('//'):
        host = (urlsplit(href if '://' in href else 'https:' + href).hostname or '').lower()
        return 'internal' if host in own_hosts else 'absolute'
    return 'internal'
