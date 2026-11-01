"""Canonicals, redirect/rewrite rules, trailing-slash policy and 404 handling in code/config."""

import json
import re

from seo_agents.exploration.repo_analysis.tools.skill_runner import records_of, run_skill


def redirect_rules(index):
    rules = []
    for path in index.paths('_redirects'):
        for number, line in enumerate(index.lines(path), 1):
            parts = line.split('#', 1)[0].split()
            if len(parts) >= 2:
                rules.append({'file': path, 'line': number, 'from': parts[0], 'to': parts[1],
                              'status': parts[2] if len(parts) > 2 else '301', 'code': line})
    for path in index.paths('vercel.json', 'netlify.json', 'firebase.json', 'staticwebapp.config.json'):
        try:
            document = json.loads(index.files[path])
        except ValueError:
            continue
        hosting = document.get('hosting', document) if isinstance(document, dict) else {}
        for key in ('redirects', 'rewrites', 'routes'):
            for rule in hosting.get(key, []) if isinstance(hosting, dict) else []:
                if isinstance(rule, dict):
                    rules.append({'file': path, 'type': key, 'from': rule.get('source') or rule.get('src') or rule.get('route'),
                                  'to': rule.get('destination') or rule.get('dest') or rule.get('redirect'),
                                  'status': rule.get('statusCode') or rule.get('status') or rule.get('type')
                                  or (308 if rule.get('permanent') else 307 if 'permanent' in rule else None)})
    for path in index.paths('.htaccess', 'nginx*.conf'):
        for number, line in enumerate(index.lines(path), 1):
            if re.match(r'\s*(?:Redirect(?:Match|Permanent)?|RewriteRule|return\s+30[1278]|rewrite)\b', line):
                rules.append({'file': path, 'line': number, 'code': line.strip()})
    return rules


def analyze_canonicals_redirects(index):
    result = run_skill(index, 'canonicals-redirects')
    rules = redirect_rules(index)
    hosting_files = {r['file'] for r in rules}
    for record in records_of(result, 'redirect-config'):
        if record['location']['file'] in hosting_files:
            continue
        for match in re.finditer(r'''source\s*:\s*["'`]([^"'`]+)["'`][\s\S]{0,200}?destination\s*:\s*["'`]([^"'`]+)["'`](?:[\s\S]{0,80}?permanent\s*:\s*(true|false))?''', record['code']):
            rules.append({'file': record['location']['file'], 'record_id': record['id'], 'from': match.group(1),
                          'to': match.group(2), 'permanent': match.group(3) == 'true' if match.group(3) else None})
    result['redirect_rules'] = rules
    result['canonical_values'] = sorted({m.group(1) for r in records_of(result, 'canonical')
                                         for m in re.finditer(r'''(?:href\s*=|canonical\s*:)\s*\{?\s*["'`]([^"'`]+)''',
                                                              r['context']['text'], re.I)})
    return result
