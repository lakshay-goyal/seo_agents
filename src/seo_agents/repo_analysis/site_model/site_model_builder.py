"""Page-centric SEO site model: one complete, edit-ready JSON model of any website.

`explore_site(repo_path, ...)` runs the deterministic orchestrator (doc §11):

  A acquire     snapshot once -> framework fingerprint -> unified element index
  B build/serve site_url | pre-built static HTML | opt-in build + serve | source-only
  C URL manifest adapter routes ∪ static params ∪ content collections ∪ sitemaps ∪ build output ∪ crawl
  D measure     raw HTML + rendered DOM (desktop/mobile) + Lighthouse + link checks (parallel, no LLM)
  E attribute   every rendered value -> file:line:col + edit kind; ambiguous -> gap queue
  F gap workers cheap-model sub-agents on small task packets (optional)
  G assemble    index.json (planning view) + site.json (site-level facts) + pages/<id>.json; validators

Output files (when `output_dir` is given): index.json, site.json, pages/<page_id>.json.
Every head/heading/link/image value carries `{value, method, edit, confidence}` where
`edit.kind` is literal | json | yaml | markdown | i18n | prop | computed | cms | add.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urljoin, urlsplit

from seo_agents.repo_analysis.site_model.source_lens import CHROME
from seo_agents.repo_analysis.site_model.url_manifest import normalize_path
from seo_agents.utils.env_settings import env_flag
from seo_agents.utils.step_logger import log, progress

SCHEMA = 'seo-site-model/1.0'
EXTRACTOR_VERSION = '1.0.0'
EDIT_KINDS = {'literal', 'json', 'yaml', 'toml', 'markdown', 'i18n', 'prop', 'computed', 'cms', 'add'}
METHODS = {'rendered', 'raw-html', 'source-literal', 'source-expression', 'source-i18n', 'front-matter', 'none', 'llm'}


# ---------------------------------------------------------------- stage D helpers
def same_site(url, base):
    if not url:
        return False
    a, b = urlsplit(url), urlsplit(base)
    return (a.hostname or '').lower() == (b.hostname or '').lower() and (a.port or None) == (b.port or None)


def best_summary(result):
    lenses = (result or {}).get('lenses', {})
    for name in ('desktop', 'mobile'):
        if lenses.get(name, {}).get('status') == 'success':
            return lenses[name]
    return (lenses.get('raw') or {}).get('summary')


# ---------------------------------------------------------------- stage G helpers
def simhash(text, bits=64):
    tokens = re.findall(r'\w+', (text or '').casefold())
    if len(tokens) < 8:
        return None
    vector = [0] * bits
    for gram in zip(tokens, tokens[1:], tokens[2:]):
        digest = int(hashlib.md5(' '.join(gram).encode()).hexdigest()[:16], 16)
        for bit in range(bits):
            vector[bit] += 1 if digest >> bit & 1 else -1
    return sum(1 << bit for bit in range(bits) if vector[bit] > 0)


def near_duplicates(pages, threshold=3):
    hashes = [(p['id'], p['url'], p['_simhash']) for p in pages if p.get('_simhash') is not None]
    clusters, used = [], set()
    for index, (pid, url, value) in enumerate(hashes):
        if pid in used:
            continue
        group = [(pid, url)]
        for other_id, other_url, other in hashes[index + 1:]:
            if other_id not in used and bin(value ^ other).count('1') <= threshold:
                group.append((other_id, other_url))
                used.add(other_id)
        if len(group) > 1:
            used.add(pid)
            clusters.append({'page_ids': [g[0] for g in group], 'urls': [g[1] for g in group],
                             'method': 'SimHash over word 3-grams of main content (Hamming <= %d of 64)' % threshold})
    return clusters


def hreflang_matrix(pages):
    """Reciprocity: every hreflang target must link back; x-default presence."""
    by_url = {}
    for page in pages:
        for entry in page['head'].get('hreflang') or []:
            by_url.setdefault(page['url'], []).append(entry)
    issues = []
    for url, entries in by_url.items():
        for entry in entries:
            target = normalize_path(entry.get('href')) if entry.get('href') else None
            if target is None or target == url:
                continue
            back = by_url.get(target)
            if back is None:
                issues.append({'page': url, 'target': entry.get('href'), 'issue': 'target page has no hreflang annotations (or is not in the model)'})
            elif not any(normalize_path(e.get('href') or '') == url for e in back):
                issues.append({'page': url, 'target': entry.get('href'), 'issue': 'no return link'})
        if not any((e.get('hreflang') or '').lower() == 'x-default' for e in entries):
            issues.append({'page': url, 'issue': 'no x-default'})
    return {'pages_with_hreflang': len(by_url), 'issues': issues[:200]}


VERIFY_STATES = ('grounded', 'heuristic', 'failed')
TITLE_SEPARATORS = re.compile(r'\s+[|\-\u2013\u2014\u00b7:\u2022]\s+')
PLACEHOLDER_VIA = re.compile(r'no source location matched|no literal source', re.I)
CATALOG_PATH = re.compile(r'(?:^|/)(?:locales?|i18n|lang|langs|translations?|messages|intl)(?:/|$)|\.(?:po|pot|xliff|xlf|arb)$', re.I)


def _window(lines, line, before=3, after=3):
    return norm(' '.join(lines[max(0, line - 1 - before):line + after]))


def _value_parts(value):
    """A served value and the page-specific parts a title template may have decorated."""
    text = norm(value)
    parts = [text[:60]]
    parts += [norm(p)[:60] for p in TITLE_SEPARATORS.split(str(value)) if len(p.strip()) >= 3]
    return [p for p in parts if p]


def _locate(lines, needle):
    """1-based line numbers whose text contains needle (normalised)."""
    return [i + 1 for i, text in enumerate(lines) if needle and needle in norm(text)]


def verify_edit(files, edit, value):
    """Classify one EDIT: ('grounded'|'heuristic'|'failed', reason|None).

    grounded  the value (or the insertion anchor) was found at/near the cited line;
    heuristic the location is plausible but cannot be value-checked (computed logic, CMS, catalog lookup);
    failed    the pointer is a placeholder (line 1 fallback, template call site) or the value is absent.
    May move an `add` edit from a line-1 placeholder to the real insertion point.
    """
    if not edit:
        return None, None
    kind = edit.get('kind')
    if kind not in EDIT_KINDS:
        return 'failed', 'unknown edit kind'
    file = edit.get('file')
    if file not in files:
        if kind == 'cms':
            return 'heuristic', None  # platform/admin data: nothing in the repo to check
        return 'failed', 'edit file not captured'
    lines = files[file].splitlines()
    line = edit.get('line')
    if kind == 'add':
        anchor = str(edit.get('anchor') or '')
        if isinstance(line, int) and 1 <= line <= len(lines) and anchor and anchor.lower() in lines[line - 1].lower():
            return 'grounded', None  # the source lens already pointed at the anchor itself
        if re.fullmatch(r'</?[A-Za-z][\w:-]*\s*/?>', anchor):  # tag anchor such as </head> or <main>
            tag_hits = [i + 1 for i, text in enumerate(lines) if anchor.lower().rstrip('>') in text.lower()]
            if tag_hits:
                edit['line'] = tag_hits[0]
                return 'grounded', None
        literal = re.sub(r'\s*\(.*$|\s*/.*$', '', anchor).strip()
        hits = _locate(lines, norm(literal)) if len(literal) >= 3 and not literal.startswith('<') else []
        if hits:
            edit['line'] = hits[0]
            return 'grounded', None
        head_close = [i + 1 for i, text in enumerate(lines) if '</head>' in text.lower()]
        if head_close:
            edit['line'] = head_close[0]
            edit.setdefault('insert', 'before </head>')
            return 'grounded', None
        return 'heuristic', 'insertion point not located in file; anchor is a recipe, not a location'
    if not isinstance(line, int) or not 1 <= line <= max(1, len(lines)):
        return 'failed', 'edit line out of range'
    if edit.get('composed_from'):
        return 'heuristic', None  # value is assembled from several data fields
    if kind in {'computed', 'cms'}:
        if PLACEHOLDER_VIA.search(str(edit.get('via') or '')):
            return 'failed', 'placeholder location: no source matched the served value'
        if line == 1 and value not in (None, '') and not any(p in _window(lines, 1, 0, 0) for p in _value_parts(value)) \
                and not edit.get('template') and not edit.get('pointer'):
            return 'failed', 'placeholder location (line 1) without evidence'
        return 'heuristic', None
    if value in (None, ''):
        return 'heuristic', None
    parts = _value_parts(value)
    window = _window(lines, line)
    if parts and parts[0] in window:
        return 'grounded', None
    if edit.get('via') and any(p in window for p in parts[1:]):
        return 'grounded', None  # title template decorates the page-specific part
    if edit.get('i18n_key'):
        catalogs = [f for f in files if CATALOG_PATH.search(f)]
        if any(parts[0] in norm(files[f]) for f in catalogs) or any(p in norm(files[file]) for p in parts):
            return 'heuristic', None
        return 'failed', 'translation value not found in any catalog'
    if kind == 'prop' and any(p in window for p in parts[1:]):
        return 'heuristic', None
    return 'failed', 'value not found near cited line'


def grounding_issue(files, edit, value):
    """Back-compatible: problem string when the edit fails verification, else None."""
    state, reason = verify_edit(files, dict(edit) if edit else edit, value)
    return reason if state == 'failed' else None


def norm(value):
    import html as _html
    return ' '.join(_html.unescape(str(value)).split()).casefold()


# ---------------------------------------------------------------- assembly
def merge_measured(page, source, measured, attribute):
    """Rendered values with source attribution (stage E). Falls back to source values."""
    summary = best_summary(measured)
    if summary is None:
        return source, None
    lens = 'raw-html' if summary is ((measured.get('lenses') or {}).get('raw') or {}).get('summary') else 'rendered'
    return attribute(page, source, summary, lens), lens


def explore_site(repo_path=None, *, site_url=None, render=None, build=None, run_gap_workers=None, output_dir=None,
                 snapshot=None, deterministic=None, measure=None, lighthouse=None, check_links=None,
                 max_pages=None, workers=None, timeout=30):
    """Explore one repository (any framework) and return {'index', 'site', 'pages'}.

    site_url  measure a deployed site (no build); render: Playwright desktop+mobile DOM;
    build     install+build+serve in the sandbox; measure: True/False forces the runtime lenses
              on/off; None (default) measures pre-built static HTML when present (SEO_MEASURE_PREBUILT=0 disables);
    run_gap_workers: cheap-model gap resolution (default: on when OPENROUTER_API_KEY is set).
    """
    from seo_agents.repo_analysis.site_model.site_model_workflow import SiteModelWorkflow
    render = env_flag('SEO_RENDER') if render is None else render
    build = env_flag('SEO_BUILD') if build is None else build
    site_url = site_url or os.getenv('SEO_SITE_URL') or None
    config = {
        'root': Path(repo_path or Path.cwd()).resolve(), 'site_url': site_url, 'render': render, 'build': build,
        # None = auto: measure when asked to, else when pre-built static HTML exists (serving files runs no repo code).
        'measure': True if (site_url or render or build) and measure is None else
                   (None if env_flag('SEO_MEASURE_PREBUILT', True) else False) if measure is None else measure,
        'max_pages': max_pages or int(os.getenv('SEO_MAX_PAGES', '200')),
        'workers': workers or int(os.getenv('SEO_MEASURE_WORKERS', '4')), 'workers_explicit': bool(workers), 'timeout': timeout,
        'gap_workers': run_gap_workers if run_gap_workers is not None else (
            env_flag('SEO_GAP_WORKERS', True) and bool(os.getenv('OPENROUTER_API_KEY'))),
        'lighthouse': lighthouse if lighthouse is not None else env_flag('SEO_LIGHTHOUSE', True),
        'check_links': check_links if check_links is not None else env_flag('SEO_CHECK_LINKS', True),
        'output_dir': output_dir, 'deterministic': deterministic, 'begun': datetime.now(timezone.utc),
    }
    return SiteModelWorkflow(config).run(snapshot=snapshot)


def empty_page():
    from seo_agents.repo_analysis.site_model.source_lens import field
    return {'composition': [], 'head': {'title': field(), 'description': field(), 'canonical': field(), 'robots': field(),
            'lang': field(), 'viewport': field(), 'og': {}, 'twitter': {}, 'hreflang': [], 'json_ld': [], 'keywords': field()},
            'headings': [], 'content': {'main_words': None, 'blocks': []}, 'links': {'out': [], 'in_count': None, 'click_depth': None},
            'images': [], 'limitations': ['No source template matched this URL; values come from measurement only.']}


def assemble_page(page, values, measured, lens, click_depth):
    rendering = (measured or {}).get('rendering') or {'raw_html_words': None, 'rendered_words': None, 'js_dependent': None}
    rendering['method'] = 'measured' if measured else 'source-only'
    raw = ((measured or {}).get('lenses') or {}).get('raw') or {}
    record = {
        'id': page['id'], 'url': page['url'], 'route': page['route'], 'template': page['template'], 'kind': page['kind'],
        'is_dynamic': page['is_dynamic'], 'instance_status': page['instance_status'], 'discovered_by': page['discovered_by'],
        'composition': values.get('composition', []), 'lens': lens or 'source',
        'rendering': rendering,
        'http': {k: raw.get(k) for k in ('status', 'http_status', 'final_url', 'redirect_chain', 'headers', 'request_to_headers_ms')} if raw else None,
        'head': values['head'], 'headings': values['headings'], 'content': values['content'],
        'links': {**values['links'], 'click_depth': click_depth}, 'images': values['images'],
        'structured_data': values['head'].get('json_ld', []),
        'mobile_parity': ((measured or {}).get('lenses') or {}).get('mobile_parity'),
        'landmarks': (best_summary(measured) or {}).get('landmarks') if measured else None,
        'performance': None, 'eeat': eeat_candidates(values),
        'keywords': keyword_profile(values),
        'limitations': values.get('limitations', []) + ([] if measured else
                       ['Source-only: values are what the source declares; runtime/CMS data and conditional rendering are not observed.']),
        'inputs_sha256': None,
    }
    if page.get('content_source'):
        record['content_source'] = page['content_source']
    if page['instance_status'] == 'template':
        record['limitations'].append('Route pattern: values describe the template, not a specific instance.')
    record['inputs_sha256'] = hashlib.sha256(json.dumps(record['composition']).encode()).hexdigest()[:16]
    return record


TRUST_LINK = re.compile(r'(?i)\b(about|contact|privacy|terms|imprint|impressum|legal|cookie|refund|shipping|returns|team|careers|press|editorial|faq)\b')


def eeat_candidates(values):
    """Deterministic E-E-A-T harvest (bylines, dates, trust links, citations); the LLM only classifies."""
    blocks = values['content']['blocks']
    signals = []
    byline = [b for b in blocks if re.match(r'(?i)^\s*(?:by|written by|author:?|posted by)\s+\S', b['text'] or '')]
    dates = [b for b in blocks if re.search(r'(?i)\b(?:published|updated|last updated|posted)\b.{0,30}\d{4}|\b\d{4}-\d{2}-\d{2}\b', b['text'] or '')]
    trust = [l for l in values['links']['out'] if TRUST_LINK.search((l.get('href') or '') + ' ' + (l.get('anchor') or ''))]
    citations = [l for l in values['links']['out'] if l.get('kind') == 'absolute' and l.get('in_main') and not l.get('in_chrome')]
    schema_types = {t for item in values['head'].get('json_ld', []) for t in item.get('types', [])}
    for name, rows, edit_key in (('authorship', byline, 'edit'), ('dates', dates, 'edit')):
        signals.append({'signal': name, 'status': 'observed' if rows else 'not_observed', 'method': 'deterministic',
                        'evidence': [{'text': r['text'][:200], 'edit': r[edit_key]} for r in rows[:5]]})
    signals.append({'signal': 'trust_pages', 'status': 'observed' if trust else 'not_observed', 'method': 'deterministic',
                    'evidence': [{'href': l['href'], 'anchor': l['anchor']} for l in trust[:10]]})
    signals.append({'signal': 'citations', 'status': 'observed' if citations else 'not_observed', 'method': 'deterministic',
                    'evidence': [{'href': l['href'], 'anchor': l['anchor']} for l in citations[:10]]})
    signals.append({'signal': 'structured_identity', 'status': 'observed' if schema_types & {'Organization', 'Person', 'LocalBusiness', 'Article', 'BlogPosting', 'Review', 'Product'} else 'not_observed',
                    'method': 'deterministic', 'evidence': sorted(schema_types)})
    return signals


def keyword_profile(values):
    text = ' '.join([values['head']['title'].get('value') or ''] + [h['text'] or '' for h in values['headings'] if h['level'] <= 2] +
                    [b['text'] for b in values['content']['blocks'][:40] if b.get('section') not in CHROME])
    words = [w for w in re.findall(r"[^\W\d_]{3,}", text.casefold()) if w not in STOPWORDS]
    grams = Counter(words) + Counter(' '.join(pair) for pair in zip(words, words[1:]))
    return {'top_terms': [term for term, _ in grams.most_common(12)], 'method': 'lexical uni/bi-grams over title, H1-H2 and main copy',
            'topic': None, 'intent': None}


STOPWORDS = set('the and for with your you are from that this our has have was were will can not all but get more about into out '
                'les des une pour dans avec sur der die das und mit für von'.split())


def collect_gaps(pages, files, seen=()):
    """Source-lens ambiguities (e.g. one component used with different props) not already queued."""
    gaps, seen = [], {(g['page_id'], g['field']) for g in seen}

    def options(candidates):
        rows = []
        for index, candidate in enumerate(candidates[:10]):
            lines = files.get(candidate.get('file'), '').splitlines()
            line = candidate.get('line') or 0
            rows.append({'id': candidate.get('id') or 'c' + str(index + 1), **{k: v for k, v in candidate.items() if k not in {'id', 'tier'}},
                         'snippet': candidate.get('snippet') or (lines[line - 1].strip()[:200] if 0 < line <= len(lines) else '')})
        return rows
    for page in pages:
        items = [('/head/' + key, page['head'][key], 'value') for key in ('title', 'description')]
        items += [(f'/headings/{i}', h, 'text') for i, h in enumerate(page['headings'])]
        for pointer, item, value_key in items:
            if item.get('candidates') and (page['id'], pointer) not in seen:
                item['candidates'] = options(item['candidates'])
                gaps.append({'task': 'attribute_value', 'page_id': page['id'], 'url': page['url'], 'template': page['template'],
                             'field': pointer, 'observed': item.get(value_key), 'candidates': item['candidates']})
    return gaps


def link_graph(pages):
    """In-degree from internal links across measured/source pages; orphan candidates."""
    incoming = Counter()
    for page in pages:
        targets = set()
        for link in page['links']['out']:
            if link.get('kind') != 'internal' or not link.get('href'):
                continue
            path = normalize_path(urljoin(page['url'], link['href']))
            if path and path != page['url']:
                targets.add(path)
        for target in targets:
            incoming[target] += 1
    for page in pages:
        page['links']['in_count'] = incoming.get(page['url'], 0) if page['instance_status'] != 'template' else None
        page['links']['orphan_candidate'] = page['instance_status'] != 'template' and page['url'] != '/' and incoming.get(page['url'], 0) == 0
    return incoming


def page_edits(page):
    """Every (pointer, item, value_key, edit_key) that carries an EDIT on one page record."""
    head = page['head']
    for key in ('title', 'description', 'canonical', 'robots', 'lang', 'viewport', 'keywords'):
        if isinstance(head.get(key), dict):
            yield f'/head/{key}', head[key], 'value', 'edit'
    for group in ('og', 'twitter'):
        for key, item in (head.get(group) or {}).items():
            if isinstance(item, dict):
                yield f'/head/{group}/{key}', item, 'value', 'edit'
    if head.get('add_target'):
        yield '/head/add_target', head, None, 'add_target'
    for index, entry in enumerate(head.get('hreflang') or []):
        yield f'/head/hreflang/{index}', entry, 'href', 'edit'
    for index, entry in enumerate(head.get('json_ld') or []):
        yield f'/head/json_ld/{index}', entry, None, 'edit'
    for index, heading in enumerate(page['headings']):
        yield f'/headings/{index}', heading, 'text', 'edit'
    for index, block in enumerate(page['content'].get('blocks') or []):
        yield f'/content/blocks/{index}', block, 'text', 'edit'
    for index, link in enumerate(page['links']['out']):
        yield f'/links/out/{index}', link, 'href', 'edit'
        yield f'/links/out/{index}/anchor', link, 'anchor', 'anchor_edit'
    for index, image in enumerate(page['images']):
        yield f'/images/{index}', image, 'src', 'edit'
        yield f'/images/{index}/alt', image, 'alt', 'alt_edit'


def performance_state(performance):
    """measured | failed | unavailable | gap, from one page's lighthouse/PSI result."""
    if not performance:
        return 'gap'
    status = performance.get('status')
    if status in {'unavailable', 'failed', 'partial'}:
        return status
    if status == 'success':
        return 'measured'
    states = [(performance.get(f) or {}).get('status') for f in ('mobile', 'desktop')]
    if any(s == 'success' for s in states):
        return 'measured' if all(s == 'success' for s in states) else 'partial'
    return 'failed'


