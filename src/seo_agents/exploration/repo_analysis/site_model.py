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

from seo_agents.exploration.repo_analysis.source_lens import CHROME
from seo_agents.exploration.repo_analysis.url_manifest import normalize_path
from seo_agents.log import log, progress

SCHEMA = 'seo-site-model/1.0'
EXTRACTOR_VERSION = '1.0.0'
EDIT_KINDS = {'literal', 'json', 'yaml', 'toml', 'markdown', 'i18n', 'prop', 'computed', 'cms', 'add'}
METHODS = {'rendered', 'raw-html', 'source-literal', 'source-expression', 'source-i18n', 'front-matter', 'none', 'llm'}


def env_flag(name, default=False):
    value = os.getenv(name)
    return default if value is None else value not in {'0', '', 'false', 'False'}


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


def grounding_issue(files, edit, value):
    """Edit must point into a captured file; literal values must appear near the cited line."""
    if not edit:
        return None
    if edit.get('kind') not in EDIT_KINDS:
        return 'unknown edit kind'
    if edit.get('composed_from'):
        return None if edit.get('file') in files else 'edit file not captured'  # value is assembled from several data fields
    if edit['kind'] in {'cms', 'add', 'computed'}:
        return None if edit.get('file') in files or edit['kind'] == 'cms' else 'edit file not captured'
    file = edit.get('file')
    if file not in files:
        return 'edit file not captured'
    lines = files[file].splitlines()
    line = edit.get('line')
    if not isinstance(line, int) or not 1 <= line <= max(1, len(lines)):
        return 'edit line out of range'
    if value in (None, '') or edit['kind'] == 'prop' and value is None:
        return None
    needle = norm(value)[:60]
    window = norm(' '.join(lines[max(0, line - 4):line + 3]))
    if needle and needle not in window:
        # Values decorated by a template (title templates) only need their page-specific part.
        return None if edit.get('via') or edit.get('i18n_key') else 'value not found near cited line'
    return None


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
    from seo_agents.exploration.repo_analysis.site_graph import run_site_graph
    render = env_flag('SEO_RENDER') if render is None else render
    build = env_flag('SEO_BUILD') if build is None else build
    site_url = site_url or os.getenv('SEO_SITE_URL') or None
    config = {
        'root': Path(repo_path or Path.cwd()).resolve(), 'site_url': site_url, 'render': render, 'build': build,
        # None = auto: measure when asked to, else when pre-built static HTML exists (serving files runs no repo code).
        'measure': True if (site_url or render or build) and measure is None else
                   (None if env_flag('SEO_MEASURE_PREBUILT', True) else False) if measure is None else measure,
        'max_pages': max_pages or int(os.getenv('SEO_MAX_PAGES', '200')),
        'workers': workers or int(os.getenv('SEO_MEASURE_WORKERS', '4')), 'timeout': timeout,
        'gap_workers': run_gap_workers if run_gap_workers is not None else (
            env_flag('SEO_GAP_WORKERS', True) and bool(os.getenv('OPENROUTER_API_KEY'))),
        'lighthouse': lighthouse if lighthouse is not None else env_flag('SEO_LIGHTHOUSE', True),
        'check_links': check_links if check_links is not None else env_flag('SEO_CHECK_LINKS', True),
        'output_dir': output_dir, 'deterministic': deterministic, 'begun': datetime.now(timezone.utc),
    }
    return run_site_graph(config, snapshot=snapshot)


def empty_page():
    from seo_agents.exploration.repo_analysis.source_lens import field
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
    citations = [l for l in values['links']['out'] if l.get('kind') == 'absolute' and l.get('in_main')]
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


