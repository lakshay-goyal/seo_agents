"""Canonicals, redirect/rewrite rules, trailing-slash policy, 404 handling and soft-404 risk.

Every hosting/framework rule becomes one row `{from, to, status, kind: redirect|rewrite, file, line}`.
Rewrites (internal, URL unchanged) are never reported as redirects. `redirects` is the redirect-only view
consumed by the site model; `redirect_rules` keeps both kinds.
"""

import json
import re
import tomllib

from seo_agents.exploration.repo_analysis.tools.skill_runner import records_of, run_skill

CATCH_ALL = re.compile(r'^/?(?:\(\.\*\)|\.\*|\*\*?|:\w+[*+]|:path\*|:\w+\(\.\*\)\*?|\*\s*$|\[\.\.\.\w+\])$|/\(\.\*\)$|/\*$|/:\w+\*$')
SHELL_TARGET = re.compile(r'^/?(?:index\.html?|200\.html|404\.html)?$')
CLIENT_CATCH_ALL = [
    (re.compile(r'''\bpath\s*[:=]\s*\{?\s*['"]\*{1,2}['"]'''), 'router-catch-all'),
    (re.compile(r'''['"]/:pathMatch\(\.\*\)\*?['"]|['"]/:catchAll\(\.\*\)['"]'''), 'router-catch-all'),
    (re.compile(r'''<Route\b[^>]*\bpath\s*=\s*['"]\*['"]'''), 'router-catch-all'),
]
EXPRESSION_VALUE = re.compile(r'\{\{|\{%|<\?|\$\{|^\{|\bprops\.|\bAstro\.|\.Permalink|^\w+(?:\.\w+)+$')


def _line(text, offset):
    return text.count('\n', 0, offset) + 1


def _rule(kind, source, target, status, path, line, **extra):
    return {'from': source, 'to': target, 'status': status, 'kind': kind, 'file': path, 'line': line,
            **{k: v for k, v in extra.items() if v is not None}}


def _find_line(text, needle):
    position = text.find(needle) if needle else -1
    return _line(text, position) if position >= 0 else None


def _redirects_file(index, path):
    """Netlify / Cloudflare Pages `_redirects`: `from to [status][!]`; 200 = rewrite."""
    rules = []
    for number, line in enumerate(index.lines(path), 1):
        parts = line.split('#', 1)[0].split()
        if len(parts) < 2:
            continue
        status_token = next((p for p in parts[2:] if re.fullmatch(r'\d{3}!?', p)), None)
        status = int(status_token.rstrip('!')) if status_token else 301
        kind = 'rewrite' if status in {200} else 'redirect' if 300 <= status < 400 else 'rewrite'
        rules.append(_rule(kind, parts[0], parts[1], status, path, number,
                           force=True if status_token and status_token.endswith('!') else None))
    return rules


def _netlify_toml(index, path):
    text = index.files[path]
    try:
        document = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return []
    rules = []
    for entry in document.get('redirects', []) or []:
        if not isinstance(entry, dict):
            continue
        status = int(entry.get('status') or 301)
        kind = 'redirect' if 300 <= status < 400 else 'rewrite'
        rules.append(_rule(kind, entry.get('from'), entry.get('to'), status, path,
                           _find_line(text, str(entry.get('from'))), force=entry.get('force')))
    return rules


def _json_hosting(index, path):
    text = index.files[path]
    try:
        document = json.loads(text)
    except ValueError:
        return []
    if not isinstance(document, dict):
        return []
    rules = []
    name = path.rsplit('/', 1)[-1]
    hosting = document.get('hosting', document) if name == 'firebase.json' else document
    if isinstance(hosting, list):  # firebase multi-site
        hosting = hosting[0] if hosting and isinstance(hosting[0], dict) else {}
    if not isinstance(hosting, dict):
        return []
    for entry in hosting.get('redirects', []) or []:
        if not isinstance(entry, dict):
            continue
        source = entry.get('source') or entry.get('src')
        if name == 'firebase.json':
            status = int(entry.get('type') or 301)
        elif entry.get('statusCode'):
            status = int(entry['statusCode'])
        else:  # vercel.json: `permanent` defaults to true -> 308
            status = 307 if entry.get('permanent') is False else 308
        rules.append(_rule('redirect', source, entry.get('destination') or entry.get('dest'), status, path,
                           _find_line(text, json.dumps(source) if source else None)))
    for entry in hosting.get('rewrites', []) or []:
        if not isinstance(entry, dict):
            continue
        source = entry.get('source') or entry.get('src')
        target = entry.get('destination') or entry.get('dest') or (entry.get('function') and 'function:' + str(entry['function']))
        rules.append(_rule('rewrite', source, target, 200, path, _find_line(text, json.dumps(source) if source else None)))
    for entry in hosting.get('routes', []) or []:  # vercel legacy routes / Azure Static Web Apps
        if not isinstance(entry, dict):
            continue
        source = entry.get('src') or entry.get('route')
        status = entry.get('status') or entry.get('statusCode')
        target = entry.get('dest') or entry.get('redirect') or entry.get('rewrite') or (entry.get('headers') or {}).get('Location')
        if not target:
            continue
        is_redirect = bool(entry.get('redirect')) or (status and 300 <= int(status) < 400)
        rules.append(_rule('redirect' if is_redirect else 'rewrite', source, target,
                           int(status) if status else (302 if is_redirect else 200), path,
                           _find_line(text, json.dumps(source) if source else None)))
    fallback = (document.get('navigationFallback') or {}).get('rewrite') if isinstance(document.get('navigationFallback'), dict) else None
    if fallback:
        rules.append(_rule('rewrite', '/*', fallback, 200, path, _find_line(text, 'navigationFallback')))
    return rules


