"""Reject unsafe scripts, invalid reports and non-repeatable extraction."""

import hashlib
import html
import json
import re
from pathlib import Path, PurePosixPath
import subprocess
import sys

from seo_agents.exploration.repo_analysis.agents.llm.script_policy import entry_arity, validate_script
from seo_agents.exploration.repo_analysis.tools.source_context import strict_json
from seo_agents.exploration.repo_analysis.tools.contracts import validate_report


class ObservationValidationError(ValueError):
    """Bounded field-level feedback, without dumping extracted client content."""
    def __init__(self, issues):
        self.issues = issues
        super().__init__('Invalid observations: ' + json.dumps(issues, ensure_ascii=True))


ELEMENT_FIELDS = ('id', 'line', 'col', 'end_line', 'tag', 'kind', 'attrs', 'text', 'text_literal', 'parent',
                  'section', 'heading_level', 'component_ref', 'dynamic_attrs', 'spread_props', 'raw_text')


def compact_index(element_index, budget_bytes=24_000_000):
    """Plain-dict element index for generated scripts: elements grouped by file, plus data, i18n and refs."""
    if not element_index:
        return {}
    by_file = {}
    for element in element_index.get('elements', []):
        by_file.setdefault(element['file'], []).append({key: element[key] for key in ELEMENT_FIELDS if key in element})
    compact = {'elements_by_file': by_file,
               'data': [{key: record.get(key) for key in ('id', 'file', 'pointer', 'line', 'value', 'kind', 'i18n_key', 'locale')}
                        for record in element_index.get('data', [])],
               'i18n': element_index.get('i18n', {}),
               'refs': [{key: ref.get(key) for key in ('file', 'line', 'kind', 'spec', 'resolved')}
                        for ref in element_index.get('refs', [])]}
    if len(json.dumps(compact, ensure_ascii=False).encode()) > budget_bytes:
        # Keep structure; drop the bulkiest optional fields first.
        for elements in by_file.values():
            for element in elements:
                element.pop('text', None)
                element.pop('raw_text', None)
        compact['data'] = compact['data'][:20_000]
    return compact