def validate_model(pages, files):
    """Schema + grounding + lens agreement + coverage ledger. Problems are recorded, never hidden."""
    issues = []
    ledger = {}

    def check(page, pointer, item, value_key='value'):
        edit = item.get('edit')
        if item.get('method') and item['method'] not in METHODS:
            issues.append({'page': page['id'], 'pointer': pointer, 'issue': 'unknown method ' + str(item['method'])})
        problem = grounding_issue(files, edit, item.get(value_key))
        if problem:
            issues.append({'page': page['id'], 'pointer': pointer, 'issue': problem, 'edit': edit})
            item['grounding'] = 'failed: ' + problem
            item['confidence'] = min(item.get('confidence') or 0, 0.3)
    for page in pages:
        head = page['head']
        for key in ('title', 'description', 'canonical', 'robots', 'lang'):
            check(page, f'/head/{key}', head[key])
        for group in ('og', 'twitter'):
            for key, item in head[group].items():
                check(page, f'/head/{group}/{key}', item)
        for index, heading in enumerate(page['headings']):
            check(page, f'/headings/{index}', heading, 'text')
            if heading['level'] not in range(1, 7):
                issues.append({'page': page['id'], 'pointer': f'/headings/{index}', 'issue': 'invalid level'})
        for index, link in enumerate(page['links']['out']):
            # A link's edit points at the element: either its href or its anchor text must be there.
            value = link['href'] if not grounding_issue(files, link['edit'], link['href']) else link.get('anchor')
            check(page, f'/links/out/{index}', {'edit': link['edit'], 'value': value, 'method': link.get('method')})
        for index, image in enumerate(page['images']):
            check(page, f'/images/{index}', {'edit': image['edit'], 'value': image['src']})
        measured = page['rendering']['method'] == 'measured'
        ledger[page['id']] = {
            'head': 'measured' if measured else 'source-only',
            'headings': 'measured' if measured else 'source-only',
            'links': 'measured' if measured else 'source-only',
            'images': 'measured' if measured else 'source-only',
            'performance': 'measured' if page.get('performance') else 'gap',
            'rendering': 'measured' if page['rendering'].get('js_dependent') is not None else 'gap',
            'attribution': 'complete' if all((h.get('edit') or {}).get('kind') for h in page['headings']) else 'partial',
        }
    return {'issues': issues[:500], 'issue_count': len(issues), 'coverage_ledger': ledger,
            'checks': ['schema (field shapes, enums)', 'grounding (literal near cited line)', 'lens agreement (attribution.py)',
                       'coverage ledger (url x category state)']}


def live_sitemap_urls(base_url, limit=5000):
    from seo_agents.exploration.repo_analysis.tools.safe_xml import safe_xml_fromstring
    urls, queue, seen = [], [urljoin(base_url + '/', 'sitemap.xml')], set()
    from seo_agents.exploration.repo_analysis.tools.runtime import fetch_baseline as _fetch
    robots = _fetch(urljoin(base_url + '/', 'robots.txt'), timeout=10)
    text = robots.get('initial_html') or '' if robots.get('http_status') == 200 else ''
    for match in re.finditer(r'(?im)^\s*sitemap:\s*(\S+)', text):
        queue.append(match.group(1))
    while queue and len(urls) < limit and len(seen) < 50:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        from seo_agents.exploration.repo_analysis.tools.runtime import fetch_baseline
        response = fetch_baseline(url, timeout=10, max_body_bytes=5_000_000)
        body = response.get('initial_html')
        if response.get('http_status') != 200:
            continue
        try:
            from urllib.request import urlopen
            with urlopen(url, timeout=10) as stream:
                body = stream.read(10_000_000)
            root = safe_xml_fromstring(body)
        except Exception:  # noqa: BLE001 - unreachable or invalid sitemap is a gap, not a crash
            continue
        name = root.tag.rsplit('}', 1)[-1]
        for element in root.iter():
            if element.tag.rsplit('}', 1)[-1] == 'loc' and element.text:
                (queue if name == 'sitemapindex' else urls).append(element.text.strip()) if name == 'sitemapindex' else urls.append((element.text.strip(), 'sitemap:live'))
    return urls


def run_lighthouse(base_url, pages):
    from seo_agents.exploration.repo_analysis.runtime.lighthouse import measure_performance, performance_available
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
    results = {}
    with progress(f'site model: lighthouse on {len(chosen)} representative URLs'):
        for page in chosen:
            results[page['id']] = measure_performance(urljoin(base_url + '/', page['url'].lstrip('/')))
    return results


def run_link_checks(base_url, pages, measured):
    from seo_agents.exploration.repo_analysis.runtime.link_check import check_links
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


