"""Byte-bounded, recoverable model views over complete in-memory evidence."""

from collections import Counter
import json

from langchain_core.tools import tool

OUTPUT_BYTES = 16_000


def encoded_size(value):
    return len(json.dumps(value, ensure_ascii=False).encode())


def page_records(records, offset=0, limit=100, budget=OUTPUT_BYTES):
    offset = max(0, offset)
    selected = []
    for row in records[offset:offset + limit]:
        if encoded_size(selected + [row]) > budget - 1500:
            if not selected:
                selected.append({'record_id': row.get('record_id', offset), 'oversized_record': True,
                                 'preview_json': json.dumps(row, ensure_ascii=False)[:800],
                                 'recovery': 'Use read_index_record or the corresponding artifact JSON pointer'})
            break
        selected.append(row)
    return {'records': selected, 'total': len(records),
            'next_offset': offset + len(selected) if offset + len(selected) < len(records) else None,
            'truncated': offset + len(selected) < len(records) or any(r.get('oversized_record') for r in selected)}


def text_chunk(text, offset, metadata, budget=OUTPUT_BYTES, field='text'):
    offset = max(0, min(offset, len(text)))
    low, high = 0, min(len(text) - offset, budget)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = {**metadata, field: text[offset:offset + middle], 'offset': offset,
                     'next_offset': offset + middle, 'truncated': True}
        if encoded_size(candidate) <= budget - 200:
            low = middle
        else:
            high = middle - 1
    end = offset + low
    return {**metadata, field: text[offset:end], 'offset': offset,
            'next_offset': end if end < len(text) else None, 'truncated': end < len(text)}


def repository_summary(snapshot, context):
    roles = Counter(role for r in snapshot.get('inventory', []) for role in r.get('roles', []))
    # No all-path/all-asset dump. Full inventories remain available through tools.
    summary = {'snapshot_sha256': snapshot['sha256'], 'source_files': len(snapshot['files']),
            'asset_files': len(snapshot['assets']), 'candidate_roles': dict(roles),
            'packages': [{key: p.get(key) for key in ('file', 'root', 'frameworks')} for p in context['packages'][:12]],
            'package_count': len(context['packages']),
            'context_records': len(context['records']), 'parse_errors': len(context['parse_errors']),
            'coverage': snapshot.get('coverage', {}),
            'tools': 'list_files, read_sources, search_sources, query_context, read_context_record, list_routes'}
    while encoded_size(summary) > OUTPUT_BYTES and summary['packages']:
        summary['packages'].pop()
    return summary


