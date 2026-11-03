"""Locale routing, i18n libraries, hreflang alternates and <html lang>.

`html_lang` keeps literal values only; `lang={expr}` / `lang="{{ ... }}"` are recorded as
`value_origin: expression` with value null. `i18n_config` reads {locales, default, strategy} from the
framework config (Next, Nuxt, Astro, next-intl/paraglide routing, Hugo, Jekyll Polyglot).
"""

import json
import re
import tomllib

from seo_agents.repo_analysis.tools.signal_profiles.signal_profile_runner import records_of, run_signal_profile
from seo_agents.repo_analysis.agents.base_agent import RepoAnalysisAgent

LANG_ATTR = re.compile(r'''\blang\s*=\s*(?:"([^"]*)"|'([^']*)'|\{([^}]*)\})''', re.I)
LOCALE_CODE = re.compile(r'^[a-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*$')


def _strings(array_text):
    codes = re.findall(r'''\bcode\s*:\s*['"`]([\w-]+)['"`]''', array_text)
    if codes:
        return codes
    return [v for v in re.findall(r'''['"`]([\w-]+)['"`]''', array_text) if LOCALE_CODE.match(v)]


def _array_after(text, key):
    match = re.search(r'\b' + key + r'''\s*[:=]\s*\[''', text)
    if not match:
        return None
    depth, position = 1, match.end()
    while position < len(text) and depth:
        depth += {'[': 1, ']': -1}.get(text[position], 0)
        position += 1
    return text[match.end():position - 1]


def _scalar(text, key):
    match = re.search(r'\b' + key + r'''\s*[:=]\s*['"`]?([\w-]+)''', text)
    return match.group(1) if match else None


def html_lang_values(index, records):
    values = []
    for record in records:
        code = record.get('code') or record.get('matched_text') or ''
        tag = re.search(r'<html\b[^>]*>', code, re.I | re.S)
        code = tag.group(0) if tag else code
        match = LANG_ATTR.search(code)
        if not match:
            values.append({'value': None, 'value_origin': 'function', 'expression': code.strip()[:120],
                           'file': record['location']['file'], 'line': record['location']['line_start']})
            continue
        literal = match.group(1) if match.group(1) is not None else match.group(2)
        if literal is not None and not re.search(r'\{\{|\{%|<\?|<%|\$\{', literal) and literal:
            values.append({'value': literal, 'value_origin': 'literal',
                           'file': record['location']['file'], 'line': record['location']['line_start']})
        else:
            values.append({'value': None, 'value_origin': 'expression',
                           'expression': (match.group(3) if match.group(3) is not None else literal or '').strip()[:120],
                           'file': record['location']['file'], 'line': record['location']['line_start']})
    return values


def i18n_config(index):
    configs = []
    for path in index.paths('next.config.*', 'nuxt.config.*', 'astro.config.*', 'svelte.config.*', 'i18n.config.*',
                            'routing.ts', 'routing.js', 'i18n/routing.*', 'src/i18n/routing.*', 'middleware.ts', 'middleware.js'):
        text = index.files[path]
        block = re.search(r'\bi18n\s*:\s*\{|\bdefineRouting\s*\(\s*\{|\bcreateMiddleware\s*\(\s*\{', text)
        scope = text[block.start():index.block_end(path, block.end() - 1)] if block else text
        array = _array_after(scope, 'locales')
        if array is None:
            continue
        locales = _strings(array)
        if not locales:
            continue
        name = path.rsplit('/', 1)[-1]
        strategy = _scalar(scope, 'strategy') or _scalar(scope, 'localePrefix')
        if not strategy and name.startswith('next.config'):
            strategy = 'domain' if re.search(r'\bdomains\s*:', scope) else 'prefix_except_default'
        if not strategy and name.startswith('astro.config'):
            strategy = 'prefix' if re.search(r'prefixDefaultLocale\s*:\s*true', scope) else 'prefix_except_default'
        configs.append({'file': path, 'framework': name.split('.', 1)[0], 'locales': locales,
                        'default': _scalar(scope, 'defaultLocale') or _scalar(scope, 'baseLocale'), 'strategy': strategy})
    for path in index.paths('project.inlang/settings.json', 'inlang.config.*'):
        try:
            settings = json.loads(index.files[path])
        except ValueError:
            continue
        if isinstance(settings, dict) and (settings.get('locales') or settings.get('languageTags')):
            configs.append({'file': path, 'framework': 'paraglide', 'locales': settings.get('locales') or settings.get('languageTags'),
                            'default': settings.get('baseLocale') or settings.get('sourceLanguageTag'), 'strategy': None})
    for path in index.paths('hugo.toml', 'config.toml', 'config/_default/hugo.toml', 'config/_default/config.toml',
                            'config/_default/languages.toml'):
        try:
            document = tomllib.loads(index.files[path])
        except (tomllib.TOMLDecodeError, ValueError):
            continue
        languages = document if path.endswith('languages.toml') else document.get('languages')
        if isinstance(languages, dict) and languages:
            configs.append({'file': path, 'framework': 'hugo', 'locales': sorted(languages),
                            'default': document.get('defaultContentLanguage'),
                            'strategy': 'prefix' if document.get('defaultContentLanguageInSubdir') else 'prefix_except_default'})
    for path in index.paths('hugo.yaml', 'hugo.yml', 'config.yaml', 'config.yml', '_config.yml'):
        text = index.files[path]
        match = re.search(r'(?m)^languages\s*:\s*(\[[^\]]*\])?\s*$', text)
        if not match:
            continue
        locales = _strings(match.group(1)) if match.group(1) else re.findall(
            r'(?m)^  ([a-z]{2,3}(?:-[A-Za-z]{2,4})?)\s*:\s*$', text[match.end():])
        if locales:
            configs.append({'file': path, 'framework': 'hugo' if 'hugo' in path or 'defaultContentLanguage' in text else 'jekyll',
                            'locales': locales, 'default': _scalar(text, 'defaultContentLanguage') or _scalar(text, 'default_lang'),
                            'strategy': None})
    return configs


def analyze_hreflang_i18n(index):
    result = run_signal_profile(index, 'hreflang-i18n')
    declarations = html_lang_values(index, records_of(result, 'html-lang'))
    result['html_lang'] = declarations
    result['html_lang_values'] = sorted({d['value'] for d in declarations if d['value']})
    result['i18n_config'] = i18n_config(index)
    if declarations and any(d['value_origin'] != 'literal' for d in declarations):
        result['html_lang_note'] = 'Some <html lang> values are computed at runtime; measure the served HTML to confirm.'
    return result


class HreflangI18nAgent(RepoAnalysisAgent):
    """Agent: Locale routing, i18n libraries, hreflang alternates and <html lang>."""

    name = 'hreflang_i18n'
    description = 'Locale routing, i18n libraries, hreflang alternates and <html lang>.'
    signal_profile = 'hreflang-i18n'
    requires = ()

    def run(self, index, results):
        return analyze_hreflang_i18n(index)