def validate_model(pages, files):
    """Schema + grounding (every EDIT gets `verify`) + coverage ledger. Problems are recorded, never hidden."""
    issues = []
    ledger = {}
    verify_counts = Counter()
    for page in pages:
        for pointer, item, value_key, edit_key in page_edits(page):
            edit = item.get(edit_key)
            if not isinstance(edit, dict):
                continue
            if edit_key == 'edit' and item.get('method') and item['method'] not in METHODS:
                issues.append({'page': page['id'], 'pointer': pointer, 'issue': 'unknown method ' + str(item['method'])})
            value = item.get(value_key) if value_key else None
            if pointer.startswith('/links/out/') and edit_key == 'edit':
                # A link's edit points at the element: its href or its anchor text must be there.
                state, reason = verify_edit(files, dict(edit), value)
                if state == 'failed' and item.get('anchor'):
                    value = item['anchor']
            state, reason = verify_edit(files, edit, value)
            if state is None:
                continue
            edit['verify'] = state
            verify_counts[state] += 1
            if state == 'failed':
                issues.append({'page': page['id'], 'pointer': pointer, 'issue': reason, 'edit': {k: edit.get(k) for k in ('kind', 'file', 'line')}})
                if edit_key == 'edit' and value_key:
                    item['grounding'] = 'failed: ' + reason
                    item['confidence'] = min(item.get('confidence') or 0, 0.3)
        for index, heading in enumerate(page['headings']):
            if heading['level'] not in range(1, 7):
                issues.append({'page': page['id'], 'pointer': f'/headings/{index}', 'issue': 'invalid level'})
        measured = page['rendering']['method'] == 'measured'
        state = 'measured' if measured else 'source-only'
        ledger[page['id']] = {
            'head': state, 'headings': state, 'links': state, 'images': state,
            'performance': performance_state(page.get('performance')),
            'rendering': 'measured' if page['rendering'].get('js_dependent') is not None else 'gap',
            'attribution': 'complete' if all((h.get('edit') or {}).get('verify') == 'grounded' for h in page['headings'])
                           else 'partial',
        }
    return {'issues': issues[:500], 'issue_count': len(issues), 'coverage_ledger': ledger,
            'edit_verification': dict(verify_counts),
            'checks': ['schema (field shapes, enums)', 'grounding (value at/near cited line; placeholders rejected)',
                       'lens agreement (attribution.py)', 'coverage ledger (url x field state, failures kept)']}