def _server_config(index, path):
    """Apache .htaccess and nginx: only real redirects count; RewriteRule without [R] is a rewrite."""
    rules, location = [], None
    for number, line in enumerate(index.lines(path), 1):
        stripped = line.split('#', 1)[0].strip()
        if not stripped:
            continue
        match = re.match(r'location\s+(?:[=~^*]+\s*)?(\S+)\s*\{', stripped)
        if match:
            location = match.group(1)
            continue
        match = re.match(r'RedirectPermanent\s+(\S+)\s+(\S+)', stripped)
        if match:
            rules.append(_rule('redirect', match.group(1), match.group(2), 301, path, number))
            continue
        match = re.match(r'Redirect(?:Match)?\s+(?:(permanent|temp|seeother|gone|\d{3})\s+)?(\S+)(?:\s+(\S+))?', stripped)
        if match and match.group(3):
            token = match.group(1) or 'temp'
            status = {'permanent': 301, 'temp': 302, 'seeother': 303, 'gone': 410}.get(token) or int(token)
            rules.append(_rule('redirect', match.group(2), match.group(3), status, path, number))
            continue
        match = re.match(r'RewriteRule\s+(\S+)\s+(\S+)(?:\s+\[([^\]]*)\])?', stripped)
        if match:
            flags = match.group(3) or ''
            redirect = re.search(r'(?:^|,)\s*R(?:=(\d{3}))?\s*(?:,|$)', flags)
            rules.append(_rule('redirect' if redirect else 'rewrite', match.group(1), match.group(2),
                               int(redirect.group(1) or 302) if redirect else 200, path, number))
            continue
        match = re.match(r'return\s+(30[12378])\s+(\S+?);?$', stripped)
        if match:
            rules.append(_rule('redirect', location, match.group(2), int(match.group(1)), path, number))
            continue
        match = re.match(r'rewrite\s+(\S+)\s+(\S+?)(?:\s+(permanent|redirect|last|break))?\s*;', stripped)
        if match:
            flag = match.group(3)
            kind = 'redirect' if flag in {'permanent', 'redirect'} or match.group(2).startswith(('http://', 'https://')) else 'rewrite'
            status = 301 if flag == 'permanent' else 302 if kind == 'redirect' else 200
            rules.append(_rule(kind, match.group(1), match.group(2), status, path, number))
    return rules


JS_RULE = re.compile(r'''source\s*:\s*["'`]([^"'`]+)["'`][\s\S]{0,300}?destination\s*:\s*["'`]([^"'`]+)["'`]''')


