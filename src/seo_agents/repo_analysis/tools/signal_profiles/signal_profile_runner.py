"""Load deterministic skill profiles (`profiles/<name>.json`) and apply their signals.

Profiles are configuration, not prompts: every signal is a scoped regex with a
precision label and match/no-match examples (executed by tests/test_skill_profiles.py).
Signals are framework-agnostic: one signal lists every ecosystem's variant of a
concern (React, Vue, Svelte, Astro, Angular, Liquid, PHP/WordPress, Blade, ERB,
Jinja/Django, Hugo/Go templates, plain HTML), so no framework detection is needed.
`skills/<name>/SKILL.md` is human documentation only and is not read at runtime.
"""

from bisect import bisect_right
from functools import lru_cache
import hashlib
import json
import re

from seo_agents.lib.signal_profiles import SIGNAL_PROFILE_DOCS_DIR, SIGNAL_PROFILES_DIR
from seo_agents.repo_analysis.tools.repository.source_index import glob_match, language_of

PROFILES_DIR = SIGNAL_PROFILES_DIR
SKILLS_DIR = SIGNAL_PROFILE_DOCS_DIR
MAX_RECORDS_PER_SIGNAL = 150
MAX_RECORDS_PER_FILE = 25

LANGUAGE_GROUPS = {
    '@markup': {'html', 'vue', 'svelte', 'astro', 'mdx', 'erb', 'php', 'blade', 'twig', 'liquid', 'nunjucks',
                'handlebars', 'ejs', 'pug', 'razor', 'jinja'},
    '@scripts': {'javascript', 'typescript'},
    '@content': {'markdown', 'mdx'},
    '@data': {'json', 'yaml', 'toml'},
    '@styles': {'css', 'scss', 'sass', 'less'},
    '@server': {'python', 'ruby', 'php', 'blade', 'go', 'java', 'kotlin', 'csharp', 'rust', 'javascript', 'typescript'},
    '@config': {'json', 'yaml', 'toml', 'server-config', 'javascript', 'typescript', 'other'},
}
LANGUAGE_GROUPS['@templates'] = LANGUAGE_GROUPS['@markup'] | LANGUAGE_GROUPS['@scripts']
# Repository prose is documentation, not site behaviour.
DOC_FILES = re.compile(r'(?:^|/)(?:README|CHANGELOG|CHANGES|HISTORY|LICENSE|LICENCE|CONTRIBUTING|CODE_OF_CONDUCT|SECURITY|AUTHORS)[^/]*$', re.I)
SIGNAL_KEYS = {'id', 'label', 'regex', 'precision', 'examples', 'files_only', 'glob', 'languages', 'exclude', 'requires',
               'exclude_comments', 'include_docs', 'ignore_case', 'block', 'value_group', 'mode', 'tags'}
PROFILE_KEYS = {'$schema', 'name', 'framework_agnostic', 'execution_hint', 'framework_markers', 'weights', 'limits',
                'conventions', 'declarations', 'signals', 'gaps'}
CONVENTION_KEYS = {'pattern', 'kind', 'adapter', 'exclude', 'dot_segments', 'requires_path', 'route_map', 'route_template', 'route_default'}
DECLARATION_KEYS = {'glob', 'regex', 'kind', 'adapter', 'requires', 'parser', 'resource'}
GAP_KEYS = {'id', 'label', 'when_none_of', 'only_if_any'}


class ProfileError(ValueError):
    """A profile violates profiles/schema.json."""


def expand_languages(names):
    result = set()
    for name in names or ():
        result |= LANGUAGE_GROUPS.get(name, {name})
    return result