SHELL_FILE = re.compile(r'(?:^|/)(?:index\.html?|app\.html|_document\.[jt]sx?|_app\.[jt]sx?|app\.vue|theme\.liquid|'
                        r'header\.php|footer\.php|baseof\.html|_layouts/default\.html|layouts/default\.(?:vue|html|njk|hbs)|'
                        r'default\.hbs|base\.html|layout\.(?:html|njk|liquid|pug|ejs)|_layout\.cshtml|application\.html\.erb|'
                        r'app\.blade\.php|\+layout\.svelte|layout\.[jt]sx?|root\.[jt]sx?|Layout\.astro|BaseLayout\.astro|Base\.astro)$')
DATA_KINDS = {'json', 'yaml', 'toml', 'markdown'}


def is_spa(fingerprint):
    fingerprint = fingerprint or {}
    return fingerprint.get('renderer') == 'csr' or str(fingerprint.get('primary') or '').endswith('-spa') \
        or fingerprint.get('primary') in {'angular', 'static-spa'}


def is_shell_file(file, pages, fingerprint=None):
    """A file whose output renders on every route: SPA index.html, root layout, theme layout, header partial."""
    if not file:
        return False
    real = [p for p in pages if p.get('instance_status') != 'template'] or pages
    composed = [set(p.get('composition') or []) for p in real]
    in_all = len(composed) > 1 and all(file in c for c in composed)
    own_template = sum(p.get('template') == file for p in real)
    if SHELL_FILE.search(file):
        if re.search(r'(?:^|/)(?:layout\.[jt]sx?|\+layout\.svelte)$', file) and \
                file.count('/') > (2 if file.startswith('src/') else 1):
            return False  # nested layouts (app/blog/layout.tsx) are layouts, not the shell
        if re.search(r'(?:^|/)index\.html?$', file):
            # Static sites: about/index.html is a page. Only the SPA entry document is the shell.
            return is_spa(fingerprint) and file.count('/') <= 1 or in_all and own_template < len(real)
        return own_template < len(real) or in_all
    return in_all and own_template < len(real)


