"""Stage E: attribute every measured (rendered or raw) value to an edit location.

For each observed value the attributor tries, in order:
0. src hint        - build instrumentation stamped `data-src="file:line:col"` on the rendered node;
1. lens agreement  - the source lens already produced the same value for this field -> reuse its edit;
2. exact match     - a literal text/attribute in the element index (page files, then composition, then repo);
3. data match      - a JSON/YAML/front-matter string (edit.kind json|yaml|markdown + pointer);
4. i18n match      - a translation catalog value (edit.kind i18n + key);
5. template match  - the source value decorated by a title template / composed expression (computed + via);
6. otherwise       - the real expression location from the source lens (`computed`, verify=heuristic), or
                     `edit: null` with a gap. Never a `line: 1` placeholder.

Every edit carries `verify`: `grounded` (value seen at the cited span), `heuristic` (a plausible
expression/template location) or absent when there is no edit. Element-index extras
(end_line, end_col, in_loop, template_n, data_source, scope, shared, shared_by) are carried through.

Several equally good candidates -> the field keeps the best guess with low confidence
and a `candidates` list, and a gap item is queued for a small-model worker. The
model chooses a candidate ID; code fills file/line/col (doc §13 "constrain the answer").
"""

from __future__ import annotations

from collections import defaultdict
import copy
import re
from urllib.parse import urlsplit

from seo_agents.repo_analysis.site_model.source_lens import field

NON_COPY_ATTRS = {'id', 'class', 'classname', 'key', 'href', 'src', 'srcset', 'type', 'rel', 'role', 'for', 'htmlfor', 'style',
                  'target', 'name', 'variant', 'size', 'color', 'as', 'ref', 'slot', 'lang', 'loading', 'width', 'height', 'to'}
CMS_STACKS = {'shopify-theme', 'wordpress-theme', 'wordpress-block-theme', 'ghost-theme', 'drupal', 'magento'}


EDIT_EXTRAS = ('end_line', 'end_col', 'in_loop', 'template_n', 'data_source', 'scope', 'shared', 'shared_by')
LINK_TAGS = {'a', 'link', 'nuxtlink', 'nuxt-link', 'routerlink', 'router-link', 'navlink', 'anchor', 'navitem', 'button'}
LINK_ATTRS = {'href', 'to', 'url', 'link', 'path', 'xlink:href'}
HEAD_TAGS = {'head', 'title', 'meta', 'helmet', 'head-component', 'svelte:head', 'nuxt-head', 'seo', 'nextseo', 'metadata'}
SCRIPT_SUFFIXES = ('.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.vue', '.svelte', '.astro')
ATTR_ASSIGN = re.compile(r'\b(?:href|to|src)\s*=\s*\{?\s*$')


def element_edit(element, kind='literal', **extra):
    """Edit for an element-index record, carrying spans/scope extras when present."""
    edit = {'kind': kind, 'file': element['file'], 'line': element['line'], 'col': element.get('col', 0)}
    edit.update({k: element[k] for k in EDIT_EXTRAS if element.get(k) not in (None, '', [], False)})
    edit.update(extra)
    return edit


