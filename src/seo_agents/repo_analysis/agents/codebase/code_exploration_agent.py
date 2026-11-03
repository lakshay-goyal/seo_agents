"""Repository profile: manifests, dependencies, languages, entry points and parsed hosting config.

`deployment_config` is the parsed hosting layer ({host, redirects, rewrites, headers}) from vercel.json,
netlify.toml, _headers, _redirects, firebase.json, wrangler.toml, staticwebapp.config.json and nginx/Apache
config: many SEO fixes (redirect status, soft 404, headers) live there rather than in application code.
"""

import json
from pathlib import PurePosixPath
import re
import tomllib

from seo_agents.repo_analysis.agents.crawling_indexing.canonicals_redirects_agent import redirect_rules
from seo_agents.repo_analysis.tools.repository.project_profile import analyze_project
from seo_agents.repo_analysis.tools.signal_profiles.signal_profile_runner import run_signal_profile
from seo_agents.repo_analysis.agents.base_agent import RepoAnalysisAgent

HOSTS = [('vercel.json', 'vercel'), ('netlify.toml', 'netlify'), ('wrangler.toml', 'cloudflare'), ('wrangler.json', 'cloudflare'),
         ('wrangler.jsonc', 'cloudflare'), ('firebase.json', 'firebase'), ('staticwebapp.config.json', 'azure-static-web-apps'),
         ('amplify.yml', 'aws-amplify'), ('app.yaml', 'google-app-engine'), ('render.yaml', 'render'), ('fly.toml', 'fly.io'),
         ('CNAME', 'github-pages'), ('_headers', 'netlify-or-cloudflare-pages'), ('_redirects', 'netlify-or-cloudflare-pages'),
         ('.htaccess', 'apache'), ('nginx.conf', 'nginx'), ('Caddyfile', 'caddy')]


def _headers_file(index, path):
    rows, current = [], None
    for number, line in enumerate(index.lines(path), 1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if not line[:1].isspace():
            current = line.strip()
            continue
        if ':' in line and current:
            name, value = line.strip().split(':', 1)
            rows.append({'for': current, 'name': name.strip(), 'value': value.strip(), 'file': path, 'line': number})
    return rows


def _headers_json(index, path):
    try:
        document = json.loads(index.files[path])
    except ValueError:
        return []
    hosting = document.get('hosting', document) if isinstance(document, dict) else {}
    if isinstance(hosting, list):
        hosting = hosting[0] if hosting else {}
    rows = []
    for entry in (hosting.get('headers') or []) if isinstance(hosting, dict) else []:
        if not isinstance(entry, dict):
            continue
        source = entry.get('source') or entry.get('route')
        headers = entry.get('headers') or []
        items = headers.items() if isinstance(headers, dict) else ((h.get('key'), h.get('value')) for h in headers if isinstance(h, dict))
        for name, value in items:
            rows.append({'for': source, 'name': name, 'value': value, 'file': path})
    global_headers = (document.get('globalHeaders') or {}) if isinstance(document, dict) else {}
    rows += [{'for': '/*', 'name': k, 'value': v, 'file': path} for k, v in global_headers.items()]
    return rows


def _headers_toml(index, path):
    try:
        document = tomllib.loads(index.files[path])
    except (tomllib.TOMLDecodeError, ValueError):
        return []
    rows = []
    for entry in document.get('headers', []) or []:
        for name, value in ((entry.get('values') or {}) if isinstance(entry, dict) else {}).items():
            rows.append({'for': entry.get('for'), 'name': name, 'value': value, 'file': path})
    return rows


def _headers_server(index, path):
    rows = []
    for number, line in enumerate(index.lines(path), 1):
        match = re.match(r'\s*(?:add_header|Header\s+(?:always\s+)?set)\s+"?([\w-]+)"?\s+"?([^";]+)', line)
        if match:
            rows.append({'for': None, 'name': match.group(1), 'value': match.group(2).strip(), 'file': path, 'line': number})
    return rows


def deployment_config(index):
    names = {PurePosixPath(p).name: p for p in index.paths(exclude_tests=True)}
    hosts = [host for name, host in HOSTS if name in names]
    headers = []
    for name, path in names.items():
        if name == '_headers':
            headers += _headers_file(index, path)
        elif name in {'vercel.json', 'firebase.json', 'staticwebapp.config.json'}:
            headers += _headers_json(index, path)
        elif name == 'netlify.toml':
            headers += _headers_toml(index, path)
        elif name == '.htaccess' or re.match(r'nginx.*\.conf$', name):
            headers += _headers_server(index, path)
    rules = redirect_rules(index)
    trailing = None
    for name in ('vercel.json',):
        if name in names:
            try:
                document = json.loads(index.files[names[name]])
                trailing = {k: document[k] for k in ('trailingSlash', 'cleanUrls') if isinstance(document, dict) and k in document} or None
            except ValueError:
                pass
    return {'host': hosts[0] if hosts else None, 'hosts': hosts,
            'files': sorted(names[n] for n, _ in HOSTS if n in names),
            'redirects': [r for r in rules if r['kind'] == 'redirect'],
            'rewrites': [r for r in rules if r['kind'] == 'rewrite'],
            'headers': headers, **({'url_policy': trailing} if trailing else {})}


def explore_code(index):
    profile = analyze_project(index)
    profile['deployment_config_files'] = profile.pop('deployment_config', [])
    profile['deployment_config'] = deployment_config(index)
    profile['skill'] = run_signal_profile(index, 'code-exploration')
    return profile


class CodeExplorationAgent(RepoAnalysisAgent):
    """Agent: Repository profile: manifests, dependencies, languages, entry points and parsed hosting config."""

    name = 'code_exploration'
    description = 'Repository profile: manifests, dependencies, languages, entry points and parsed hosting config.'
    signal_profile = 'code-exploration'
    requires = ()

    def run(self, index, results):
        return explore_code(index)