def repository_tools(snapshot, inspected=None, *, context=None, routes=None, metrics=None):
    files = snapshot['files']
    context = context or {'records': []}
    metrics = metrics if metrics is not None else {}
    metrics.update(tool_calls=0, output_bytes=0, source_read_requests=0, repeated_source_reads=0)
    read_paths = set()
    read_ranges = set()

    def emit(name, result):
        metrics['tool_calls'] += 1
        metrics['output_bytes'] += encoded_size(result)
        metrics[name + '_calls'] = metrics.get(name + '_calls', 0) + 1
        return result

    def read(path, start_line=1, max_lines=150, start_column=0, budget=OUTPUT_BYTES):
        if path not in files:
            return {'error': 'Source unavailable; query list_files for its capture status', 'path': path[:500]}
        metrics['source_read_requests'] += 1
        identity = (path, start_line, max_lines, start_column, budget)
        metrics['repeated_source_reads'] += int(identity in read_ranges)
        read_ranges.add(identity)
        read_paths.add(path)
        metrics['unique_source_files_requested'] = len(read_paths)
        metrics['repeated_source_reads_meaning'] = 'Identical requested ranges, excluding required continuation chunks'
        text = files[path]
        lines = text.splitlines(keepends=True)
        start = max(1, start_line)
        column = max(0, start_column)
        begin = sum(len(line) for line in lines[:start - 1]) + column
        end = sum(len(line) for line in lines[:start - 1 + max(1, min(max_lines, 200))])
        begin = min(begin, end)
        result = text_chunk(text[:end], begin, {'path': path, 'total_lines': len(lines),
                            'start_line': start, 'start_column': column}, budget=budget, field='lines')
        cursor = result['next_offset'] if result['next_offset'] is not None else end
        result['next_line'] = text[:cursor].count('\n') + 1 if cursor < len(text) else None
        result['next_column'] = cursor - (text.rfind('\n', 0, cursor) + 1) if cursor < len(text) else None
        result['truncated'] = cursor < len(text)
        if inspected is not None and result['lines']:
            inspected.add(path)
        return result

    @tool
    def list_files(prefix: str = '', offset: int = 0, role: str = '', status: str = '') -> dict:
        """Page through file capture states and candidate roles, including omissions/exclusions."""
        inventory = snapshot.get('inventory') or [{'path': p, 'status': 'captured'} for p in files]
        rows = [{**r, 'record_id': i} for i, r in enumerate(inventory) if r['path'].startswith(prefix)
                and (not role or role in r.get('roles', [])) and (not status or r['status'] == status)]
        result = page_records(rows, offset, budget=6500)
        result['paths'] = [r['path'] for r in result['records'] if 'path' in r]
        return emit('list_files', result)

    @tool
    def read_source(path: str, start_line: int = 1, max_lines: int = 150, start_column: int = 0) -> dict:
        """Read source within a byte budget. Continue at next_line AND next_column, including long lines."""
        return emit('read_source', read(path, start_line, max_lines, start_column))

    @tool
    def read_sources(paths: list[str]) -> dict:
        """Batch up to eight known related paths. Each result has a recovery cursor."""
        selected = paths[:8]
        budget = max(800, (OUTPUT_BYTES - 2000) // max(1, len(selected)))
        result = {'results': [read(path, budget=budget) for path in selected],
                  'pending_count': max(0, len(paths) - 8), 'pending_indices': list(range(8, min(len(paths), 16)))}
        # Extremely long requested paths should not consume an unbounded response.
        while encoded_size(result) > OUTPUT_BYTES and result['results']:
            result['results'].pop()
            result['pending_count'] += 1
            result['pending_indices'].insert(0, len(result['results']))
        return emit('read_sources', result)

    def search(needles, prefix, offset):
        needles = [n.casefold() for n in needles[:12] if n]
        matches = []
        for path, text in files.items():
            if not path.startswith(prefix):
                continue
            for index, line in enumerate(text.splitlines(), 1):
                folded = line.casefold()
                positions = [folded.find(n) for n in needles if n in folded]
                if positions:
                    column = min(positions)
                    matches.append({'file': path, 'line': index, 'column_hint': column,
                                    'preview': line[max(0, column - 80):column + 300],
                                    'recovery': 'read_source at line; column_hint is approximate after Unicode case folding'})
        result = page_records(matches, offset, limit=60)
        if not needles:
            result['error'] = 'Search text must not be empty'
        return result

    @tool
    def search_source(needle: str, prefix: str = '', offset: int = 0) -> dict:
        """Search literal source text; previews can be recovered with read_source."""
        result = search([needle], prefix, offset)
        result['matches'] = result.pop('records')
        return emit('search_source', result)

    @tool
    def search_sources(needles: list[str], prefix: str = '', offset: int = 0) -> dict:
        """Batch up to twelve literal patterns in one source traversal, preserving evidence locations."""
        return emit('search_sources', search(needles, prefix, offset))

    @tool
    def query_context(kind: str = '', prefix: str = '', offset: int = 0) -> dict:
        """Query cached dependencies/data, JSON pointers, HTML text/head/headings/navigation-link/media-declaration/structured-data, Markdown and sitemaps."""
        rows = []
        for index, record in enumerate(context['records']):
            if (not kind or record['kind'] == kind) and record['file'].startswith(prefix):
                preview = json.dumps(record, ensure_ascii=False)[:900]
                rows.append({'record_id': index, 'kind': record['kind'], 'file': record['file'],
                             'preview': preview, 'recovery': 'read_context_record'})
        return emit('query_context', page_records(rows, offset, limit=30))

    @tool
    def read_context_record(record_id: int, offset: int = 0) -> dict:
        """Recover the full JSON representation of a context record in byte-bounded chunks."""
        if record_id < 0 or record_id >= len(context['records']):
            return emit('read_context_record', {'error': 'Unknown record_id'})
        value = json.dumps(context['records'][record_id], ensure_ascii=False)
        return emit('read_context_record', text_chunk(value, offset, {'record_id': record_id}, field='json_text'))

    @tool
    def list_routes(offset: int = 0) -> dict:
        """Query the verified route catalog; do not put the whole catalog into conversation context."""
        return emit('list_routes', page_records([{**row, 'record_id': i} for i, row in enumerate(routes or [])], offset))

    @tool
    def read_index_record(collection: str, record_id: int, offset: int = 0) -> dict:
        """Recover an oversized inventory or routes record as complete JSON text chunks."""
        records = {'inventory': snapshot.get('inventory', []), 'routes': routes or []}.get(collection, [])
        if record_id < 0 or record_id >= len(records):
            return emit('read_index_record', {'error': 'Unknown collection or record_id'})
        value = json.dumps(records[record_id], ensure_ascii=False)
        return emit('read_index_record', text_chunk(value, offset, {'collection': collection, 'record_id': record_id}, field='json_text'))

    return [list_files, read_source, read_sources, search_source, search_sources,
            query_context, read_context_record, list_routes, read_index_record]