def parse_src_hint(hint):
    """`file:line[:col]` from data-src instrumentation -> (file, line, col) or None."""
    if not hint or not isinstance(hint, str):
        return None
    match = re.match(r'^(.+?):(\d+)(?::(\d+))?$', hint.strip())
    if not match:
        return None
    return match.group(1).lstrip('./'), int(match.group(2)), int(match.group(3)) if match.group(3) else None


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

    def search(self, value, composition, page_files, *, attr_names=None, element_filter=None, hint_file=None):
        """Exact candidates for an observed value, best tier first.

        element_filter: optional predicate restricting element candidates (e.g. links may only be
        attributed to href-bearing elements, never to a heading that happens to share the label).
        hint_file: file named by instrumentation; its candidates rank first.
        """
        key = norm(value)
        if not key or len(key) < 2:
            return []
        found = []
        for element in self.text.get(key, []):
            if element_filter is None or element_filter(element):
                found.append({**element_edit(element, 'literal'), 'el': element['id']})
        for element, name in self.attrs.get(key, []):
            if element_filter is not None and not element_filter(element):
                continue
            if (attr_names is None and name.lower() not in NON_COPY_ATTRS and not name.lower().startswith(('data-', 'on', 'aria-hidden'))) \
                    or (attr_names is not None and name in attr_names):
                found.append({**element_edit(element, 'prop' if element['kind'] == 'component' else 'literal'),
                              'el': element['id'], 'attr': name})
        for record in self.data.get(key, []):
            suffix = record['file'].rsplit('.', 1)[-1].lower()
            kind = 'markdown' if record['kind'] == 'front-matter' else 'json' if suffix == 'json' else 'yaml' if suffix in {'yml', 'yaml'} else 'toml' if suffix == 'toml' else 'json'
            found.append({'kind': kind, 'file': record['file'], 'line': record['line'], 'pointer': record['pointer']})
        for i18n_key, entry in self.i18n.get(key, []):
            found.append({'kind': 'i18n', 'file': entry['file'], 'line': entry['line'], 'i18n_key': i18n_key, 'pointer': entry.get('pointer')})

        def tier(candidate):
            if hint_file and candidate['file'] == hint_file:
                return -1
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
            data_part = {'kind': data_kind(records[0]), 'file': file, 'line': min(r['line'] for r in records if r.get('line')) if any(r.get('line') for r in records) else None,
                         'pointer': parent, 'composed_from': sorted(r['pointer'] for r in records), 'scope': 'data', 'verify': 'grounded'}
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
        return field(value, 'rendered', element_edit(element, 'computed', via='template with expressions; literal parts match',
                                                     verify='heuristic'), 0.55, composed=True)

    def call_sites(self, file, limit=3):
        """Where a component file is used (call-site chain for gap packets)."""
        sites = []
        for usage in self.ctx.usages.get(file, [])[:limit]:
            sites.append({'file': usage['file'], 'line': usage['line'], 'snippet': self.snippet(usage['file'], usage['line'])})
        return sites

    def in_head(self, element):
        current, hops = element, 0
        while current and hops < 12:
            if (current.get('tag') or '').casefold() in HEAD_TAGS:
                return True
            current, hops = self.ctx.by_id.get(current.get('parent') or ''), hops + 1
        return False

    def expression_candidates(self, composition, page_files, attr=None, limit=10, head=False):
        """Elements printing an expression in this page's files: candidates when no literal matched.
        Head fields only consider head elements (title/meta/Helmet/svelte:head...), never body text."""
        found = []
        for element, name, _fragments, _refs in self.templates:
            if element['file'] not in page_files and element['file'] not in composition:
                continue
            if head != self.in_head(element):
                continue
            if (attr is None and name is not None) or (attr is not None and name not in (attr if isinstance(attr, set) else {attr})):
                continue
            found.append({**element_edit(element, 'computed'), 'el': element['id'], 'tier': 0 if element['file'] in page_files else 1,
                          **({'attr': name} if name else {})})
        found.sort(key=lambda c: (c['tier'], c['file'], c['line']))
        return found[:limit]

    def unresolved(self, page, pointer, value, method, context=None, fallback=None):
        """No location matched: keep a real expression location (heuristic) or emit edit=None + gap."""
        if self.cms:
            edit = {'kind': 'cms', 'platform': self.ctx.fingerprint.get('primary'), 'verify': 'heuristic',
                    'via': 'served value has no literal source; managed in the platform admin'}
            if page.get('template'):
                edit['file'] = page['template']
            return field(value, method, edit, 0.35, lens_agreement=False)
        candidates = self.expression_candidates(self.current_composition, self.current_page_files, self.current_attr,
                                                head=pointer.startswith('/head/'))
        if len(candidates) == 1:
            chosen = {k: v for k, v in candidates[0].items() if k not in {'tier', 'el'}}
            chosen.update(verify='heuristic', via='only expression in the page files; literal value not in source')
            return field(value, method, chosen, 0.4, lens_agreement=False)
        if candidates:
            options = [{'id': 'c' + str(index + 1), **{k: v for k, v in c.items() if k != 'tier'}, 'snippet': self.snippet(c['file'], c['line'])}
                       for index, c in enumerate(candidates)]
            self.gaps.append({'task': 'attribute_value', 'page_id': page['id'], 'url': page['url'], 'field': pointer,
                              'observed': value, 'candidates': options, 'template': page.get('template'), **(context or {})})
            return field(value, method, None, 0.2, candidates=options, gap='no-literal-source', lens_agreement=False)
        return field(value, method, None, 0.0, gap='no-source-match', lens_agreement=False)

    def decide(self, page, pointer, value, candidates, fallback=None, method='rendered', context=None):
        """Pick an edit; queue a gap when the best tier is ambiguous. Never invents a line-1 location."""
        if not candidates:
            if value and (fallback is None or not fallback.get('edit') or fallback['edit'].get('kind') in {'computed', 'cms', 'add'}):
                composed = self.composed(value, self.current_composition, self.current_page_files,
                                         attr=self.current_attr)
                if composed is not None:
                    return composed | {'method': method}
            if fallback and fallback.get('edit') and fallback['edit'].get('kind') != 'add' and isinstance(fallback['edit'].get('line'), int) \
                    and fallback['edit']['line'] > 0:
                edit = dict(fallback['edit'])
                if edit.get('kind') == 'literal' and norm(fallback.get('value')) != norm(value):
                    edit['kind'] = 'cms' if self.cms else 'computed'
                    edit['via'] = 'source literal differs from served value: ' + str(fallback.get('value'))[:80]
                edit['verify'] = 'heuristic'
                return field(value, method, edit, 0.5, lens_agreement=False)
            if fallback and (fallback.get('edit') or {}).get('kind') == 'cms':
                return field(value, method, {**fallback['edit'], 'verify': 'heuristic'}, 0.4, lens_agreement=False)
            result = self.unresolved(page, pointer, value, method, context)
            if fallback and (fallback.get('edit') or {}).get('kind') == 'add':
                result['add_target'] = fallback['edit']  # where a NEW value would go; not where this one comes from
            return result
        best = [c for c in candidates if c['tier'] == candidates[0]['tier']]
        chosen = {k: v for k, v in best[0].items() if k not in {'tier', 'el'}}
        if len(best) == 1:
            chosen.setdefault('verify', 'grounded')
            return field(value, method, chosen, 0.9 if best[0]['tier'] <= 1 else 0.75, el=best[0].get('el'))
        options = []
        for index, c in enumerate(candidates):
            option = {'id': 'c' + str(index + 1), **{k: v for k, v in c.items() if k != 'tier'}, 'snippet': self.snippet(c['file'], c['line'])}
            sites = self.call_sites(c['file']) if c.get('kind') in {'literal', 'computed'} else []
            if sites:
                option['call_sites'] = sites
            options.append(option)
        self.gaps.append({'task': 'attribute_value', 'page_id': page['id'], 'url': page['url'], 'field': pointer,
                          'observed': value, 'candidates': options, 'template': page.get('template'), **(context or {})})
        chosen['verify'] = 'heuristic'
        return field(value, method, chosen, 0.45, candidates=options, gap='attribution-ambiguous')

    def hinted(self, hint, value, attr_names=None):
        """Edit from build instrumentation (`data-src`), when the hinted element really produces the value."""
        parsed = parse_src_hint(hint)
        if not parsed:
            return None, None
        file, line, col = parsed
        if file not in self.ctx.files:
            return None, None
        elements = [e for e in self.ctx.by_file.get(file, []) if e['line'] == line]
        if col is not None:
            elements.sort(key=lambda e: abs((e.get('col') or 0) - col))
        key = norm(value)
        for element in elements:
            if attr_names is None and element.get('text_literal') and norm(element['text_literal']) == key:
                return element_edit(element, 'literal', verify='grounded', via='data-src'), element['id']
            for name, attr in element['attrs'].items():
                if (attr_names is None or name in attr_names) and norm(attr.get('literal')) == key and key:
                    kind = 'prop' if element['kind'] == 'component' else 'literal'
                    return element_edit(element, kind, attr=name, verify='grounded', via='data-src'), element['id']
        return None, file  # element known, value not literal there: rank that file's candidates first

    # ------------------------------------------------------------ fields
    def is_link_element(self, element):
        """Link anchors may only come from href-bearing elements (or components rendering one)."""
        tag = (element.get('tag') or '').casefold()
        attrs = {name.casefold() for name in element.get('attrs', {})}
        if tag in LINK_TAGS or attrs & LINK_ATTRS:
            return True
        parent = self.ctx.by_id.get(element.get('parent') or '', {})
        return (parent.get('tag') or '').casefold() in LINK_TAGS or bool({n.casefold() for n in parent.get('attrs', {})} & LINK_ATTRS)

    def array_entry(self, href, anchor, files):
        """`{label: "About", href: "/about"}` entries in JS/TS/Vue/Svelte arrays rendered by a loop.

        The href literal and (when given) the label literal must sit within two lines of each other,
        so a heading or section that merely shares the label text never wins.
        """
        if not href:
            return []
        quoted = re.compile('([\'"`])' + re.escape(href) + r'\1')
        label = re.compile('([\'"`])' + re.escape(anchor.strip()) + r'\1', re.I) if anchor and anchor.strip() else None
        found = []
        for file in files:
            text = self.ctx.files.get(file)
            if not text or not file.endswith(SCRIPT_SUFFIXES):
                continue
            lines = text.splitlines()
            for number, line in enumerate(lines, 1):
                match = quoted.search(line)
                if not match or ATTR_ASSIGN.search(line[:match.start()]):  # `href="/x"` attribute: an element, not data
                    continue
                first = max(1, number - 2)
                window = lines[first - 1:number + 2]
                anchor_edit = None
                if label is not None:
                    hit = next((offset for offset, row in enumerate(window) if label.search(row)), None)
                    if hit is None:
                        continue
                    label_line = first + hit
                    anchor_edit = {'kind': 'literal', 'file': file, 'line': label_line,
                                   'col': label.search(lines[label_line - 1]).start(), 'scope': 'data', 'in_loop': True,
                                   'verify': 'grounded', 'via': 'array entry rendered in a loop'}
                edit = {'kind': 'literal', 'file': file, 'line': number, 'col': match.start(), 'end_col': match.end(),
                        'attr': 'href', 'scope': 'data', 'in_loop': True, 'verify': 'grounded', 'via': 'array entry rendered in a loop'}
                found.append({'href_edit': edit, 'anchor_edit': anchor_edit})
        return found

    def link_search(self, page, index, href, anchor, composition, page_files, context):
        """Edit locations for a rendered link: array entry by href+label, href-bearing literal, else gap."""
        ordered = sorted(composition | page_files, key=lambda f: (f not in page_files, f))
        entries = self.array_entry(href, anchor, ordered)
        if len(entries) == 1:
            return entries[0]['href_edit'], entries[0]['anchor_edit'] or entries[0]['href_edit'], 0.85
        by_href = self.search(href, composition, page_files, attr_names=LINK_ATTRS) if href else []
        by_href = [c for c in by_href if c.get('attr') or c['kind'] in {'json', 'yaml', 'toml', 'markdown', 'i18n'}]
        by_anchor = self.search(anchor, composition, page_files, element_filter=self.is_link_element) if anchor else []
        near = [c for c in by_href if any(a['file'] == c['file'] and abs(a['line'] - c['line']) <= 2 for a in by_anchor)]
        if len(entries) > 1 and not near:
            options = [{'id': 'c' + str(i + 1), **e['href_edit'], 'snippet': self.snippet(e['href_edit']['file'], e['href_edit']['line'])}
                       for i, e in enumerate(entries[:10])]
            self.gaps.append({'task': 'attribute_value', 'page_id': page['id'], 'url': page['url'], 'field': f'/links/out/{index}',
                              'observed': anchor or href, 'candidates': options, 'template': page.get('template'), **context})
            return {**entries[0]['href_edit'], 'verify': 'heuristic'}, entries[0]['anchor_edit'], 0.45
        candidates = near or by_href or by_anchor
        if candidates:
            decided = self.decide(page, f'/links/out/{index}', anchor or href, candidates, None, context=context)
            anchor_edit = decided['edit']
            if near or by_href:
                top = candidates[0]
                anchor_match = next((c for c in by_anchor if c['file'] == top['file'] and abs(c['line'] - top['line']) <= 2), None)
                anchor_edit = ({k: v for k, v in anchor_match.items() if k not in {'tier', 'el'}} | {'verify': 'grounded'}) if anchor_match else None
            return decided['edit'], anchor_edit, decided['confidence']
        decided = self.unresolved(page, f'/links/out/{index}', anchor or href, 'rendered', context)
        return decided['edit'], decided['edit'], decided['confidence']

    def attribute(self, page, source, summary, lens):
        values = copy.deepcopy(source)
        composition = set(source.get('composition', []))
        page_files = {page['template']} | {f for f in composition if f.startswith(page['template'].rsplit('/', 1)[0])} if page.get('template') else set()
        self.current_composition, self.current_page_files, self.current_attr = composition, page_files, None
        head = values['head']
        method = 'rendered' if lens == 'rendered' else 'raw-html'
        hints = summary.get('src_hints') or {}

        def head_value(key, observed, attr_names=None):
            current = head.get(key) or field()
            if observed is None:
                if current.get('value') is not None:
                    head[key] = field(None, method, current.get('edit'), 0.6, source_declared=current.get('value'),
                                      lens_agreement=False, note='declared in source but absent from the served page')
                else:
                    head[key] = field(None, method, current.get('edit'), current.get('confidence') or 0.6)
                return
            edit, hint_file = self.hinted(hints.get(key), observed, attr_names)
            if edit:
                head[key] = field(observed, method, edit, 0.98, lens_agreement=norm(current.get('value')) == norm(observed))
                return
            if norm(current.get('value')) == norm(observed) and current.get('edit'):
                head[key] = current | {'value': observed, 'method': method, 'confidence': 0.97, 'lens_agreement': True,
                                       'edit': {**current['edit'], 'verify': current['edit'].get('verify') or 'grounded'}}
                return
            via = current.get('via_template')
            if via and current.get('edit') and norm(observed).find(norm(current.get('value') or '').split('|')[0].strip()) >= 0:
                head[key] = current | {'value': observed, 'method': method, 'confidence': 0.85, 'lens_agreement': True}
                return
            candidates = self.search(observed, composition, page_files, attr_names=attr_names, hint_file=hint_file)
            self.current_attr = attr_names
            head[key] = self.decide(page, '/head/' + key, observed, candidates, current, method)
            self.current_attr = None
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
                    self.current_attr = {'content'}
                    merged[name] = self.decide(page, f'/head/{group}/{name}', observed,
                                               self.search(observed, composition, page_files, attr_names={'content'}), current, method)
                    self.current_attr = None
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
        # Headings: reuse source edits by text (DOM order), otherwise search.
        unused = list(source['headings'])
        headings = []
        observed_headings = summary.get('headings', [])
        for position, observed in enumerate(observed_headings):
            match = next((s for s in unused if s['level'] == observed['level'] and norm(s['text']) == norm(observed['text'])), None)
            if match:
                unused.remove(match)
                headings.append({**match, 'text': observed['text'], 'method': method, 'confidence': 0.95, 'lens_agreement': True,
                                 'path': observed.get('path'), 'visible': observed.get('visible')})
                continue
            context = {'dom_path': observed.get('path'), 'section': observed.get('section'),
                       'neighbours': [h.get('text') for h in observed_headings[max(0, position - 1):position + 2] if h is not observed][:2]}
            edit, hint_file = self.hinted(observed.get('src_hint'), observed['text'])
            if edit:
                decided = field(observed['text'], method, edit, 0.98)
            else:
                decided = self.decide(page, f'/headings/{len(headings)}', observed['text'],
                                      self.search(observed['text'], composition, page_files, hint_file=hint_file), None, method, context)
            headings.append({'level': observed['level'], 'text': observed['text'], 'el': decided.get('el'), 'method': method,
                             'edit': decided['edit'], 'confidence': decided['confidence'], 'section': observed.get('section'),
                             'path': observed.get('path'), 'visible': observed.get('visible'),
                             **({'candidates': decided['candidates']} if decided.get('candidates') else {}),
                             **({'gap': decided['gap']} if decided.get('gap') else {})})
        values['headings'] = headings
        values['source_only_headings'] = [{'level': s['level'], 'text': s['text'], 'edit': s['edit']} for s in unused][:30]
        # Links: href AND anchor AND DOM order; a heading that shares the label is never a link location.
        source_links = list(source['links']['out'])
        links = []
        observed_links = summary.get('links', [])
        for position, observed in enumerate(observed_links):
            href, anchor = observed.get('href'), observed.get('text') or ''
            record = {'href': href, 'resolved': observed.get('resolved'), 'anchor': anchor, 'aria_label': observed.get('aria_label'),
                      'rel': observed.get('rel'), 'target': observed.get('target'), 'section': observed.get('section'),
                      'in_main': observed.get('in_main'), 'kind': link_kind(href, observed.get('resolved'), page, self.ctx.own_hosts),
                      'visible': observed.get('visible'), 'img_alt': observed.get('img_alt') or None, 'method': method}
            if not href and observed.get('visible') is False:
                links.append({**record, 'edit': None, 'anchor_edit': None, 'confidence': 0.0, 'attributable': False,
                              'note': 'link without href that is not visibly rendered; not an edit target'})
                continue
            match = next((s for s in source_links if s.get('href') and norm(s.get('anchor')) == norm(anchor) and
                          path_of(s.get('href')) == path_of(href)), None) or \
                next((s for s in source_links if s.get('href') is None and anchor and norm(s.get('anchor')) == norm(anchor)), None) or \
                next((s for s in source_links if s.get('href') and path_of(s.get('href')) == path_of(href)
                      and (not anchor or not s.get('anchor') or norm(s.get('anchor')) == norm(anchor))), None)
            hint_edit = self.hinted(observed.get('src_hint'), href, LINK_ATTRS)[0] if href else None
            if hint_edit:
                edit, anchor_edit, confidence = hint_edit, hint_edit, 0.98
            elif match is not None and (match.get('edit') or {}).get('kind') not in {None, 'add'}:
                source_links.remove(match)
                edit, anchor_edit, confidence = match['edit'], match.get('anchor_edit') or match['edit'], 0.9
            else:
                if match is not None:
                    source_links.remove(match)
                context = {'section': observed.get('section'), 'dom_path': observed.get('path'), 'href': href,
                           'neighbours': [l.get('text') for l in observed_links[max(0, position - 1):position + 2] if l is not observed][:2]}
                edit, anchor_edit, confidence = self.link_search(page, len(links), href, anchor, composition, page_files, context)
            links.append({**record, 'edit': edit, 'anchor_edit': anchor_edit, 'confidence': confidence})
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
            edit = (match or {}).get('edit') or ({'kind': data_kind(data_src[0]), 'file': data_src[0]['file'],
                                                  'line': data_src[0]['line'], 'pointer': data_src[0]['pointer'],
                                                  'scope': 'data', 'verify': 'grounded'} if data_src else None)
            if edit is None and observed.get('src_hint'):
                hinted = parse_src_hint(observed['src_hint'])
                if hinted and hinted[0] in self.ctx.files:
                    edit = {'kind': 'computed', 'file': hinted[0], 'line': hinted[1], 'col': hinted[2] or 0,
                            'verify': 'heuristic', 'via': 'data-src'}
            if edit is None and self.cms:
                edit = {'kind': 'cms', 'platform': self.ctx.fingerprint.get('primary'), 'verify': 'heuristic',
                        **({'file': page['template']} if page.get('template') else {})}
            record = {'src': observed.get('src'), 'resolved': observed.get('resolved'), 'alt': observed.get('alt'),
                      'alt_missing': observed.get('alt') is None, 'width': observed.get('width'), 'height': observed.get('height'),
                      'loading': observed.get('loading'), 'fetchpriority': observed.get('fetchpriority'),
                      'srcset': bool(observed.get('srcset')), 'natural': observed.get('natural'), 'rendered': observed.get('rendered'),
                      'above_fold': observed.get('above_fold'), 'transfer_bytes': observed.get('transfer_bytes'),
                      'format': (name.rsplit('.', 1)[-1].lower() if '.' in name else None), 'section': observed.get('section'),
                      'edit': edit, 'alt_edit': alt_edit, 'method': method,
                      **({'gap': 'no-source-match'} if edit is None else {}),
                      **({k: match[k] for k in ('asset_file', 'size_bytes', 'intrinsic') if k in match} if match else {})}
            if record['natural'] and record['natural'][0] and record['rendered'] and record['rendered'][0]:
                record['oversized_ratio'] = round(record['natural'][0] / max(1, record['rendered'][0]), 2)
            images.append(record)
        values['images'] = images
        # Content: repeated blocks keep their own locations, assigned in DOM order.
        source_blocks = defaultdict(list)
        for block in source['content']['blocks']:
            source_blocks[norm(block['text'])].append(block)
        blocks = []
        for observed in summary.get('blocks', []):
            queue = source_blocks.get(norm(observed['text']))
            match = queue.pop(0) if queue else None
            edit = match['edit'] if match else None
            if edit is None:
                edit, hint_file = self.hinted(observed.get('src_hint'), observed['text'])
                if edit is None:
                    candidates = self.search(observed['text'], composition, page_files, hint_file=hint_file)
                    if len(candidates) == 1:
                        edit = {k: v for k, v in candidates[0].items() if k not in {'tier', 'el'}} | {'verify': 'grounded'}
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
    if record.get('edit_kind'):  # element-index `const` records (same-file JS constants) are literals
        return record['edit_kind']
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
