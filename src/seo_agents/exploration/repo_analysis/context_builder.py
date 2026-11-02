"""Assemble the repository SEO context JSON: references, not copies.

By default (`include_content='none'`) every record keeps its exact location
(file, line range, JSON pointer), a short code excerpt (<= 12 lines) and the
sha256 of the full construct; consumers read source on demand. `'referenced'`
and `'all'` embed full file contents and full records for callers that need them.
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


def compact_record(value):
    """Reference form of evidence: location + code excerpt + hash; no context blob."""
    if isinstance(value, list):
        return [compact_record(item) for item in value]
    if not isinstance(value, dict):
        return value
    if 'location' in value and ('code' in value or 'context' in value):
        out = {key: compact_record(child) for key, child in value.items() if key not in {'code', 'context'}}
        code = value.get('code')
        if isinstance(code, str):
            lines = code.split('\n')
            out['code'] = '\n'.join(lines[:CODE_EXCERPT_LINES])
            out['code_sha256'] = hashlib.sha256(code.encode()).hexdigest()
            if len(lines) > CODE_EXCERPT_LINES or value.get('code_truncated'):
                out['code_truncated'] = True
                out['code_lines_omitted'] = max(0, len(lines) - CODE_EXCERPT_LINES)
        return out
    return {key: compact_record(child) for key, child in value.items()}


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
    agents = results if include_content != 'none' else compact_record(results)

    context = {
        'schema_version': SCHEMA_VERSION,
        'kind': 'seo-repo-context',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'content_mode': include_content,
        'how_to_use': ('`agents` holds every agent result by name. Every record has `location` (file, line_start, '
                       'line_end, file_ref JSON pointer into `files`) and `code` (the construct; capped to '
                       + str(CODE_EXCERPT_LINES) + ' lines with `code_sha256` of the whole construct when '
                       'content_mode is none). `files` lists every referenced file with its sha256 and line count '
                       '(and full `content` only in referenced/all modes); read the checkout for the rest. Use `pages` '
                       'for a per-route view and `gaps` for missing signals (leads, not proof). Each signal carries '
                       '`precision`, `total_matches` and `truncated`. Observations are static source facts, not '
                       'rendered/deployed behaviour.'),
        'repository': {'url': github_url, 'root': str(Path(repo_path).resolve()) if repo_path else None,
                       'snapshot_sha256': snapshot['sha256'], 'observed_at': snapshot.get('observed_at'),
                       'coverage': snapshot.get('coverage', {}), 'limitations': snapshot.get('limitations', [])},
        'agents': agents,
        'pages': pages,
        'gaps': gaps,
        'files': files,
        'file_inventory': [{k: row.get(k) for k in ('path', 'kind', 'status', 'reason', 'roles', 'size_bytes')}
                           for row in snapshot.get('inventory', [])],
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
        'identity': {'skills': skills_identity()},
    }
    log('repo context: ' + json.dumps(context['summary'], ensure_ascii=False))
    return context


def write_repo_context(context, output_path):
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(context, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    log('repo context written: ' + str(path) + ' (' + str(path.stat().st_size // 1024) + ' KiB)')
    return path
