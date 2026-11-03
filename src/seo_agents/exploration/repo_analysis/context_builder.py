"""Assemble the repository SEO context JSON: references, not copies.

By default (`include_content='none'`) the JSON is compact: every record keeps only its location
(repo-relative file + line range) and its value; no code excerpts, hashes, JSON pointers or record ids.
Null/empty values are omitted and zero-count signals are listed once in `coverage.absent[]`. Repository prose
(README, CHANGELOG, docs/) is excluded from content and keywords upstream. `'referenced'` and `'all'` embed
full file contents and full records for callers that need them.
Execution-phase guidance (profile `execution_hint`) is never emitted here.
"""

from collections import defaultdict
import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path

from seo_agents.exploration.repo_analysis.tools.skill_runner import skills_identity
from seo_agents.log import log

SCHEMA_VERSION = 'repo-context/1.0'
CODE_EXCERPT_LINES = 12
CONTENT_MODES = ('referenced', 'all', 'none')


def _page_summaries(index, inventory, routes, keywords, rendering):
    """One row per page-like route: what the page file itself declares, with record ids."""
    by_file = defaultdict(lambda: defaultdict(list))
    for key in ('headings', 'text_blocks', 'links', 'media', 'front_matter'):
        for record in inventory[key]:
            by_file[record['location']['file']][key].append(record)
    for key in ('title_tags', 'meta_tags', 'link_tags'):
        for record in inventory['metadata'][key]:
            by_file[record['location']['file']][key].append(record)
    declared = defaultdict(list)
    for value in inventory['metadata']['declared_values']:
        declared[value['location']['file']].append(value)
    def declared_in(file):
        values = defaultdict(list)
        for d in declared[file]:
            values[d['field']].append(d['value'])
        for t in by_file[file]['title_tags']:
            values['title'].append(t['value'])
        for m in by_file[file]['meta_tags']:
            if m.get('name') and m['name'].lower() in {'description', 'robots', 'viewport', 'og:title', 'og:description', 'og:image'}:
                values[m['name'].lower()].append(m['value'])
        for l in by_file[file]['link_tags']:
            if (l.get('rel') or '').lower() == 'canonical':
                values['canonical'].append(l['href'])
        return dict(values)

    layouts = [{'route': r['route'], 'file': r['source']['location']['file'], 'declares': declared_in(r['source']['location']['file'])}
               for r in routes if r['kind'] == 'layout']
    pages = []
    for route in routes:
        if route['kind'] not in {'page', 'content-page', 'template'}:
            continue
        file = route['source']['location']['file']
        rows = by_file[file]
        fm = (rows['front_matter'][0]['fields'] if rows['front_matter'] else {})
        metas = {m['name'].lower(): m['value'] for m in rows['meta_tags'] if m.get('name')}
        titles = [t['value'] for t in rows['title_tags']] + [d['value'] for d in declared[file] if d['field'] == 'title']
        descriptions = ([metas['description']] if metas.get('description') else []) + \
            [d['value'] for d in declared[file] if d['field'] == 'description']
        if fm.get('title'):
            titles.append(fm['title'])
        if fm.get('description'):
            descriptions.append(fm['description'])
        headings = [{'level': h['level'], 'text': h['text'], 'record_id': h['id']} for h in rows['headings']]
        media_issues = sorted({i for m in rows['media'] for i in (m.get('issues') or [])})
        pages.append({
            'route': route['route'], 'kind': route['kind'], 'adapter': route.get('adapter'), 'is_dynamic': route['is_dynamic'], 'file': file,
            'file_ref': route['source']['location']['file_ref'],
            'declared_titles': titles, 'declared_descriptions': descriptions,
            'canonical': next((l['href'] for l in rows['link_tags'] if (l.get('rel') or '').lower() == 'canonical'), None),
            'robots_meta': metas.get('robots'),
            'h1': [h['text'] for h in headings if h['level'] == 1], 'headings': headings,
            'word_count': sum(len((b.get('text') or '').split()) for b in rows['text_blocks']),
            'internal_links': [{'href': l['href'], 'anchor_text': l.get('anchor_text'), 'record_id': l['id']}
                               for l in rows['links'] if l.get('link_type') == 'internal'],
            'external_links': len([l for l in rows['links'] if l.get('link_type') == 'absolute']),
            'media_count': len(rows['media']), 'media_issues': media_issues,
            'rendering_modes': rendering.get('per_file_modes', {}).get(file, []),
            'top_keywords': keywords['by_file'].get(file, [])[:10],
            'inherited_from_layouts': [l for l in layouts if route['route'] == l['route'] or
                                       route['route'].startswith(l['route'].rstrip('/') + '/')],
            'record_ids': index.referenced.get(file, []),
            'note': 'Values declared in this file only; layouts, components and runtime data may add or override them.',
        })
    return pages


RECORD_DROP = {'code', 'context', 'code_truncated', 'code_sha256', 'code_lines_omitted', 'id', 'record_id', 'kind', 'label'}
EMPTY = (None, '', [], {})


def reference_record(value):
    """Compact form of evidence for `include_content='none'`: location + value, nothing else."""
    if isinstance(value, list):
        return [reference_record(item) for item in value]
    if not isinstance(value, dict):
        return value
    if 'location' in value and ('code' in value or 'context' in value):
        location = value['location']
        out = {key: reference_record(child) for key, child in value.items() if key not in RECORD_DROP | {'location'}}
        out['file'] = location.get('file')
        if location.get('line_start'):
            out['line'] = location['line_start']
            if location.get('line_end') and location['line_end'] != location['line_start']:
                out['line_end'] = location['line_end']
        if location.get('asset'):
            out['asset'] = True
        if isinstance(out.get('matched_text'), str) and len(out['matched_text']) > 120:
            out['matched_text'] = out['matched_text'][:117] + '...'
        return out
    return {key: reference_record(child) for key, child in value.items()
            if key not in {'record_ids', 'record_id', 'file_ref', 'skill_sha256', 'skill_file'}}