def edit_key(edit):
    return (edit.get('file'), edit.get('line'), edit.get('pointer'), edit.get('i18n_key'))


def annotate_scope(pages, fingerprint=None):
    """scope + shared_by (+ template_n) on every EDIT, and head_scope per page.

    scope: shell (renders on every route) | layout (shared by >1 route) | template (one source line renders
    N instances: change once, affects all) | data (pointer into a data file: safe per instance) | literal.
    """
    fingerprint = fingerprint or {}
    sharing, per_page_count = {}, Counter()
    for page in pages:
        for _, item, _, key in page_edits(page):
            edit = item.get(key)
            if isinstance(edit, dict) and edit.get('file'):
                k = edit_key(edit)
                sharing.setdefault(k, set()).add(page['id'])
                if key in {'edit'}:
                    per_page_count[(page['id'], k)] += 1
    shell_cache = {}
    for page in pages:
        for pointer, item, _, key in page_edits(page):
            edit = item.get(key)
            if not isinstance(edit, dict) or not edit.get('file'):
                continue
            k = edit_key(edit)
            file = edit['file']
            if file not in shell_cache:
                shell_cache[file] = is_shell_file(file, pages, fingerprint)
            shared = sorted(sharing.get(k, {page['id']}) | {p for p in edit.get('used_by_pages') or [] if isinstance(p, str)})
            edit['shared_by'] = shared
            repeats = per_page_count[(page['id'], k)]
            if edit.get('kind') in DATA_KINDS and edit.get('pointer'):
                scope = 'data'
            elif shell_cache[file]:
                scope = 'shell'
            elif edit.get('in_loop') or edit.get('template_n') or repeats > 1:
                scope = 'template'
                edit['template_n'] = edit.get('template_n') or repeats
            elif (len(shared) > 1 or edit.get('shared')) and file != page.get('template'):
                scope = 'layout'
            else:
                scope = 'literal'
            edit['scope'] = scope
        page['head_scope'] = head_scope_for(page, pages, fingerprint)