def execute_script(source, snapshot, timeout=12, *, routes=None, index=None):
    tree = validate_script(source)
    payload = {'source': source, 'files': snapshot['files'], 'assets': snapshot['assets']}
    if entry_arity(tree) == 4:
        payload['routes'] = routes or []
        payload['index'] = index if index is not None else {}
    payload = json.dumps(payload, ensure_ascii=False)
    if len(payload.encode()) > 64_000_000:
        raise ValueError('Worker input exceeds 64 MB budget; reduce snapshot scope')
    try:
        process = subprocess.run(
            [sys.executable, '-I', '-B', str(Path(__file__).with_name('script_worker.py'))],
            input=payload, text=True, capture_output=True, timeout=timeout,
            env={}, cwd=Path(__file__).parent,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError('Extractor exceeded execution time limit') from error
    if process.returncode:
        raise ValueError('Extractor trial failed: ' + (process.stderr[:2000] or 'worker resource limit exceeded'))
    try:
        output = json.loads(process.stdout)
        report = output['report']
        if isinstance(report, dict) and isinstance(report.get('extraction'), dict):
            report['extraction']['script_access'] = {'files': output['accessed_files'],
                'html_parsing': output['html_parsing']}
        return report
    except (ValueError, RecursionError) as error:
        raise ValueError('Extractor did not return valid JSON') from error


GROUNDED_FIELDS = ('text', 'href', 'src', 'alt', 'anchor_text', 'title', 'content')
VALUE_ORIGINS = {'expression', 'data', 'i18n', 'prop'}
GROUNDING_WINDOW = 3
GROUNDING_HINT = ('Cite the line where this literal value appears (±3 lines), or mark value_origin '
                  '(expression|data|i18n|prop) with an origin_ref that resolves: a data/i18n record id (d_...), '
                  'an element id (e_...), {file, pointer} for JSON data or {i18n_key}. Never cite an unrelated line.')


def alnum(value, strip_tags=False):
    """Normalised comparison form: unescaped, casefolded word characters only (optionally tag-free)."""
    if strip_tags:
        value = re.sub(r'<[^>]*>', ' ', value)
    return re.sub(r'[\W_]+', '', html.unescape(value).casefold())


def resolve_pointer(document, pointer):
    value = document
    for token in pointer.split('/')[1:]:
        token = token.replace('~1', '/').replace('~0', '~')
        if isinstance(value, list):
            if not token.isascii() or not token.isdigit() or str(int(token)) != token:
                raise ValueError('Invalid JSON array index')
            value = value[int(token)]
        elif isinstance(value, dict):
            value = value[token]
        else:
            raise ValueError('JSON pointer traverses a scalar')
    return value


class Grounding:
    """Checks that cited observation values exist at (or are supplied to) their cited source location."""

    def __init__(self, snapshot, index=None):
        self.files = snapshot['files']
        self.snapshot = snapshot
        self._index = index
        self._windows = {}
        self._json = {}

    @property
    def index(self):
        if self._index is None:
            from seo_agents.exploration.repo_analysis.tools.element_index import build_element_index
            self._index = build_element_index(self.snapshot)
        return self._index

    def window(self, file, line):
        key = (file, line)
        if key not in self._windows:
            lines = self.files[file].splitlines()
            low, high = max(0, line - 1 - GROUNDING_WINDOW), line + GROUNDING_WINDOW
            text = ' '.join(lines[low:high])
            self._windows[key] = (alnum(text), alnum(text, strip_tags=True))
        return self._windows[key]

    def origin_resolves(self, record):
        ref = record.get('origin_ref')
        if record.get('value_origin') not in VALUE_ORIGINS or ref in (None, '', {}):
            return False
        if isinstance(ref, str):
            if ref.startswith('d_'):
                return any(item['id'] == ref for item in self.index.get('data', []))
            if ref.startswith('e_'):
                return any(item['id'] == ref for item in self.index.get('elements', []))
            return ref in self.index.get('i18n', {})
        if isinstance(ref, dict):
            if isinstance(ref.get('i18n_key'), str):
                return ref['i18n_key'] in self.index.get('i18n', {})
            file = ref.get('file')
            if not isinstance(file, str) or file not in self.files:
                return False
            if isinstance(ref.get('pointer'), str) and ref['pointer'].startswith('/'):
                try:
                    if file not in self._json:
                        self._json[file] = strict_json(self.files[file])
                    resolve_pointer(self._json[file], ref['pointer'])
                    return True
                except (ValueError, KeyError, IndexError, TypeError, RecursionError):
                    return any(item['file'] == file and item['pointer'] == ref['pointer'] for item in self.index.get('data', []))
            line = ref.get('line')
            return type(line) is int and 1 <= line <= len(self.files[file].splitlines())
        return False

    def check(self, record, pointer, issues):
        file, line = record.get('file'), record.get('line')
        if not isinstance(file, str) or file not in self.files or type(line) is not int:
            return
        for field in GROUNDED_FIELDS:
            value = record.get(field)
            if not isinstance(value, str):
                continue
            needle = alnum(value)[:60]
            if not needle or any(needle in window for window in self.window(file, line)):
                continue
            if self.origin_resolves(record):
                continue
            issues.append({'pointer': pointer + '/' + field, 'file': file[:180], 'line': line, 'field': field,
                           'problem': 'value not found within ±3 lines of the cited location', 'hint': GROUNDING_HINT})
            return


def validate_observations(category, report, snapshot, *, index=None, grounding=True):
    validate_report(category, report)
    grounder = Grounding(snapshot, index) if grounding else None
    ungrounded = []
    files = snapshot['files']
    if not report['pages']:
        raise ValueError('No pages discovered; investigate entry points instead of accepting an empty inventory')
    identities = set()
    line_counts = {}
    json_documents = {}

    def relationships(items, page_index):
        unresolved = False
        for index, item in enumerate(items):
            location = f'/pages/{page_index}/content_sources/{index}'
            if not isinstance(item, dict):
                raise ValueError(location + ': expected a source relationship object')
            if 'file' not in item:
                continue  # External source intentions are not local-file observations.
            file = item['file']
            if not isinstance(file, str) or file not in files:
                raise ValueError(location + '/file: relationship must reference a captured source file')
            line = item.get('line')
            if line is not None and (type(line) is not int or not 1 <= line <= len(files[file].splitlines())):
                raise ValueError(location + '/line: expected an in-range source line when provided')
            if item.get('source_id') is not None and (not isinstance(item['source_id'], str) or not item['source_id']):
                raise ValueError(location + '/source_id: expected a nonempty string when provided')
            if 'pointer' not in item:
                continue  # A captured file itself is a valid relationship target.
            pointer = item['pointer']
            if not isinstance(pointer, str) or pointer and not pointer.startswith('/') or re.search(r'~(?:[^01]|$)', pointer):
                raise ValueError(location + '/pointer: expected a valid JSON pointer')
            if not pointer:
                item['pointer_validation'] = 'file-reference'
                continue  # Empty pointer denotes the source file, including a template.
            if PurePosixPath(file).suffix.lower() != '.json':
                item['pointer_validation'] = 'unverified-source-selector'
                unresolved = True
                continue
            try:
                if file not in json_documents:
                    json_documents[file] = strict_json(files[file])
                value = json_documents[file]
                for token in pointer.split('/')[1:]:
                    token = token.replace('~1', '/').replace('~0', '~')
                    if isinstance(value, list):
                        if not token.isascii() or not token.isdigit() or str(int(token)) != token:
                            raise ValueError('Invalid JSON array index')
                        value = value[int(token)]
                    elif isinstance(value, dict):
                        value = value[token]
                    else:
                        raise ValueError('JSON pointer traverses a scalar')
                item['pointer_validation'] = 'resolved-json-pointer'
            except (ValueError, KeyError, IndexError, TypeError, RecursionError) as error:
                item['pointer_validation'] = 'invalid-or-unresolvable-json-pointer'
                item['pointer_error'] = type(error).__name__
                unresolved = True
        return unresolved

    def evidence(value, *, page_root=False, pointer=''):
        if isinstance(value, list):
            for index, item in enumerate(value):
                evidence(item, pointer=pointer + '/' + str(index))
        elif isinstance(value, dict):
            if 'file' in value:
                file = value['file']
                if not isinstance(file, str) or file not in files:
                    raise ValueError('Evidence references an unknown source file')
                if file not in line_counts:
                    line_counts[file] = len(files[file].splitlines())
                line = value.get('line')
                if (type(line) is not int or not 1 <= line <= line_counts[file]
                        or not isinstance(value.get('source_id'), str) or not value['source_id']):
                    raise ValueError('Evidence requires an in-range line and stable source_id')
                if grounder is not None and len(ungrounded) < 4:
                    grounder.check(value, pointer, ungrounded)
            for key, child in value.items():
                if key in {'coverage', 'origin_ref'} or page_root and key == 'content_sources':
                    continue
                if isinstance(child, (list, dict)):
                    evidence(child, pointer=pointer + '/' + key)

    def records(items, required, pointer, hint=None):
        hint = hint or 'Return observation objects with the required fields and source locations.'
        if not isinstance(items, list):
            raise ObservationValidationError([{'pointer': pointer, 'expected': 'list of observation objects',
                'actual_type': type(items).__name__, 'hint': hint}])
        issues = []
        for index, item in enumerate(items):
            if not isinstance(item, dict) or not required <= set(item):
                issue = {'pointer': f'{pointer}/{index}', 'expected': 'observation object',
                         'actual_type': type(item).__name__, 'hint': hint}
                if isinstance(item, dict):
                    issue['missing_fields'] = sorted(required - set(item))
                else:
                    issue['required_fields'] = sorted(required)
                issues.append(issue)
                if len(issues) == 4:
                    break
        if issues:
            raise ObservationValidationError(issues)

    ref = {'file', 'line', 'source_id'}
    for page_index, page in enumerate(report['pages']):
        pointer = f'/pages/{page_index}'
        if not isinstance(page['template'], str) or page['template'] not in files or PurePosixPath(page['template']).is_absolute():
            raise ValueError('Page template must reference a captured source file')
        identity = (page['url'], page['template'])
        if identity in identities:
            raise ValueError('Duplicate page identity')
        identities.add(identity)
        if type(page['is_dynamic']) is not bool:
            raise ValueError('is_dynamic must be a boolean')
        coverage = page['coverage']
        if (coverage.get('complete') is not False or not isinstance(coverage.get('limitations'), list)
                or not coverage['limitations']):
            raise ValueError('Static coverage requires complete=false and explicit limitations')
        if relationships(page.get('content_sources', []), page_index):
            limitation = 'Some content-source selectors are unverified or invalid; inspect content_sources.pointer_validation. They are not verified observation locations.'
            if limitation not in coverage['limitations']:
                coverage['limitations'].append(limitation)
        if category == 'content':
            records(page['blocks'], ref | {'text', 'tag', 'section'}, pointer + '/blocks')
            issues = []
            for block_index, block in enumerate(page['blocks']):
                for field in ('text', 'tag', 'section'):
                    if not isinstance(block[field], str):
                        hints = {'text': 'Extract literal text only. Keep unresolved data in coverage limitations; do not stringify objects or null.',
                                 'tag': 'Use the literal element tag or a documented source-text label.',
                                 'section': 'Use a semantic tag string (for example main or nav); use body when no semantic parent is established. Store hierarchy objects in sections/section_id, not section.'}
                        issues.append({'pointer': f'/pages/{page_index}/blocks/{block_index}/{field}',
                            'expected': 'string', 'actual_type': type(block[field]).__name__,
                            'file': str(block.get('file', ''))[:180], 'line': block.get('line'),
                            'hint': hints[field]})
                        if len(issues) == 4:
                            break
                if len(issues) == 4:
                    break
            if issues:
                raise ObservationValidationError(issues)
            if type(page['word_count']) is not int or page['word_count'] != sum(len(b['text'].split()) for b in page['blocks']):
                raise ValueError('Content word_count must match extracted blocks')
            for field in ('link_refs', 'media_refs'):
                records(page[field], {'source_id', 'report'}, pointer + '/' + field)
        elif category == 'page_structure':
            records(page['headings'], ref | {'level', 'text'}, pointer + '/headings')
            if any(type(h['level']) is not int or not 1 <= h['level'] <= 6 for h in page['headings']):
                raise ValueError('Heading levels must be integers from 1 to 6')
            if any(not isinstance(h['text'], str) for h in page['headings']):
                raise ValueError('Heading text must be a string')
            records(page['landmarks'], ref | {'landmarks'}, pointer + '/landmarks')
        elif category == 'internal_links':
            records(page['links'], ref | {'href', 'anchor_text', 'target_url', 'kind', 'rel', 'target'}, pointer + '/links')
            for link in page['links']:
                if not all(isinstance(link[key], str) for key in ('href', 'anchor_text', 'target_url', 'kind')):
                    raise ValueError('Link destinations and labels must be strings')
        elif category == 'metadata':
            records(page['declarations'], ref, pointer + '/declarations')
            for declaration in page['declarations']:
                for key in ('title', 'description', 'name', 'property', 'content', 'href'):
                    if declaration.get(key) is not None and not isinstance(declaration[key], str):
                        raise ValueError('Metadata literal values must be strings or null')
            if not isinstance(page['inheritance_note'], str):
                raise ValueError('Metadata inheritance_note must be a string')
        elif category == 'media':
            records(page['items'], ref | {'kind', 'src', 'size_review'}, pointer + '/items')
        elif category == 'urls':
            if type(page['depth']) is not int or page['depth'] < 0:
                raise ValueError('URL depth must be a nonnegative integer')
            records(page['query_links'], ref | {'href', 'target_url'}, pointer + '/query_links')
        elif category == 'eeat':
            records(page['signals'], {'signal', 'status', 'evidence'}, pointer + '/signals')
            evidence_hint = ('Use source-backed objects with file, line, source_id and literal text. '
                             'For not_observed_in_static_content use evidence=[]; put absence explanations '
                             'in interpretation or coverage.limitations. Never fabricate source locations.')
            for signal_index, signal in enumerate(page['signals']):
                evidence_pointer = f'{pointer}/signals/{signal_index}/evidence'
                if (not isinstance(signal['signal'], str) or signal['status'] not in
                        {'candidate_evidence', 'not_observed_in_static_content'}):
                    raise ValueError('Invalid candidate signal name or status')
                records(signal['evidence'], ref | {'text'}, evidence_pointer, evidence_hint)
                for evidence_index, item in enumerate(signal['evidence']):
                    if not isinstance(item['text'], str):
                        raise ObservationValidationError([{'pointer': f'{evidence_pointer}/{evidence_index}/text',
                            'expected': 'string', 'actual_type': type(item['text']).__name__, 'hint': evidence_hint}])
            records(page['trust_links'], ref | {'href'}, pointer + '/trust_links')
            records(page['citations'], ref | {'href'}, pointer + '/citations')
            if not isinstance(page['interpretation'], str):
                raise ValueError('E-E-A-T interpretation must be a string')
        evidence(page, page_root=True, pointer=pointer)
    if ungrounded:
        raise ObservationValidationError(ungrounded[:4])
    json.dumps(report, allow_nan=False)
    return report


def record_key(item):
    if isinstance(item, dict):
        column = item.get('column', item.get('col'))
        return (0, str(item.get('file', '')), item.get('line') if type(item.get('line')) is int else -1,
                column if type(column) is int else -1, str(item.get('source_id', '')),
                json.dumps(item, sort_keys=True, ensure_ascii=False, default=str))
    return (1, '', -1, -1, '', json.dumps(item, sort_keys=True, ensure_ascii=False, default=str))


def canonical(value):
    """Order-independent form: source-located records sorted by (file, line, column, source_id)."""
    if isinstance(value, dict):
        return {key: canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        items = [canonical(item) for item in value]
        if items and all(isinstance(item, dict) and ('file' in item or 'source_id' in item) for item in items):
            return sorted(items, key=record_key)
        return items
    return value


def verify_and_extract(category, source, snapshot, *, routes=None, index=None, compact=None):
    """Trial first; only then run the verified script and accept the same output (up to record order)."""
    compact = compact if compact is not None else (compact_index(index) if index else None)
    trial = validate_observations(category, execute_script(source, snapshot, routes=routes, index=compact), snapshot, index=index)
    result = validate_observations(category, execute_script(source, snapshot, routes=routes, index=compact), snapshot, index=index)
    if result != trial:
        if canonical(result) != canonical(trial):
            raise ValueError('Extractor results changed between verification and extraction')
        result = canonical(result)
        result['extraction']['record_order'] = 'canonicalised to source order (file, line, column); run order was not repeatable'
    result['extraction'].update(
        mode='static-source', engine='skill-guided-agent', verification='passed',
        script_sha256=hashlib.sha256(source.encode()).hexdigest(),
        snapshot_sha256=snapshot['sha256'],
        snapshot_limitations=snapshot['limitations'],
        grounding='values checked within ±3 lines of cited source or via resolved value_origin/origin_ref',
    )
    return result
