"""Queryable derived views; original page content remains in the seven reports."""

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import PurePosixPath
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from seo_agents.exploration.checks.agents.retrieval import page_records, text_chunk
from seo_agents.exploration.checks.tools.helpers import REPORT_FILES


def origin(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        return None
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    return parsed.scheme.lower(), parsed.hostname.lower(), port


def route_pattern(url):
    return bool(re.search(r'[\[\]{}*]|(?:^|/):[^/]+', urlsplit(url).path))


def resolve_destination(raw, page_url, site_url=None, base_href=None):
    base = urljoin(site_url or '', page_url)
    if base_href:
        base = urljoin(base, base_href)
    resolved = urljoin(base, raw)
    parsed = urlsplit(resolved)
    if parsed.scheme and parsed.scheme not in {'http', 'https'}:
        kind = 'action'
    elif parsed.netloc:
        kind = 'internal' if site_url and origin(resolved) == origin(site_url) else 'external' if site_url else 'absolute-unclassified'
    else:
        kind = 'internal'
    if kind == 'internal' and PurePosixPath(parsed.path).suffix.lower() in {
            '.pdf', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.avif', '.zip', '.mp4', '.mp3', '.css', '.js'}:
        kind = 'resource'
    return {'resolved_url': resolved, 'destination': urlunsplit(parsed._replace(fragment='')),
            'kind': kind, 'query': parsed.query, 'fragment': parsed.fragment,
            'health': 'not-checked'}


def refs(value):
    if isinstance(value, list):
        for child in value:
            yield from refs(child)
    elif isinstance(value, dict):
        if isinstance(value.get('file'), str):
            yield value['file']
        for key, child in value.items():
            if key != 'coverage':
                yield from refs(child)


def source_relationships(snapshot, pages):
    """Reverse source lookup; dependency candidates do not establish page usage."""
    inventory = {row['path']: row for row in snapshot.get('inventory', []) if row['kind'] == 'file'}
    paths = set(snapshot['files'])
    observed, candidates = defaultdict(set), defaultdict(set)
    for page in pages:
        for file in page['observed_source_files']:
            paths.add(file)
            observed[file].add(page['page_id'])
        for file in page['dependency_candidate_files']:
            paths.add(file)
            candidates[file].add(page['page_id'])
    result = {}
    for file in sorted(paths):
        row = inventory.get(file, {})
        result[file] = {'file': file, 'capture_status': row.get('status', 'not-in-snapshot-inventory'),
            'roles': row.get('roles', []),
            'corpus_ref': '/source_corpus/' + file.replace('~', '~0').replace('/', '~1') if file in snapshot['files'] else None,
            'directly_observed_page_ids': sorted(observed[file]),
            'dependency_candidate_page_ids': sorted(candidates[file]),
            'attribution': 'observed-in-page-report' if observed[file] else
                'dependency-candidate-only' if candidates[file] else 'not-associated-with-enumerated-pages'}
    return result


def build_evidence(state, *, site_url=None):
    reports, snapshot = state.get('artifacts', {}), state['snapshot']
    context = state['context']
    by_category = {key: {(p['url'], p['template']): (index, p) for index, p in enumerate(report['pages'])}
                   for key, report in reports.items()}
    rows = reports.get('urls', {}).get('pages', [])
    pages, occurrences, destinations, duplicates = [], [], {}, defaultdict(list)
    concrete, unresolved = {}, []
    edges = defaultdict(set)
    for record in context['records']:
        if record['kind'] == 'module-dependency' and record.get('resolved_file'):
            edges[record['file']].add(record['resolved_file'])
    base_hrefs = {}
    for record in context['records']:
        if record['kind'] == 'head-declaration' and record.get('tag') == 'base':
            if record['attributes'].get('href'):
                base_hrefs.setdefault(record['file'], record['attributes']['href'])

    def discover(value, evidence, kind):
        if kind != 'internal' or route_pattern(value):
            return
        entry = concrete.setdefault(value, {'url': value, 'sources': [], 'fetch': 'pending', 'render': 'unavailable'})
        entry['sources'].append(evidence)

    for page in rows:
        identity = (page['url'], page['template'])
        page_id = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:20]
        pointers, source_files = {}, {page['template']}
        for category, indexed in by_category.items():
            if identity in indexed:
                index, row = indexed[identity]
                pointers[category] = f'/reports/{category}/pages/{index}'
                source_files.update(refs(row))
        frontier = list(source_files)
        lineage = set(source_files)
        while frontier:
            file = frontier.pop()
            for dependency in edges[file] - lineage:
                lineage.add(dependency)
                frontier.append(dependency)
        content = by_category.get('content', {}).get(identity, (None, {}))[1]
        blocks = content.get('blocks', [])
        main = [b for b in blocks if b.get('section') not in {'header', 'nav', 'footer'}
                and b.get('ui_state', 'initial') == 'initial']
        text = ' '.join(b['text'] for b in main)
        normalized = ' '.join(text.casefold().split())
        fingerprint = hashlib.sha256(normalized.encode()).hexdigest() if normalized else None
        if fingerprint:
            duplicates[fingerprint].append(page_id)
        terms = Counter(re.findall(r'[^\W_]+(?:[’\'][^\W_]+)*', text.casefold(), re.UNICODE))
        metadata = by_category.get('metadata', {}).get(identity, (None, {}))[1]
        declared = [{'value': d.get('content'), 'source_id': d.get('source_id')}
                    for d in metadata.get('declarations', []) if (d.get('name') or '').casefold() == 'keywords']
        pages.append({'page_id': page_id, 'url': page['url'], 'template': page['template'],
                      'is_dynamic': page['is_dynamic'], 'report_refs': pointers,
                      'route_family': page.get('route_family'), 'instance_status': page.get('instance_status'),
                      'locale': page.get('locale'), 'content_sources': page.get('content_sources', []),
                      'observed_source_files': sorted(source_files), 'dependency_candidate_files': sorted(lineage - source_files),
                      'content_fingerprint': fingerprint, 'fingerprint_method': 'casefolded whitespace-normalized exact initial non-chrome text',
                      'keywords': {'declared_meta_keywords': declared, 'observed_terms': dict(terms.most_common()),
                                   'suggested_targets': [], 'method': 'Unicode lexical counts; no stemming, language segmentation, intent or market metrics'},
                      'coverage': page['coverage']})
        resolved = resolve_destination(page['url'], '/', site_url)
        is_concrete = page.get('instance_status') == 'concrete' or not page['is_dynamic'] and not route_pattern(page['url'])
        if not is_concrete:
            unresolved.append({'route_family': page['url'], 'template': page['template'],
                               'instance_denominator': None, 'reason': 'Route family is not a concrete instance inventory',
                               'limitations': page['coverage'].get('limitations', [])})
        else:
            discover(resolved['destination'], {'kind': 'route', 'page_id': page_id}, resolved['kind'])
        link_entry = by_category.get('internal_links', {}).get(identity)
        if link_entry:
            index, row = link_entry
            for number, link in enumerate(row['links']):
                try:
                    normalized_link = resolve_destination(link['href'], page['url'], site_url, base_hrefs.get(page['template']))
                except ValueError:
                    normalized_link = {'resolved_url': None, 'destination': None, 'kind': 'unresolved', 'health': 'not-checked'}
                occurrence = {'page_id': page_id, 'report_ref': f'/reports/internal_links/pages/{index}/links/{number}',
                              'source_id': link['source_id'], **normalized_link}
                occurrences.append(occurrence)
                key = normalized_link['destination']
                if key is not None:
                    target = destinations.setdefault(key, {'url': key, 'kind': normalized_link['kind'], 'occurrence_indices': [], 'health': 'not-checked'})
                    target['occurrence_indices'].append(len(occurrences) - 1)
                    if is_concrete and link.get('ui_state', 'initial') == 'initial':
                        discover(key, {'kind': 'link', 'occurrence_index': len(occurrences) - 1}, normalized_link['kind'])
    for index, record in enumerate(context['records']):
        if record['kind'] == 'sitemap-url':
            try:
                resolved = resolve_destination(record['value'], '/', site_url)
                discover(resolved['destination'], {'kind': 'sitemap', 'context_ref': f'/context/records/{index}'}, resolved['kind'])
            except ValueError:
                pass
    incoming = Counter(o['destination'] for o in occurrences if o['kind'] == 'internal')
    for page in pages:
        destination = resolve_destination(page['url'], '/', site_url)['destination']
        page['observed_incoming_links'] = incoming[destination]
        known_instance = page.get('instance_status') == 'concrete' or not page['is_dynamic'] and not route_pattern(page['url'])
        page['no_incoming_link_candidate'] = known_instance and incoming[destination] == 0
    category_coverage = {key: {'page_rows': len(report['pages']),
        'status': 'completed-with-source-limitations',
        'source_files_accessed': len(report['extraction']['script_access']['files']) if 'script_access' in report['extraction'] else None,
        'semantically_complete': False} for key, report in reports.items()}
    for key in REPORT_FILES:
        if key not in category_coverage:
            category_coverage[key] = {'status': 'pending', 'page_rows': None, 'source_files_accessed': None, 'semantically_complete': False}
    relationships = source_relationships(snapshot, pages)
    attribution_counts = Counter(row['attribution'] for file, row in relationships.items() if file in snapshot['files'])
    return {'schema_version': '1.0', 'kind': 'seo-exploration-evidence',
            'snapshot_sha256': snapshot['sha256'], 'observed_at': snapshot.get('observed_at'),
            'scope': {'site_url': site_url, 'observation': 'source', 'external_crawling': False},
            'source_corpus': snapshot['files'],
            'asset_inventory': snapshot['assets'],
            'reports': reports, 'repository': {'inventory': snapshot.get('inventory', []),
                'coverage': snapshot.get('coverage', {}), 'limitations': snapshot['limitations']},
            'context': context, 'pages': pages, 'link_occurrences': occurrences,
            'source_relationships': relationships,
            'destinations': list(destinations.values()), 'concrete_urls': list(concrete.values()),
            'unresolved_route_families': unresolved,
            'duplicate_content_candidates': [{'fingerprint': key, 'page_ids': ids} for key, ids in duplicates.items() if len(ids) > 1],
            'coverage': {'repository': snapshot.get('coverage', {}), 'extraction': category_coverage,
                'source_attribution': {'captured_source_files': len(snapshot['files']),
                    'states': dict(attribution_counts),
                    'report_files_outside_capture': sorted(file for file in relationships if file not in snapshot['files']),
                    'note': 'Report references establish observations; transitive literal imports are candidates. Unassociated files are not proven unused or irrelevant.'},
                'url_discovery': {'route_rows': len(rows), 'known_concrete_urls': len(concrete),
                                  'unresolved_families': len(unresolved), 'possible_url_denominator': None},
                'link_validation': {'destinations': len(destinations), 'checked': 0, 'pending': len(destinations)},
                'rendering': {'completed': 0, 'status': 'unsupported-no-browser-runner'}},
            'efficiency': {key: run.get('metrics', {}) for key, run in state.get('agent_runs', {}).items()},
            'limitations': ['Source observations and local declarations are not deployed HTTP or rendered facts.',
                'No-incoming-link candidates are relative to these source inventories, not verified orphans.',
                'Template/JSON/import candidates do not prove that every string becomes visible page content.',
                'Exact fingerprints do not detect semantic or near duplicates; no ranking or indexing conclusions are made.']}


def query_evidence(bundle, pointer='', *, offset=0):
    """Bounded JSON-pointer query; no re-extraction or model calls."""
    value = bundle
    if pointer and not pointer.startswith('/'):
        raise ValueError('Expected a JSON pointer beginning with /')
    for token in pointer.split('/')[1:]:
        token = token.replace('~1', '/').replace('~0', '~')
        value = value[int(token)] if isinstance(value, list) else value[token]
    if isinstance(value, list):
        # Return compact record previews; each complete item remains addressable.
        rows = [{'pointer': pointer + '/' + str(i), 'preview': json.dumps(row, ensure_ascii=False)[:900]}
                for i, row in enumerate(value)]
        return page_records(rows, offset, limit=30)
    return text_chunk(json.dumps(value, ensure_ascii=False), offset, {'pointer': pointer}, field='json_text')