def head_scope_for(page, pages, fingerprint):
    try:
        from seo_agents.repo_analysis.tools.routing.head_recipes import head_scope
        value = head_scope(page, fingerprint, pages)
        if value:
            return value
    except Exception:  # noqa: BLE001 - recipe module optional (owned elsewhere); fall back to the local rule
        pass
    title_edit = (page['head'].get('title') or {}).get('edit') or page['head'].get('add_target') or {}
    spa = is_spa(fingerprint)
    if title_edit.get('scope') == 'shell' and (spa or len(title_edit.get('shared_by') or []) > 1):
        return 'shared-shell'
    if title_edit.get('scope') in {'layout', 'template'} and len(title_edit.get('shared_by') or []) > 1:
        return 'shared-layout'
    return 'per-route'


def _host(url):
    return (urlsplit(url).hostname or '').lower()


def _origin(url):
    parts = urlsplit(url)
    return f'{parts.scheme}://{parts.netloc}'


def fetch_text(url, *, timeout=10, max_bytes=2_000_000, max_redirects=3):
    """Small same-origin text fetch (robots.txt, sitemaps). Never follows a redirect to another origin."""
    from urllib.error import HTTPError, URLError
    from urllib.request import HTTPRedirectHandler, Request, build_opener

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = build_opener(NoRedirect())
    chain = []
    for _ in range(max_redirects + 1):
        try:
            response = opener.open(Request(url, headers={'User-Agent': 'seo-agents-probe/1.0'}), timeout=timeout)
        except HTTPError as error:
            response = error
        except (URLError, TimeoutError, OSError, ValueError) as error:
            return {'status': None, 'error': type(error).__name__, 'url': url, 'redirect_chain': chain}
        with response:
            status = response.code
            location = response.headers.get('location')
            if status in {301, 302, 303, 307, 308} and location:
                target = urljoin(url, location)
                chain.append({'url': url, 'http_status': status, 'target': target})
                if _origin(target) != _origin(url):
                    return {'status': status, 'url': url, 'redirect_chain': chain, 'final_url': target, 'out_of_scope': True}
                url = target
                continue
            body = response.read(max_bytes) if hasattr(response, 'read') else b''
            if url.endswith('.gz') or body[:2] == b'\x1f\x8b':
                import gzip
                try:
                    body = gzip.decompress(body)
                except OSError:
                    pass
            return {'status': status, 'url': url, 'final_url': url, 'redirect_chain': chain,
                    'content_type': (response.headers.get('content-type') or '').lower(),
                    'body': body.decode('utf-8', errors='replace')}
    return {'status': None, 'error': 'redirect-limit', 'url': url, 'redirect_chain': chain}


def resolve_canonical_host(base_url, fetch=None, hops=4):
    """Follow apex->www and http->https (cross-origin) redirects from the home page first; served behaviour
    (robots, sitemap, soft 404, headers, trailing slash) is then measured on the final host, not on a host
    that only redirects."""
    from seo_agents.repo_analysis.tools.http.runtime_observation import fetch_baseline
    fetch = fetch or fetch_baseline
    origin, chain = base_url.rstrip('/'), []
    for _ in range(hops):
        response = fetch(origin + '/', timeout=12)
        chain += [{'url': r.get('url'), 'http_status': r.get('http_status'), 'target': r.get('target') or r.get('location')}
                  for r in response.get('redirect_chain') or []]
        final = response.get('final_url')
        if response.get('status') == 'redirect-out-of-scope' and final and _origin(final) != origin:
            origin = _origin(final)
            continue
        break
    hops_cross = [c for c in chain if c.get('target') and c.get('url') and _host(c['target']) != _host(c['url'])]
    scheme = [c for c in chain if c.get('target') and c.get('url') and urlsplit(c['url']).scheme == 'http'
              and urlsplit(c['target']).scheme == 'https']
    return {'requested': base_url.rstrip('/'), 'final_origin': origin, 'final_host': _host(origin), 'redirect_chain': chain,
            'host_redirect': {'from': _host(hops_cross[0]['url']), 'to': _host(hops_cross[0]['target']),
                              'status': hops_cross[0]['http_status'],
                              'permanent': hops_cross[0]['http_status'] in {301, 308}} if hops_cross else None,
            'scheme_redirect': {'status': scheme[0]['http_status'], 'permanent': scheme[0]['http_status'] in {301, 308}} if scheme else None,
            'method': 'raw@' + _host(base_url)}