def validate_profile(profile, source='profile'):
    """Hand-written check of profiles/schema.json (no jsonschema dependency)."""
    def fail(message):
        raise ProfileError(source + ': ' + message)

    if not isinstance(profile, dict) or set(profile) - PROFILE_KEYS:
        fail('unexpected top-level keys ' + str(sorted(set(profile) - PROFILE_KEYS)))
    for key in ('name', 'signals', 'gaps'):
        if key not in profile:
            fail('missing ' + key)
    if not re.fullmatch(r'[a-z][a-z0-9-]*', profile['name']):
        fail('invalid name')
    ids = set()
    for signal in profile['signals']:
        sid = signal.get('id', '?')
        extra = set(signal) - SIGNAL_KEYS
        if extra:
            fail(sid + ': unexpected keys ' + str(sorted(extra)))
        for key in ('id', 'label', 'regex', 'precision', 'examples'):
            if key not in signal:
                fail(sid + ': missing ' + key)
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', sid) or sid in ids:
            fail(sid + ': invalid or duplicate id')
        ids.add(sid)
        if signal['precision'] not in {'high', 'medium', 'low'}:
            fail(sid + ': precision must be high|medium|low')
        if not (signal.get('files_only') or signal.get('glob') or signal.get('languages')):
            fail(sid + ': needs a scope (files_only, glob or languages)')
        if 'glob' in signal and not (isinstance(signal['glob'], list) and all(isinstance(g, str) for g in signal['glob'])):
            fail(sid + ': glob must be a list of strings')
        if 'languages' in signal and not (isinstance(signal['languages'], list) and all(isinstance(g, str) for g in signal['languages'])):
            fail(sid + ': languages must be a list of strings')
        examples = signal['examples']
        if (not isinstance(examples, dict) or set(examples) != {'match', 'no_match'}
                or not all(isinstance(examples[k], list) and examples[k] and all(isinstance(v, str) for v in examples[k])
                           for k in ('match', 'no_match'))):
            fail(sid + ': examples must have non-empty match/no_match string lists')
        for key in ('regex', 'exclude', 'requires'):
            if key in signal:
                try:
                    re.compile(signal[key])
                except re.error as error:
                    fail(sid + ': invalid ' + key + ': ' + str(error))
    for gap in profile['gaps']:
        if set(gap) - GAP_KEYS or not {'id', 'label', 'when_none_of'} <= set(gap):
            fail('gap ' + str(gap.get('id')) + ': invalid keys')
        for ref in gap['when_none_of'] + gap.get('only_if_any', []):
            if not ref.startswith('@') and ref not in ids:
                fail('gap ' + gap['id'] + ': unknown signal ' + ref)
    for convention in profile.get('conventions', []):
        if set(convention) - CONVENTION_KEYS or not {'pattern', 'kind', 'adapter'} <= set(convention):
            fail('convention ' + convention.get('pattern', '?') + ': invalid keys')
        re.compile(convention['pattern'])
    for declaration in profile.get('declarations', []):
        if set(declaration) - DECLARATION_KEYS or not {'glob', 'regex', 'kind', 'adapter'} <= set(declaration):
            fail('declaration ' + declaration.get('regex', '?') + ': invalid keys')
        re.compile(declaration['regex'])
    return profile


@lru_cache(maxsize=1)
def load_signal_profiles():
    """agent name -> {profile, skill_file, doc_file, sha256}. Malformed profiles fail loudly."""
    skills = {}
    for path in sorted(PROFILES_DIR.glob('*.json')):
        if path.name == 'schema.json':
            continue
        raw = path.read_bytes()
        profile = validate_profile(json.loads(raw), path.name)
        doc = SKILLS_DIR / profile['name'] / 'SKILL.md'
        skills[profile['name']] = {
            'profile': profile,
            'skill_file': path.relative_to(PROFILES_DIR.parent).as_posix(),
            'doc_file': doc.relative_to(SKILLS_DIR.parent).as_posix() if doc.exists() else None,
            'sha256': hashlib.sha256(raw).hexdigest()}
    return skills


def signal_profile(name):
    return load_signal_profiles()[name]


def signal_profiles_identity():
    return {name: s['sha256'] for name, s in load_signal_profiles().items()}


def signal_paths(index, signal):
    """Captured paths in scope for a signal (tests are already excluded by the index)."""
    globs = signal.get('glob') or []
    if isinstance(globs, str):
        globs = [globs]
    languages = expand_languages(signal.get('languages'))
    exclude = re.compile(signal['exclude']) if signal.get('exclude') else None
    paths = []
    for path in index.paths():
        if globs and not any(glob_match(path, g) for g in globs):
            continue
        if languages and language_of(path) not in languages:
            continue
        if exclude and exclude.search(path):
            continue
        if not signal.get('include_docs') and not signal.get('files_only') and (
                DOC_FILES.search(path) or (hasattr(index, 'is_doc') and index.is_doc(path))):
            continue
        paths.append(path)
    return paths


_paths_for = signal_paths  # backwards-compatible private name


HASH_COMMENT_LANGUAGES = {'python', 'ruby', 'yaml', 'toml', 'server-config', 'text', 'other', 'dockerfile', 'makefile',
                          'procfile', 'gemfile'}
SLASH_COMMENT_LANGUAGES = {'javascript', 'typescript', 'java', 'kotlin', 'csharp', 'go', 'rust', 'dart', 'scss', 'less'}
CSS_LANGUAGES = {'css', 'sass'}
MARKUP_LANGUAGES = {'html', 'vue', 'svelte', 'astro', 'mdx', 'markdown', 'erb', 'php', 'blade', 'twig', 'liquid',
                    'nunjucks', 'handlebars', 'ejs', 'pug', 'razor', 'jinja', 'xml'}
