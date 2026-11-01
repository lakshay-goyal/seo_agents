"""Load per-agent SKILL.md profiles and apply their signals to any repository.

Skills are framework-agnostic: a signal's regex lists every ecosystem's variant of
one concern, so no framework detection is needed to run an agent.
"""

from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

SKILLS_DIR = Path(__file__).parents[1] / 'skills'
PROFILE_BLOCK = re.compile(r'```json profile\s*\n(.*?)\n```', re.S)
MAX_RECORDS_PER_SIGNAL = 150
MAX_RECORDS_PER_FILE = 25


@lru_cache(maxsize=1)
def load_skills():
    """agent name -> {profile, guidance, skill_file, sha256}. Malformed skills fail loudly."""
    skills = {}
    for path in sorted(SKILLS_DIR.glob('*/SKILL.md')):
        text = path.read_text()
        match = PROFILE_BLOCK.search(text)
        if not match:
            raise ValueError('Skill has no ```json profile block: ' + str(path))
        profile = json.loads(match.group(1))
        for signal in profile.get('signals', []):
            re.compile(signal['regex'])  # fail at load time, not mid-analysis
        body = text.split('---', 2)[-1]
        skills[profile['name']] = {
            'profile': profile, 'guidance': PROFILE_BLOCK.sub('', body).strip(),
            'skill_file': path.relative_to(SKILLS_DIR.parent).as_posix(),
            'sha256': hashlib.sha256(text.encode()).hexdigest()}
    return skills


def skill(name):
    return load_skills()[name]


def skills_identity():
    return {name: s['sha256'] for name, s in load_skills().items()}


def _paths_for(index, signal):
    if signal.get('glob'):
        from seo_agents.exploration.repo_analysis.tools.source_index import glob_match
        return [p for p in index.paths() if glob_match(p, signal['glob'])]
    return index.paths()


def run_signal(index, signal):
    flags = re.M | (re.I if signal.get('ignore_case') else 0)
    regex = re.compile(signal['regex'], flags)
    records = []
    if signal.get('files_only'):
        for path in index.paths():
            if regex.search(path) and len(records) < MAX_RECORDS_PER_SIGNAL:
                extra = {'mode': signal['mode']} if signal.get('mode') else {}
                records.append(index.evidence_lines(signal['id'], path, 1, len(index.lines(path)),
                                                    label=signal['label'], match='path', **extra))
        for path in index.assets:
            if regex.search(path) and len(records) < MAX_RECORDS_PER_SIGNAL:
                records.append({'id': signal['id'] + ':' + path, 'kind': signal['id'], 'label': signal['label'],
                                'location': {'file': path, 'asset': True}, 'asset': index.assets[path]})
        return records
    for path in _paths_for(index, signal):
        for match in list(regex.finditer(index.files[path]))[:MAX_RECORDS_PER_FILE]:
            if len(records) >= MAX_RECORDS_PER_SIGNAL:
                return records
            extra = {'label': signal['label'], 'matched_text': match.group(0)[:300]}
            if signal.get('value_group') and match.lastindex and match.group(signal['value_group']):
                extra['value'] = match.group(signal['value_group'])
            if signal.get('tags'):
                extra['tags'] = signal['tags']
            if signal.get('mode'):
                extra['mode'] = signal['mode']
            records.append(index.evidence(signal['id'], path, match.start(), match.end(),
                                          block=signal.get('block', False), **extra))
    return records


def run_skill(index, name, *, extra_found=()):
    """Apply one agent skill. `extra_found` adds pseudo-signal ids proven by Python extractors."""
    entry = skill(name)
    profile = entry['profile']
    signals, found = {}, set(extra_found)
    for signal in profile.get('signals', []):
        records = run_signal(index, signal)
        signals[signal['id']] = {'label': signal['label'], 'count': len(records),
                                 'files': sorted({r['location']['file'] for r in records}), 'records': records}
        if records:
            found.add(signal['id'])
    result = {'agent': name, 'skill_file': entry['skill_file'], 'skill_sha256': entry['sha256'],
              'edit_guidance': profile.get('edit_guidance'), 'signals': signals, 'gaps': [],
              'found_signals': sorted(found)}
    return evaluate_gaps(result)


def evaluate_gaps(result, extra_found=()):
    """(Re)compute gaps, optionally with pseudo-signals proven by Python extractors."""
    found = set(result['found_signals']) | set(extra_found)
    result['found_signals'] = sorted(found)
    result['gaps'] = []
    for gap in skill(result['agent'])['profile'].get('gaps', []):
        if gap.get('only_if_any') and not found & set(gap['only_if_any']):
            continue
        if not found & set(gap['when_none_of']):
            result['gaps'].append({'id': gap['id'], 'label': gap['label'], 'checked_signals': gap['when_none_of'],
                                   'note': 'Lead only: runtime, CMS or hosting configuration outside the repository may provide it.'})
    return result


def records_of(result, *signal_ids):
    return [r for sid in signal_ids for r in result['signals'].get(sid, {}).get('records', [])]