def looks_like_html(response):
    body = (response.get('body') or '').lstrip()[:200].lower()
    return 'text/html' in (response.get('content_type') or '') or body.startswith(('<!doctype html', '<html'))


def served_file_state(response, kind):
    """present | missing | spa-shell-fallback | redirect | error for robots.txt / sitemap.xml."""
    status = response.get('status')
    if status is None:
        return 'error'
    if status in {404, 410}:
        return 'missing'
    if status in {301, 302, 303, 307, 308}:
        return 'redirect'
    if status != 200:
        return 'error'
    if looks_like_html(response):
        return 'spa-shell-fallback'  # catch-all rewrite serves index.html: the file does not exist
    if kind == 'sitemap' and not re.search(r'<(?:urlset|sitemapindex)\b', response.get('body') or ''):
        return 'invalid'
    return 'present'


def live_sitemap_urls(base_url, limit=5000, own_hosts=(), fetch=None):
    """Sitemap URLs from robots.txt `Sitemap:` lines and /sitemap.xml on the canonical host.
    Only same-site sitemaps are fetched (SSRF guard); each sitemap is fetched once."""
    from seo_agents.repo_analysis.tools.parsing.safe_xml_parser import safe_xml_fromstring
    fetch = fetch or fetch_text
    canonical = resolve_canonical_host(base_url)['final_origin'] if base_url.startswith('http') else base_url
    allowed = {_host(base_url), _host(canonical)} | {h.lower() for h in own_hosts or ()}
    robots = fetch(canonical + '/robots.txt', timeout=10)
    queue = [canonical + '/sitemap.xml']
    if served_file_state(robots, 'robots') == 'present':
        queue += [m.group(1) for m in re.finditer(r'(?im)^\s*sitemap:\s*(\S+)', robots.get('body') or '')]
    urls, seen = [], set()
    while queue and len(urls) < limit and len(seen) < 50:
        url = queue.pop(0)
        if url in seen or _host(url) not in allowed or urlsplit(url).scheme not in {'http', 'https'}:
            continue
        seen.add(url)
        response = fetch(url, timeout=10, max_bytes=10_000_000)
        if served_file_state(response, 'sitemap') != 'present':
            continue
        try:
            root = safe_xml_fromstring(response['body'].encode())
        except Exception:  # noqa: BLE001 - invalid sitemap is a gap, not a crash
            continue
        index = root.tag.rsplit('}', 1)[-1] == 'sitemapindex'
        for element in root.iter():
            if element.tag.rsplit('}', 1)[-1] == 'loc' and element.text:
                loc = element.text.strip()
                if index:
                    queue.append(loc)
                elif _host(loc) in allowed:
                    urls.append((loc, 'sitemap:live'))
    return urls[:limit]


def run_lighthouse(base_url, pages):
    from seo_agents.repo_analysis.measurement.lighthouse_audit import measure_performance, performance_available
    if not performance_available(base_url):
        return {}
    limit = int(os.getenv('SEO_LIGHTHOUSE_MAX_PAGES', '12'))
    # One representative per template first (home, then each template's first instance).
    chosen, templates = [], set()
    for page in sorted(pages, key=lambda p: (p['url'] != '/', p['url'])):
        if page['instance_status'] == 'template' or page['rendering']['method'] != 'measured':
            continue
        if page['template'] not in templates or len(chosen) < limit // 2:
            chosen.append(page)
            templates.add(page['template'])
        if len(chosen) >= limit:
            break
    urls = {page['id']: urljoin(base_url + '/', page['url'].lstrip('/')) for page in chosen}
    with progress(f'site model: lighthouse on {len(chosen)} representative URLs'):
        try:
            from seo_agents.repo_analysis.measurement.lighthouse_audit import measure_many
            by_url = measure_many(list(urls.values()))
            return {pid: by_url.get(url) for pid, url in urls.items() if by_url.get(url)}
        except ImportError:
            return {pid: measure_performance(url) for pid, url in urls.items()}


def run_link_checks(base_url, pages, measured):
    from seo_agents.repo_analysis.measurement.link_checker import check_links
    internal, external = set(), set()
    for page in pages:
        for link in page['links']['out']:
            resolved = link.get('resolved') or (urljoin(base_url + page['url'], link['href']) if link.get('href') else None)
            if not resolved:
                continue
            link['resolved'] = resolved
            (internal if same_site(resolved, base_url) else external).add(resolved)
    with progress(f'site model: link checks ({len(internal)} internal, sample of {len(external)} external)'):
        return check_links(internal, external, external_sample=int(os.getenv('SEO_EXTERNAL_LINK_SAMPLE', '50')))


