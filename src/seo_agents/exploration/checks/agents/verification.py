"""Reject unsafe scripts, invalid reports and non-repeatable extraction."""

import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys

from seo_agents.exploration.checks.agents.script_policy import validate_script
from seo_agents.exploration.checks.tools.contracts import validate_report


def execute_script(source, snapshot, timeout=12):
    validate_script(source)
    payload = json.dumps({'source': source, 'files': snapshot['files'], 'assets': snapshot['assets']})
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
        return json.loads(process.stdout)
    except (ValueError, RecursionError) as error:
        raise ValueError('Extractor did not return valid JSON') from error


def validate_observations(category, report, snapshot):
    validate_report(category, report)
    files = snapshot['files']
    if not report['pages']:
        raise ValueError('No pages discovered; investigate entry points instead of accepting an empty inventory')
    identities = set()

    def evidence(value):
        if isinstance(value, list):
            for item in value:
                evidence(item)
        elif isinstance(value, dict):
            if 'file' in value:
                file = value['file']
                if not isinstance(file, str) or file not in files:
                    raise ValueError('Evidence references an unknown source file')
                line = value.get('line')
                if (type(line) is not int or not 1 <= line <= len(files[file].splitlines())
                        or not isinstance(value.get('source_id'), str) or not value['source_id']):
                    raise ValueError('Evidence requires an in-range line and stable source_id')
            for child in value.values():
                if isinstance(child, (list, dict)):
                    evidence(child)

    def records(items, required):
        for item in items:
            if not isinstance(item, dict) or not required <= set(item):
                raise ValueError('Missing observation fields: ' + str(sorted(required)))

    ref = {'file', 'line', 'source_id'}
    for page in report['pages']:
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
        if category == 'content':
            records(page['blocks'], ref | {'text', 'tag', 'section'})
            for block in page['blocks']:
                if not all(isinstance(block[key], str) for key in ('text', 'tag', 'section')):
                    raise ValueError('Content text, tag and section must be strings')
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
        evidence(page)
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