def _js_config(index, path):
    """next.config redirects()/rewrites(), Nuxt routeRules, Astro `redirects`, Gatsby createRedirect."""
    text, rules = index.files[path], []
    name = path.rsplit('/', 1)[-1]
    for function in ('redirects', 'rewrites'):
        for match in re.finditer(r'\b(?:async\s+)?' + function + r'\s*(?:\(\s*\)|:\s*(?:async\s*)?\(\s*\)\s*=>)\s*\{?', text):
            end = index.block_end(path, match.end() - 1) if text[match.end() - 1:match.end()] == '{' else min(len(text), match.end() + 5000)
            body = text[match.end():end]
            for rule in JS_RULE.finditer(body):
                tail = body[rule.end():rule.end() + 160].split('}', 1)[0]
                if function == 'rewrites':
                    rules.append(_rule('rewrite', rule.group(1), rule.group(2), 200, path, _line(text, match.end() + rule.start())))
                    continue
                code = re.search(r'statusCode\s*:\s*(\d{3})', tail)
                permanent = re.search(r'permanent\s*:\s*(true|false)', tail)
                status = int(code.group(1)) if code else 307 if permanent and permanent.group(1) == 'false' else 308
                rules.append(_rule('redirect', rule.group(1), rule.group(2), status, path,
                                   _line(text, match.end() + rule.start())))
    if name.startswith('nuxt.config'):
        rules_match = re.search(r'\brouteRules\s*:\s*\{', text)
        if rules_match:
            body = text[rules_match.end():index.block_end(path, rules_match.end() - 1)]
            for entry in re.finditer(r'''['"`]([^'"`]+)['"`]\s*:\s*\{[^{}]*?\bredirect\s*:\s*(?:['"`]([^'"`]+)['"`]|\{([^{}]*)\})''', body):
                target, status = entry.group(2), 307
                if entry.group(3):
                    to = re.search(r'''to\s*:\s*['"`]([^'"`]+)''', entry.group(3))
                    code = re.search(r'statusCode\s*:\s*(\d{3})', entry.group(3))
                    target, status = (to.group(1) if to else None), int(code.group(1)) if code else 307
                rules.append(_rule('redirect', entry.group(1), target, status, path, _line(text, rules_match.end() + entry.start())))
            for entry in re.finditer(r'''['"`]([^'"`]+)['"`]\s*:\s*\{[^{}]*?\bproxy\s*:\s*['"`]([^'"`]+)''', body):
                rules.append(_rule('rewrite', entry.group(1), entry.group(2), 200, path, _line(text, rules_match.end() + entry.start())))
    if name.startswith('astro.config'):
        block = re.search(r'\bredirects\s*:\s*\{', text)
        if block:
            body = text[block.end():index.block_end(path, block.end() - 1)]
            for entry in re.finditer(r'''['"`]([^'"`]+)['"`]\s*:\s*(?:['"`]([^'"`]+)['"`]|\{([^{}]*)\})''', body):
                target, status = entry.group(2), 301
                if entry.group(3):
                    to = re.search(r'''destination\s*:\s*['"`]([^'"`]+)''', entry.group(3))
                    code = re.search(r'status\s*:\s*(\d{3})', entry.group(3))
                    target, status = (to.group(1) if to else None), int(code.group(1)) if code else 301
                rules.append(_rule('redirect', entry.group(1), target, status, path, _line(text, block.end() + entry.start())))
    for call in re.finditer(r'\bcreateRedirect\s*\(\s*\{', text):
        body = text[call.end():index.block_end(path, call.end() - 1)]
        source = re.search(r'''fromPath\s*:\s*['"`]([^'"`]+)''', body)
        target = re.search(r'''toPath\s*:\s*['"`]([^'"`]+)''', body)
        permanent = re.search(r'isPermanent\s*:\s*(true|false)', body)
        code = re.search(r'statusCode\s*:\s*(\d{3})', body)
        status = int(code.group(1)) if code else 301 if permanent and permanent.group(1) == 'true' else 302
        rules.append(_rule('redirect', source.group(1) if source else None, target.group(1) if target else None,
                           status, path, _line(text, call.start())))
    return rules


FRONT_MATTER = re.compile(r'\A(?:---|\+\+\+)\s*\n(.*?)\n(?:---|\+\+\+)\s*(?:\n|\Z)', re.S)


def _front_matter_aliases(index, path):
    """Hugo `aliases` and Jekyll `redirect_from` generate meta-refresh pages pointing at this page."""
    match = FRONT_MATTER.match(index.files[path])
    if not match:
        return []
    block, rules = match.group(1), []
    for key in ('aliases', 'redirect_from'):
        found = re.search(r'(?m)^' + key + r'\s*[:=]\s*(.*)$', block)
        if not found:
            continue
        inline = found.group(1).strip()
        values = re.findall(r'''['"]?(/[^'",\]\s]*)['"]?''', inline) if inline else []
        if not values:  # YAML list on following lines
            following = block[found.end():]
            values = [v for v in re.findall(r'(?m)^\s*-\s*[\'"]?(/[^\'"\s]*)', following.split('\n\n', 1)[0])]
        for value in values:
            rules.append(_rule('redirect', value, path, None, path, _line(index.files[path], match.start(1) + found.start()),
                               mechanism='meta-refresh', to_is_source_file=True))
    return rules


