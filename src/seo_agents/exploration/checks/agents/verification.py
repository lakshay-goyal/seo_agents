"""Reject unsafe scripts, invalid reports and non-repeatable extraction."""

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
import subprocess
import sys

from seo_agents.exploration.checks.agents.script_policy import validate_script
from seo_agents.exploration.checks.agents.context import strict_json
from seo_agents.exploration.checks.tools.contracts import validate_report


class ObservationValidationError(ValueError):
    """Bounded field-level feedback, without dumping extracted client content."""
    def __init__(self, issues):
        self.issues = issues
        super().__init__('Invalid content observations: ' + json.dumps(issues, ensure_ascii=True))


def execute_script(source, snapshot, timeout=12):
    validate_script(source)
    payload = json.dumps({'source': source, 'files': snapshot['files'], 'assets': snapshot['assets']}, ensure_ascii=False)
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
                'meaning': 'Source values accessed, not proof of semantic understanding',
                'html_parsing': output['html_parsing']}
        return report
    except (ValueError, RecursionError) as error:
        raise ValueError('Extractor did not return valid JSON') from error


def validate_observations(category, report, snapshot):
    validate_report(category, report)
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

    def evidence(value, *, page_root=False):
        if isinstance(value, list):
            for item in value:
                evidence(item)
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
            for key, child in value.items():
                if key == 'coverage' or page_root and key == 'content_sources':
                    continue
                if isinstance(child, (list, dict)):
                    evidence(child)

    def records(items, required):
        for item in items:
            if not isinstance(item, dict) or not required <= set(item):
                raise ValueError('Missing observation fields: ' + str(sorted(required)))

    ref = {'file', 'line', 'source_id'}
    for page_index, page in enumerate(report['pages']):
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
            records(page['blocks'], ref | {'text', 'tag', 'section'})
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
                records(page[field], {'source_id', 'report'})
        elif category == 'page_structure':
            records(page['headings'], ref | {'level', 'text'})
            if any(type(h['level']) is not int or not 1 <= h['level'] <= 6 for h in page['headings']):
                raise ValueError('Heading levels must be integers from 1 to 6')
            if any(not isinstance(h['text'], str) for h in page['headings']):
                raise ValueError('Heading text must be a string')
            records(page['landmarks'], ref | {'landmarks'})
        elif category == 'internal_links':
            records(page['links'], ref | {'href', 'anchor_text', 'target_url', 'kind', 'rel', 'target'})
            for link in page['links']:
                if not all(isinstance(link[key], str) for key in ('href', 'anchor_text', 'target_url', 'kind')):
                    raise ValueError('Link destinations and labels must be strings')
        elif category == 'metadata':
            records(page['declarations'], ref)
            for declaration in page['declarations']:
                for key in ('title', 'description', 'name', 'property', 'content', 'href'):
                    if declaration.get(key) is not None and not isinstance(declaration[key], str):
                        raise ValueError('Metadata literal values must be strings or null')
            if not isinstance(page['inheritance_note'], str):
                raise ValueError('Metadata inheritance_note must be a string')
        elif category == 'media':
            records(page['items'], ref | {'kind', 'src', 'size_review'})
        elif category == 'urls':
            if type(page['depth']) is not int or page['depth'] < 0:
                raise ValueError('URL depth must be a nonnegative integer')
            records(page['query_links'], ref | {'href', 'target_url'})
        elif category == 'eeat':
            records(page['signals'], {'signal', 'status', 'evidence'})
            for signal in page['signals']:
                if (not isinstance(signal['signal'], str) or signal['status'] not in
                        {'candidate_evidence', 'not_observed_in_static_content'}):
                    raise ValueError('Invalid candidate signal name or status')
                if not isinstance(signal['evidence'], list):
                    raise ValueError('Signal evidence must be a list')
                records(signal['evidence'], ref | {'text'})
            records(page['trust_links'], ref | {'href'})
            records(page['citations'], ref | {'href'})
            if not isinstance(page['interpretation'], str):
                raise ValueError('E-E-A-T interpretation must be a string')
        evidence(page, page_root=True)
    json.dumps(report, allow_nan=False)
    return report


def verify_and_extract(category, source, snapshot):
    """Trial first; only then run the verified script and accept identical output."""
    trial = validate_observations(category, execute_script(source, snapshot), snapshot)
    result = validate_observations(category, execute_script(source, snapshot), snapshot)
    if result != trial:
        raise ValueError('Extractor results changed between verification and extraction')
    result['extraction'].update(
        mode='static-source', engine='skill-guided-agent', verification='passed',
        script_sha256=hashlib.sha256(source.encode()).hexdigest(),
        snapshot_sha256=snapshot['sha256'],
        snapshot_limitations=snapshot['limitations'],
    )
    return result