def prune(value):
    """Drop None/empty values recursively (False and 0 are kept: they are facts)."""
    if isinstance(value, dict):
        out = {}
        for key, child in value.items():
            child = prune(child)
            if child not in EMPTY:
                out[key] = child
        return out
    if isinstance(value, list):
        return [item for item in (prune(v) for v in value) if item not in EMPTY]
    return value


def absent_signals(results):
    """Remove zero-match signals from agent results; return their ids once ('agent.signal')."""
    absent = []
    for name, result in results.items():
        signals = result.get('signals') if isinstance(result, dict) else None
        if not isinstance(signals, dict):
            continue
        for sid in list(signals):
            entry = signals[sid]
            if isinstance(entry, dict) and not entry.get('count') and not entry.get('total_matches'):
                absent.append(name + '.' + sid)
                del signals[sid]
            elif isinstance(entry, dict):
                for key in ('truncated',):
                    if entry.get(key) is False:
                        entry.pop(key)
                if entry.get('total_matches') == entry.get('count'):
                    entry.pop('total_matches', None)
        result.pop('found_signals', None)
    return sorted(absent)


def build_repo_context(index, results, *, repo_path=None, github_url=None, include_content='none'):
    """Assemble the JSON from `results` ({agent name: result}) produced by `pipeline.explore_codebase`."""
    if include_content not in CONTENT_MODES:
        raise ValueError('include_content must be one of ' + ', '.join(CONTENT_MODES))
    snapshot = index.snapshot
    inventory, routes, keywords = results['page_inventory'], results['routes'], results['keywords']
    rendering = results['rendering_strategy']
    pages = _page_summaries(index, inventory, routes, keywords, rendering)

    skill_results = {'code_exploration': results['code_exploration']['skill'],
                     'metadata': inventory['metadata']['apis'], 'content': inventory['content_sources'],
                     **{name: r for name, r in results.items() if isinstance(r, dict) and 'signals' in r}}
    gaps = [{'agent': name, **gap} for name, result in skill_results.items() for gap in result.get('gaps', [])]
    truncated = sorted(name + '.' + sid for name, result in skill_results.items()
                       for sid, signal in result.get('signals', {}).items() if signal.get('truncated'))

    if include_content == 'all':
        content_paths = sorted(index.files)
    else:
        content_paths = sorted(p for p in index.files if p in index.referenced)
    files = {p: index.file_entry(p, include_content=include_content != 'none') for p in content_paths}
    absent = []
    if include_content == 'none':
        import copy
        agents = reference_record(copy.deepcopy(results))
        absent = absent_signals(agents)
        agents = prune(agents)
        files = {p: {k: v for k, v in entry.items() if k in {'language', 'roles', 'line_count', 'size_bytes'}}
                 for p, entry in files.items()}
        pages = prune(reference_record(pages))
    else:
        agents = results

    context = {
        'schema_version': SCHEMA_VERSION,
        'kind': 'seo-repo-context',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'content_mode': include_content,
        'how_to_use': ('`agents` holds every agent result by name. In content_mode none every record is '
                       '{file, line[, line_end], value/matched_text}: read the checkout for the code. Signals with no '
                       'match are listed once in `coverage.absent`. `files` lists every referenced file (full `content` '
                       'only in referenced/all modes). Use `pages` for a per-route view and `gaps` for missing signals '
                       '(leads, not proof). Observations are static source facts, not rendered/deployed behaviour; the '
                       'site model (seo_reports/site_model) is the source of truth for served values.'),
        'repository': {'url': github_url, 'name': Path(repo_path).resolve().name if repo_path else None,
                       'snapshot_sha256': snapshot['sha256'], 'observed_at': snapshot.get('observed_at'),
                       'coverage': snapshot.get('coverage', {}), 'limitations': snapshot.get('limitations', [])},
        'agents': agents,
        'pages': pages,
        'gaps': gaps,
        'files': files,
        'file_inventory': [{k: row.get(k) for k in ('path', 'kind', 'status', 'reason', 'roles', 'size_bytes')
                            if row.get(k) not in EMPTY} for row in snapshot.get('inventory', [])],
        'coverage': {'absent': absent},
        'summary': {
            'agents_run': sorted(results),
            'framework_hints': [h['label'] for h in results['code_exploration']['framework_hints']],
            'route_adapters': sorted({r.get('adapter') for r in routes if r.get('adapter')}),
            'project_rendering_modes': rendering['project_modes'],
            'routes': len(routes), 'pages': len(pages),
            'records': len(index.records), 'files_referenced': len(files),
            'captured_source_files': len(index.files), 'assets': len(index.assets),
            'gaps': len(gaps), 'truncated_signals': truncated,
            'primary_keyword_candidates': keywords['primary_candidates'][:10],
        },
        'identity': {'skills_sha256': hashlib.sha256(json.dumps(skills_identity(), sort_keys=True).encode()).hexdigest()},
    }
    if include_content == 'none':
        context = prune(context)
        context.setdefault('coverage', {'absent': []})
    log('repo context: ' + json.dumps(context['summary'], ensure_ascii=False))
    return context


def write_repo_context(context, output_path):
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    compact = context.get('content_mode', 'none') == 'none'
    path.write_text(json.dumps(context, ensure_ascii=False, allow_nan=False,
                               **({'separators': (',', ':')} if compact else {'indent': 2})) + '\n')
    log('repo context written: ' + str(path) + ' (' + str(path.stat().st_size // 1024) + ' KiB)')
    return path