# Template-language comment delimiters. Svelte `{#if}` / Handlebars `{{#each}}` are blocks, not comments.
TEMPLATE_COMMENTS = {
    'twig': (('{#', '#}'),), 'nunjucks': (('{#', '#}'),), 'jinja': (('{#', '#}'),),
    'liquid': (('{% comment %}', '{% endcomment %}'), ('{%- comment -%}', '{%- endcomment -%}'), ('{% comment -%}', '{%- endcomment %}')),
    'handlebars': (('{{!--', '--}}'), ('{{!', '}}')), 'blade': (('{{--', '--}}'),), 'erb': (('<%#', '%>'),),
    'ejs': (('<%#', '%>'),), 'razor': (('@*', '*@'),),
}
SCRIPT_BLOCK = re.compile(r'<script\b[^>]*>(.*?)</script\s*>|<\?php\b(.*?)(?:\?>|\Z)', re.S | re.I)
STYLE_BLOCK = re.compile(r'<style\b[^>]*>(.*?)</style\s*>', re.S | re.I)
ASTRO_FRONTMATTER = re.compile(r'\A---\s*\n(.*?)\n---', re.S)


def _code_comments(text, start, end, *, slash=True, hash_=False, block=True):
    """Comment ranges in [start, end) of C-like or hash-comment code. Strings are skipped; '/" strings
    end at a newline (so prose apostrophes in JSX text cannot swallow following lines)."""
    ranges, i = [], start
    while i < end:
        char = text[i]
        if char in '"\'`':
            quote, i = char, i + 1
            while i < end and text[i] != quote:
                if text[i] == '\\':
                    i += 1
                elif text[i] == '\n' and quote != '`':
                    break
                i += 1
            i += 1
        elif block and text.startswith('/*', i):
            close = text.find('*/', i + 2, end)
            stop = end if close < 0 else close + 2
            ranges.append((i, stop))
            i = stop
        elif (slash and text.startswith('//', i) and not (i > 0 and text[i - 1] == ':')) or (hash_ and char == '#'):
            close = text.find('\n', i, end)
            stop = end if close < 0 else close
            ranges.append((i, stop))
            i = stop
        else:
            i += 1
    return ranges


def _delimited(text, pairs, start=0, end=None):
    end = len(text) if end is None else end
    ranges = []
    for opener, closer in pairs:
        position = text.find(opener, start, end)
        while position >= 0:
            close = text.find(closer, position + len(opener), end)
            stop = end if close < 0 else close + len(closer)
            ranges.append((position, stop))
            position = text.find(opener, stop, end)
    return ranges


@lru_cache(maxsize=256)
def comment_ranges(text, language=None):
    """Sorted (start, end) comment spans using the real comment syntax of `language`."""
    if language in SLASH_COMMENT_LANGUAGES:
        ranges = _code_comments(text, 0, len(text))
    elif language in CSS_LANGUAGES:
        ranges = _code_comments(text, 0, len(text), slash=False)
    elif language == 'python':
        ranges = _code_comments(text, 0, len(text), slash=False, hash_=True, block=False)
    elif language in HASH_COMMENT_LANGUAGES:
        ranges = [(m.start(1), m.end(1)) for m in re.finditer(r'(?m)^[ \t]*(#.*)$', text)]
        ranges += [(m.start(1), m.end(1)) for m in re.finditer(r'(?m)(?<=\s)(#.*)$', text)
                   if language in {'ruby', 'yaml', 'toml', 'server-config', 'text'}]
    elif language in MARKUP_LANGUAGES:
        ranges = _delimited(text, (('<!--', '-->'),)) + _delimited(text, TEMPLATE_COMMENTS.get(language, ()))
        for match in SCRIPT_BLOCK.finditer(text):
            group = 1 if match.group(1) is not None else 2
            ranges += _code_comments(text, match.start(group), match.end(group), hash_=group == 2)
        for match in STYLE_BLOCK.finditer(text):
            ranges += _code_comments(text, match.start(1), match.end(1), slash=False)
        if language in {'astro', 'mdx'}:
            front = ASTRO_FRONTMATTER.match(text)
            if front:
                ranges += _code_comments(text, front.start(1), front.end(1))
    else:
        ranges = []
    return tuple(sorted(ranges))


def in_comment(text, offset, language=None):
    """True when `offset` sits inside a comment of the file's own language (`//`, `/* */`, `<!-- -->`,
    `{# #}` only for Jinja-family, `{% comment %}` for Liquid, `#` only for hash-comment languages).
    Strings are skipped, so `'src/**/*.js'` and Svelte `{#if}` do not open a comment."""
    ranges = comment_ranges(text, language)
    position = bisect_right(ranges, (offset, float('inf'))) - 1
    return position >= 0 and ranges[position][0] <= offset < ranges[position][1]