def redirect_rules(index):
    rules = []
    for path in index.paths('_redirects'):
        rules += _redirects_file(index, path)
    for path in index.paths('netlify.toml'):
        rules += _netlify_toml(index, path)
    for path in index.paths('vercel.json', 'netlify.json', 'firebase.json', 'staticwebapp.config.json'):
        rules += _json_hosting(index, path)
    for path in index.paths('.htaccess', 'nginx*.conf', '*.nginx', 'nginx/*.conf', 'conf.d/*.conf'):
        rules += _server_config(index, path)
    for path in index.paths('next.config.*', 'nuxt.config.*', 'astro.config.*', 'gatsby-node.*'):
        rules += _js_config(index, path)
    for path in index.paths('*.md', '*.markdown', '*.html', exclude_docs=True):
        if index.files[path].startswith(('---', '+++')):
            rules += _front_matter_aliases(index, path)
    seen, unique = set(), []
    for rule in rules:
        key = (rule['file'], rule.get('line'), rule['from'], rule['to'], rule['kind'])
        if key not in seen:
            seen.add(key)
            unique.append(rule)
    return unique


def soft_404_risks(index, rules):
    """Catch-alls that make unknown URLs answer 200 with the app shell (soft 404 unless the host 404s)."""
    risks = []
    for rule in rules:
        if rule['kind'] == 'rewrite' and rule.get('from') and CATCH_ALL.search(str(rule['from']).strip()) \
                and SHELL_TARGET.match(str(rule.get('to') or '').split('?', 1)[0]):
            risks.append({'kind': 'host-catch-all-rewrite', 'file': rule['file'], 'line': rule.get('line'),
                          'detail': f"{rule['from']} -> {rule['to']} serves the app shell (200) for every unknown URL"})
    for path in index.paths(languages={'javascript', 'typescript', 'vue', 'svelte', 'astro'}):
        text = index.files[path]
        for pattern, kind in CLIENT_CATCH_ALL:
            for match in pattern.finditer(text):
                risks.append({'kind': kind, 'file': path, 'line': _line(text, match.start()),
                              'detail': 'client-side catch-all route renders a not-found view with HTTP 200 unless the host returns 404'})
    for path in index.paths('error.vue'):
        if path.count('/') == 0 or path.startswith(('app/', 'src/')):
            risks.append({'kind': 'nuxt-error-page', 'file': path, 'line': 1,
                          'detail': 'Nuxt error.vue: confirm unknown URLs return 404 (throw createError({statusCode: 404}))'})
    return risks


CANONICAL_TAG = re.compile(r'''<link\b(?=[^>]*\brel\s*=\s*["'{]?\s*["']?canonical["'])[^>]*?\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|\{([^}]*)\})''', re.I | re.S)
CANONICAL_TAG_REVERSED = re.compile(r'''<link\b[^>]*?\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|\{([^}]*)\})[^>]*\brel\s*=\s*["']canonical["']''', re.I | re.S)
CANONICAL_PROP = re.compile(r'''\bcanonical(?:_url|URL|Url)?\s*[:=]\s*(?:["'`]([^"'`\n]+)["'`]|([\w.$()\[\]]+))''')


def canonical_values(index, records):
    """Values of the canonical declaration itself (never neighbouring lines), tagged literal vs expression."""
    values = []
    for record in records:
        path = record['location']['file']
        lines = index.lines(path)
        code = '\n'.join(lines[record['location']['line_start'] - 1:record['location']['line_end'] + 1])
        found = []
        for pattern in (CANONICAL_TAG, CANONICAL_TAG_REVERSED):
            for match in pattern.finditer(code):
                literal = match.group(1) if match.group(1) is not None else match.group(2)
                found.append((literal if literal is not None else match.group(3), literal is None))
        if not found:
            for match in CANONICAL_PROP.finditer(code):
                found.append((match.group(1) or match.group(2), match.group(1) is None))
        for value, is_expression in found:
            value = (value or '').strip()
            if not value:
                continue
            expression = is_expression or bool(EXPRESSION_VALUE.search(value))
            values.append({'value': None if expression else value, 'expression': value if expression else None,
                           'value_origin': 'expression' if expression else 'literal',
                           'file': path, 'line': record['location']['line_start']})
    return values


def analyze_canonicals_redirects(index):
    result = run_skill(index, 'canonicals-redirects')
    rules = redirect_rules(index)
    result['redirect_rules'] = rules
    result['redirects'] = [r for r in rules if r['kind'] == 'redirect']
    result['rewrites'] = [r for r in rules if r['kind'] == 'rewrite']
    result['soft_404_risks'] = soft_404_risks(index, rules)
    canonicals = canonical_values(index, records_of(result, 'canonical'))
    result['canonical_declarations'] = canonicals
    result['canonical_values'] = sorted({c['value'] for c in canonicals if c['value']})
    return result