def probe_site(base_url):
    """Served behaviour: robots.txt, trailing slash, http->https, soft 404, security headers."""
    from seo_agents.exploration.repo_analysis.tools.runtime import fetch_baseline
    probes = {}
    home = fetch_baseline(base_url + '/', timeout=12)
    probes['home_headers'] = home.get('headers')
    robots = fetch_baseline(base_url + '/robots.txt', timeout=10)
    probes['robots_txt'] = {'status': robots.get('http_status'), 'body': (robots.get('initial_html') or '')[:5000] if robots.get('http_status') == 200 else None}
    missing = fetch_baseline(base_url + '/seo-agents-probe-' + hashlib.sha1(base_url.encode()).hexdigest()[:8], timeout=10)
    probes['soft_404'] = {'status': missing.get('http_status'), 'soft_404': missing.get('http_status') == 200}
    sample = '/about'
    slash = fetch_baseline(base_url + sample + '/', timeout=10)
    plain = fetch_baseline(base_url + sample, timeout=10)
    probes['trailing_slash'] = {'with_slash': slash.get('http_status'), 'without_slash': plain.get('http_status'),
                                'redirects': [r.get('location') for r in (slash.get('redirect_chain') or []) + (plain.get('redirect_chain') or [])]}
    if base_url.startswith('https://'):
        insecure = fetch_baseline('http://' + base_url[len('https://'):] + '/', timeout=10)
        probes['http_to_https'] = {'status': insecure.get('status'), 'redirect_chain': insecure.get('redirect_chain')}
    headers = probes['home_headers'] or {}
    probes['security_headers'] = {name: headers.get(name) for name in ('strict-transport-security', 'content-security-policy', 'x-frame-options',
                                                                       'x-content-type-options', 'referrer-policy', 'permissions-policy')}
    return probes


def site_facts(snapshot, fingerprint, index, manifest_notes, served, probes, deterministic, pages, duplicates, link_health,
               gap_report, validation, limitations, begun):
    from seo_agents.exploration.repo_analysis.tools.llm import usage_summary
    deterministic = deterministic or {}
    summary = {}
    for name in ('sitemap_robots', 'canonicals_redirects', 'https_security_headers', 'structured_data', 'hreflang_i18n',
                 'performance_web_vitals', 'brand_entity_signals', 'local_business_presence', 'ai_search_readiness', 'mobile_parity'):
        result = deterministic.get(name)
        if isinstance(result, dict):
            summary[name] = {'found_signals': result.get('found_signals'), 'gaps': [g.get('id') for g in result.get('gaps', [])],
                             **{k: result[k] for k in ('robots_files', 'sitemap_files', 'redirects', 'project_modes') if k in result}}
    depth = [p['links']['click_depth'] for p in pages if p['links']['click_depth'] is not None]
    return {
        'schema': SCHEMA, 'framework': fingerprint, 'snapshot_sha256': snapshot['sha256'],
        'generated_at': begun.isoformat(), 'duration_seconds': round((datetime.now(timezone.utc) - begun).total_seconds(), 2),
        'serve': served.describe() if served else {'mode': 'source-only', 'limitations': ['Measurement disabled (pass site_url, render=True or build=True).']},
        'url_manifest': manifest_notes,
        'i18n': {'catalog_keys': len(index['i18n']), 'default_locale': next(iter(sorted({e.get('locale') for v in index['i18n'].values() for e in v if e.get('locale')})), None)},
        'served': probes,
        'deterministic_signals': summary,
        'hreflang': hreflang_matrix(pages),
        'duplicates': duplicates,
        'link_health': link_health.get('coverage') if link_health else None,
        'broken_links': sorted({url for url, r in (link_health or {}).get('results', {}).items()
                                if r.get('status') in {404, 410} or (r.get('status') or 0) >= 500})[:200],
        'blocked_links': sorted({url for url, r in (link_health or {}).get('results', {}).items()
                                 if r.get('status') in {401, 403, 429, 999}})[:200],
        'redirected_links': sorted({url for url, r in (link_health or {}).get('results', {}).items() if r.get('redirects')})[:200],
        'click_depth': {'max': max(depth) if depth else None, 'pages_deeper_than_3': sum(d > 3 for d in depth)},
        'orphan_candidates': [p['url'] for p in pages if p['links'].get('orphan_candidate')][:200],
        'gap_workers': gap_report,
        'validation': {k: validation[k] for k in ('issue_count', 'issues', 'checks')},
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