def run_signal(index, signal, stats=None):
    """Records for one signal. `stats` (optional dict) receives total_matches/truncated."""
    flags = re.M | (re.I if signal.get('ignore_case') else 0)
    regex = re.compile(signal['regex'], flags)
    records, total = [], 0
    if signal.get('files_only'):
        scoped = signal_paths(index, signal) if (signal.get('glob') or signal.get('languages') or signal.get('exclude')) else index.paths()
        for path in scoped:
            if regex.search(path):
                total += 1
                if len(records) < MAX_RECORDS_PER_SIGNAL:
                    extra = {'mode': signal['mode']} if signal.get('mode') else {}
                    records.append(index.evidence_lines(signal['id'], path, 1, len(index.lines(path)),
                                                        label=signal['label'], match='path', **extra))
        for path in index.assets:
            if regex.search(path):
                total += 1
                if len(records) < MAX_RECORDS_PER_SIGNAL:
                    records.append({'id': signal['id'] + ':' + path, 'kind': signal['id'], 'label': signal['label'],
                                    'location': {'file': path, 'asset': True}, 'asset': index.assets[path]})
    else:
        requires = re.compile(signal['requires'], re.M) if signal.get('requires') else None
        for path in signal_paths(index, signal):
            text = index.files[path]
            if requires and not requires.search(text):
                continue
            kept = 0
            for match in regex.finditer(text):
                if signal.get('exclude_comments') and in_comment(text, match.start(), language_of(path)):
                    continue
                total += 1
                if kept >= MAX_RECORDS_PER_FILE or len(records) >= MAX_RECORDS_PER_SIGNAL:
                    continue
                kept += 1
                extra = {'label': signal['label'], 'matched_text': match.group(0)[:300]}
                if signal.get('value_group') and match.lastindex and match.group(signal['value_group']):
                    extra['value'] = match.group(signal['value_group'])
                if signal.get('tags'):
                    extra['tags'] = signal['tags']
                if signal.get('mode'):
                    extra['mode'] = signal['mode']
                records.append(index.evidence(signal['id'], path, match.start(), match.end(),
                                              block=signal.get('block', False), **extra))
    if stats is not None:
        stats.update(total_matches=total, truncated=total > len(records))
    return records


def run_signal_profile(index, name, *, extra_found=()):
    """Apply one agent skill. `extra_found` adds pseudo-signal ids proven by Python extractors."""
    entry = signal_profile(name)
    profile = entry['profile']
    signals, found = {}, set(extra_found)
    for signal in profile.get('signals', []):
        stats = {}
        records = run_signal(index, signal, stats)
        signals[signal['id']] = {'label': signal['label'], 'precision': signal['precision'], 'count': len(records),
                                 'total_matches': stats['total_matches'], 'truncated': stats['truncated'],
                                 'files': sorted({r['location']['file'] for r in records}), 'records': records}
        if records:
            found.add(signal['id'])
    result = {'agent': name, 'skill_file': entry['skill_file'], 'skill_sha256': entry['sha256'],
              'signals': signals, 'gaps': [], 'found_signals': sorted(found)}
    return evaluate_gaps(result)


def evaluate_gaps(result, extra_found=()):
    """(Re)compute gaps, optionally with pseudo-signals proven by Python extractors."""
    found = set(result['found_signals']) | set(extra_found)
    result['found_signals'] = sorted(found)
    result['gaps'] = []
    for gap in signal_profile(result['agent'])['profile'].get('gaps', []):
        if gap.get('only_if_any') and not found & set(gap['only_if_any']):
            continue
        if not found & set(gap['when_none_of']):
            result['gaps'].append({'id': gap['id'], 'label': gap['label'], 'checked_signals': gap['when_none_of'],
                                   'note': 'Lead only: runtime, CMS or hosting configuration outside the repository may provide it.'})
    return result


def records_of(result, *signal_ids):
    return [r for sid in signal_ids for r in result['signals'].get(sid, {}).get('records', [])]


class SignalProfileRunner:
    """Tool: apply one regex signal profile (lib/signal_profiles/profiles/<name>.json) to a SourceIndex."""

    name = 'signal_profile_runner'

    def run(self, index, profile_name, *, extra_found=()):
        return run_signal_profile(index, profile_name, extra_found=extra_found)

    def records(self, result, *signal_ids):
        return records_of(result, *signal_ids)

    def evaluate_gaps(self, result, extra_found=()):
        return evaluate_gaps(result, extra_found)

    def profiles(self):
        return load_signal_profiles()

    def identity(self):
        return signal_profiles_identity()