def probe_site(base_url, sample_paths=(), *, fetch=None, text_fetch=None):
    """Served behaviour measured on the canonical host: host/scheme redirects, robots.txt, sitemap.xml,
    soft 404, trailing slash (on a real non-root route), http->https and security headers.
    Each fact carries {value, method: raw@<host>, status: measured|failed|not-run}."""
    from seo_agents.repo_analysis.tools.http.runtime_observation import fetch_baseline
    fetch = fetch or fetch_baseline
    text_fetch = text_fetch or fetch_text
    host = resolve_canonical_host(base_url, fetch)
    final = host['final_origin']
    method = 'raw@' + host['final_host']

    def fact(value, status='measured', **extra):
        return {'value': value, 'method': method, 'status': status, **extra}
    probes = {'canonical_host': fact(final, host=host['final_host'], requested=host['requested'],
                                     redirect_chain=host['redirect_chain'], host_redirect=host['host_redirect'],
                                     scheme_redirect=host['scheme_redirect'])}
    home = fetch(final + '/', timeout=12)
    probes['home_headers'] = home.get('headers')
    robots = text_fetch(final + '/robots.txt', timeout=10)
    state = served_file_state(robots, 'robots')
    probes['robots_txt'] = fact(state, 'measured' if robots.get('status') is not None else 'failed',
                                http_status=robots.get('status'), content_type=robots.get('content_type'),
                                body=(robots.get('body') or '')[:5000] if state == 'present' else None)
    probes['robots_txt']['status_code'] = robots.get('status')
    sitemap = text_fetch(final + '/sitemap.xml', timeout=10)
    probes['sitemap_xml'] = fact(served_file_state(sitemap, 'sitemap'), 'measured' if sitemap.get('status') is not None else 'failed',
                                 http_status=sitemap.get('status'), content_type=sitemap.get('content_type'))
    missing = fetch(final + '/seo-agents-probe-' + hashlib.sha1(base_url.encode()).hexdigest()[:8], timeout=10)
    same_as_home = bool(missing.get('initial_html')) and missing.get('initial_html') == home.get('initial_html')
    soft = missing.get('http_status') == 200
    probes['soft_404'] = fact(soft, 'measured' if missing.get('http_status') else 'failed', http_status=missing.get('http_status'),
                              soft_404=soft, same_body_as_home=same_as_home,
                              kind=('spa-catch-all' if same_as_home else 'soft-404') if soft else None)
    sample = next((p for p in sample_paths if p and p != '/' and not PLACEHOLDER_SEGMENT.search(p)), None)
    if sample:
        sample = '/' + sample.strip('/')
        slash = fetch(final + sample + '/', timeout=10)
        plain = fetch(final + sample, timeout=10)
        a, b = slash.get('http_status'), plain.get('http_status')
        a_redirect, b_redirect = bool(slash.get('redirect_chain')), bool(plain.get('redirect_chain'))
        policy = ('redirect-to-no-slash' if a_redirect and not b_redirect else 'redirect-to-slash' if b_redirect and not a_redirect
                  else 'duplicate (both 200)' if a == 200 and b == 200 else 'inconsistent')
        probes['trailing_slash'] = fact(policy, sample=sample, with_slash=a, without_slash=b,
                                        redirect_statuses=[r.get('http_status') for r in (slash.get('redirect_chain') or []) + (plain.get('redirect_chain') or [])],
                                        redirects=[r.get('location') for r in (slash.get('redirect_chain') or []) + (plain.get('redirect_chain') or [])])
    else:
        probes['trailing_slash'] = fact(None, 'not-run', reason='no concrete non-root route to probe')
    if final.startswith('https://'):
        insecure = fetch('http://' + final[len('https://'):] + '/', timeout=10)
        chain = insecure.get('redirect_chain') or []
        first = chain[0].get('http_status') if chain else None
        probes['http_to_https'] = fact(bool(chain) and str(chain[-1].get('target') or '').startswith('https://') or
                                       str(insecure.get('final_url') or '').startswith('https://'),
                                       status='failed' if insecure.get('status') in {None, 'failed', 'timeout', 'network-error'} else 'measured',
                                       http_status=first,
                                       permanent=first in {301, 308} if first else None, redirect_chain=chain)
    headers = probes['home_headers'] or {}
    probes['security_headers'] = {name: headers.get(name) for name in ('strict-transport-security', 'content-security-policy', 'x-frame-options',
                                                                       'x-content-type-options', 'referrer-policy', 'permissions-policy')}
    probes['hsts'] = fact(headers.get('strict-transport-security'), 'measured' if home.get('http_status') else 'failed')
    probes['x_robots_tag'] = fact(headers.get('x-robots-tag'), 'measured' if home.get('http_status') else 'failed')
    return probes


PLACEHOLDER_SEGMENT = re.compile(r'[\[\]{}<>*:]')


DETERMINISTIC_KEYS = ('robots_files', 'sitemap_files', 'redirects', 'redirect_rules', 'project_modes', 'route_modes',
                      'deployment_config', 'hosting', 'csr_only', 'initial_html_contains_content', 'large_assets', 'same_as_urls',
                      'social_profiles', 'interpretation')
HOSTING_FILES = [('vercel', re.compile(r'(?:^|/)vercel\.json$')), ('netlify', re.compile(r'(?:^|/)(?:netlify\.toml|_redirects)$')),
                 ('cloudflare', re.compile(r'(?:^|/)(?:wrangler\.toml|wrangler\.jsonc?)$')), ('firebase', re.compile(r'(?:^|/)firebase\.json$')),
                 ('github-pages', re.compile(r'(?:^|/)CNAME$|\.github/workflows/.*pages')), ('apache', re.compile(r'(?:^|/)\.htaccess$')),
                 ('nginx', re.compile(r'(?:^|/)nginx[^/]*\.conf$|(?:^|/)conf\.d/')), ('amplify', re.compile(r'(?:^|/)amplify\.ya?ml$')),
                 ('render', re.compile(r'(?:^|/)render\.ya?ml$')), ('fly', re.compile(r'(?:^|/)fly\.toml$')),
                 ('shopify', re.compile(r'(?:^|/)layout/theme\.liquid$')), ('wordpress', re.compile(r'(?:^|/)style\.css$'))]
HOSTING_HEADERS = [('vercel', re.compile(r'vercel', re.I)), ('netlify', re.compile(r'netlify', re.I)),
                   ('cloudflare', re.compile(r'cloudflare', re.I)), ('github-pages', re.compile(r'github\.com', re.I)),
                   ('cloudfront', re.compile(r'cloudfront|AmazonS3', re.I)), ('nginx', re.compile(r'nginx', re.I))]


def detect_hosting(files, probes=None, deterministic=None):
    """Hosting is a first-class fact: many fixes (redirect status, soft 404, headers) live there, not in code."""
    config = [{'host': name, 'file': path} for path in sorted(files) for name, pattern in HOSTING_FILES[:10] if pattern.search(path)]
    headers = (probes or {}).get('home_headers') or {}
    served_by = next((name for name, pattern in HOSTING_HEADERS
                      if pattern.search(' '.join(str(headers.get(k) or '') for k in ('server', 'x-cache', 'via')))), None)
    deployment = ((deterministic or {}).get('code_exploration') or {}).get('deployment_config')
    primary = served_by or (config[0]['host'] if config else None)
    return {'value': primary, 'served_by': served_by, 'config_files': config[:10], 'deployment_config': deployment,
            'method': 'response headers' if served_by else 'config files' if config else 'none'}


def link_state(result):
    """ok | broken | blocked | error. Bot walls (LinkedIn 999, 401/403/429) are blocked, never broken."""
    if result.get('state'):
        return result['state']
    status = result.get('status') or 0
    if status in {401, 403, 429, 999}:
        return 'blocked'
    if status in {404, 410} or status >= 500:
        return 'broken'
    return 'ok' if status else 'error'


