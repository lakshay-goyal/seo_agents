"""Learned route adapters for unrecognised frameworks: the route-adapter gap worker writes declarative URL rules\nfrom the file tree (cached per repo snapshot), and `apply_learned_rules` turns them into URL rows."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re

from seo_agents.utils.step_logger import log
from seo_agents.repo_analysis.agents.gap_workers.gap_task_prompts import LIMITS
from seo_agents.repo_analysis.agents.gap_workers.gap_task_solver import GAP_CODE_VERSION, solve

CACHE_DIR = Path(os.getenv('SEO_ADAPTER_CACHE') or Path.home() / '.cache' / 'seo_agents' / 'adapters')


# ---------------------------------------------------------------- learned adapters (unknown frameworks)
TEMPLATE_HINT = re.compile(r'\.(?:html?|php|tpl|twig|njk|liquid|hbs|handlebars|mustache|ejs|pug|erb|haml|slim|cshtml|razor|'
                           r'jsx?|tsx?|vue|svelte|astro|mdx?|py|rb|go|templ|jinja2?|j2|cfm|jsp|aspx?|gohtml)$', re.I)


def file_heads(files, paths):
    """First HEAD_LINES lines of up to MAX_HEADS template-like files, spread across directories."""
    chosen, per_dir = [], Counter()
    for path in sorted((p for p in paths if TEMPLATE_HINT.search(p)), key=lambda p: (p.count('/'), p)):
        directory = path.rsplit('/', 1)[0] if '/' in path else ''
        if per_dir[directory] >= 3:
            continue
        per_dir[directory] += 1
        chosen.append(path)
        if len(chosen) >= LIMITS['MAX_HEADS']:
            break
    return {path: '\n'.join(line[:160] for line in str(files.get(path, '')).splitlines()[:LIMITS['HEAD_LINES']]) for path in chosen}


def learn_adapter(snapshot, fingerprint, *, model_factory=None):
    """Declarative URL rules for an unrecognised stack (ladder small -> haiku -> mid), cached per repo snapshot."""
    files = sorted(p for p in snapshot['files'] if not re.search(r'(?:^|/)(?:node_modules|tests?|__tests__|\.git)/', p))[:LIMITS['MAX_FILES']]
    names = sorted(c['name'] for c in fingerprint.get('candidates', []))
    repo = snapshot.get('repo') or snapshot.get('root') or os.getenv('SEO_REPO_ID') or ''
    key = hashlib.sha256(json.dumps([GAP_CODE_VERSION, str(repo), snapshot.get('sha256'), names,
                                     fingerprint.get('version', '') and str(fingerprint['version']).split('.')[0],
                                     sorted({p.rsplit('.', 1)[-1] for p in files if '.' in p})]).encode()).hexdigest()[:16]
    cache = CACHE_DIR / (key + '.json')
    if cache.exists():
        try:
            rules = json.loads(cache.read_text())
            log('route adapter: reusing cached rules ' + cache.name)
            return rules, 'cache'
        except ValueError:
            pass
    task = {'task': 'route_adapter', 'page_id': None,
            'packet': {'files': files, 'heads': file_heads(snapshot['files'], files), 'candidates': names,
                       'manifests': sorted({c['name'] for c in fingerprint.get('candidates', [])})}}
    result = solve(task, fingerprint.get('family'), model_factory=model_factory)
    if result['status'] != 'resolved':
        return None, result['status']
    rules = result['answer']
    if not rules.get('rules'):
        return None, 'unknown'
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(rules, indent=2))
    except OSError:
        pass
    return rules, result['tier']


def apply_learned_rules(files, rules):
    rows = []
    for rule in (rules or {}).get('rules', []):
        regex = re.compile(rule['path_regex'])
        for path in sorted(files):
            match = regex.match(path)
            if not match:
                continue
            route = match.group('route') or ''
            parts = [p for p in route.split('/') if p and p not in set(rule.get('index_names') or ['index'])]
            url = (rule.get('url_template') or '/{route}').replace('{route}', '/'.join(parts))
            url = '/' + url.strip('/') if url.strip('/') else '/'
            rows.append({'url': url, 'template': path, 'kind': 'page', 'adapter': 'learned:' + (rules.get('framework') or 'unknown'),
                         'is_dynamic': bool(re.search(r'\[[^\]]+\]|:\w+|\{\w+\}', url))})
    return rows