def site_facts(snapshot, fingerprint, index, manifest_notes, served, probes, deterministic, pages, duplicates, link_health,
               gap_report, validation, limitations, begun):
    from seo_agents.llm.usage_tracking import usage_summary
    deterministic = deterministic or {}
    summary = {}
    for name in ('sitemap_robots', 'canonicals_redirects', 'https_security_headers', 'structured_data', 'hreflang_i18n',
                 'performance_web_vitals', 'brand_entity_signals', 'local_business_presence', 'ai_search_readiness', 'mobile_parity',
                 'rendering_strategy', 'code_exploration'):
        result = deterministic.get(name)
        if isinstance(result, dict):
            summary[name] = {'found_signals': result.get('found_signals'), 'gaps': [g.get('id') for g in result.get('gaps', [])],
                             **{k: result[k] for k in DETERMINISTIC_KEYS if k in result}}
            if 'redirect_rules' in result and 'redirects' not in result:
                summary[name]['redirects'] = result['redirect_rules']  # one name for consumers; the agent emits redirect_rules
    depth = [p['links']['click_depth'] for p in pages if p['links']['click_depth'] is not None]
    return {
        'schema': SCHEMA, 'framework': fingerprint, 'snapshot_sha256': snapshot['sha256'],
        'generated_at': begun.isoformat(), 'duration_seconds': round((datetime.now(timezone.utc) - begun).total_seconds(), 2),
        'serve': served.describe() if served else {'mode': 'source-only', 'limitations': ['Measurement disabled (pass site_url, render=True or build=True).']},
        'url_manifest': manifest_notes,
        'i18n': {'catalog_keys': len(index['i18n']), 'default_locale': next(iter(sorted({e.get('locale') for v in index['i18n'].values() for e in v if e.get('locale')})), None)},
        'served': probes,
        'canonical_host': (probes.get('canonical_host') or {}).get('value') if probes else None,
        'hosting': detect_hosting(snapshot['files'], probes, deterministic),
        'deterministic_signals': summary,
        'hreflang': hreflang_matrix(pages),
        'duplicates': duplicates,
        'link_health': link_health.get('coverage') if link_health else None,
        'broken_links': sorted({url for url, r in (link_health or {}).get('results', {}).items() if link_state(r) == 'broken'})[:200],
        'blocked_links': sorted({url for url, r in (link_health or {}).get('results', {}).items() if link_state(r) == 'blocked'})[:200],
        'redirected_links': sorted({url for url, r in (link_health or {}).get('results', {}).items()
                                    if link_state(r) == 'ok' and r.get('redirects')})[:200],
        'click_depth': {'max': max(depth) if depth else None, 'pages_deeper_than_3': sum(d > 3 for d in depth)},
        'orphan_candidates': [p['url'] for p in pages if p['links'].get('orphan_candidate')][:200],
        'gap_workers': gap_report,
        'validation': {k: validation[k] for k in ('issue_count', 'issues', 'checks', 'edit_verification') if k in validation},
        'coverage': {'urls_total': len(pages), 'templates_only': sum(p['instance_status'] == 'template' for p in pages),
                     'measured': sum(p['rendering']['method'] == 'measured' for p in pages),
                     'source_only': sum(p['rendering']['method'] != 'measured' for p in pages),
                     'ledger': validation['coverage_ledger'],
                     'element_index': {'elements': len(index['elements']), 'data_strings': len(index['data']),
                                       'files': Counter(v['status'] for v in index['files'].values())}},
        'model_usage': usage_summary(),
        'limitations': sorted(set(limitations)) + index['limitations'],
    }


def planning_index(snapshot, fingerprint, pages, site):
    rows = []
    for page in pages:
        head = page['head']
        issues = sum([head['title']['value'] is None, head['description']['value'] is None,
                      sum(h['level'] == 1 for h in page['headings']) != 1,
                      any(i.get('alt') is None for i in page['images'])])
        rows.append({'id': page['id'], 'url': page['url'], 'file': 'pages/' + page['id'] + '.json', 'template': page['template'],
                     'status': (page.get('http') or {}).get('http_status'), 'title': head['title']['value'],
                     'title_edit': (head['title'].get('edit') or {}).get('kind'),
                     'lcp_ms': (((page.get('performance') or {}).get('mobile') or {}).get('metrics') or {}).get('lcp_ms'),
                     'instance_status': page['instance_status'], 'issues_hint': issues})
    return {'schema': SCHEMA, 'repo': {'snapshot_sha256': snapshot['sha256'],
            'framework': {'name': fingerprint['primary'], 'version': fingerprint['version'], 'router': fingerprint['router']}},
            'coverage': {k: site['coverage'][k] for k in ('urls_total', 'templates_only', 'measured', 'source_only')} |
                        {'gaps_open': site['gap_workers']['total'] - site['gap_workers'].get('resolved', 0),
                         'validation_issues': site['validation']['issue_count']},
            'how_to_use': ('Plan from this file; load site.json for site-wide facts and pages/<id>.json for one URL. '
                           'Every value has {value, method, edit, confidence}; edit.kind tells how to change it '
                           '(literal|json|yaml|markdown|i18n|prop|computed|cms|add) and edit.file/line where.'),
            'pages': rows}


def write_model(model, output_dir):
    directory = Path(output_dir)
    (directory / 'pages').mkdir(parents=True, exist_ok=True)
    for stale in (directory / 'pages').glob('*.json'):
        if stale.stem not in model['pages']:
            stale.unlink()
    for pid, page in model['pages'].items():
        (directory / 'pages' / (pid + '.json')).write_text(json.dumps(page, indent=2, ensure_ascii=False, default=str) + '\n')
    (directory / 'site.json').write_text(json.dumps(model['site'], indent=2, ensure_ascii=False, default=str) + '\n')
    (directory / 'index.json').write_text(json.dumps(model['index'], indent=2, ensure_ascii=False, default=str) + '\n')
    log('site model written: ' + str(directory) + ' (' + str(len(model['pages'])) + ' pages)')
    return directory


class SiteModelBuilder:
    """Tool: build the page-centric site model (runs the site-model LangGraph; see site_model_workflow.py)."""

    name = 'site_model_builder'

    def build(self, repo_path=None, **options):
        """{'index', 'site', 'pages'}; options are those of `explore_site` (site_url, render, build, output_dir, ...)."""
        return explore_site(repo_path, **options)
