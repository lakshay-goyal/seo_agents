"""Project profile: manifests, dependencies, scripts, languages, entry points, deploy config."""

from collections import Counter
import json
from pathlib import PurePosixPath
import re
import tomllib

from seo_agents.repo_analysis.tools.repository.source_index import language_of

DEPLOY_FILES = ('vercel.json', 'netlify.toml', '_redirects', '_headers', 'Dockerfile', 'firebase.json',
                'app.yaml', 'wrangler.toml', 'render.yaml', 'fly.toml', 'Procfile', '.htaccess',
                'nginx.conf', 'amplify.yml', 'staticwebapp.config.json', 'web.config')
ENTRY_NAMES = re.compile(r'^(?:src/|app/)?(?:index|main|app|server|_app|root|layout|App)\.(?:html|[jt]sx?|vue|svelte|astro|py|rb|php|go)$', re.I)
SEO_PACKAGES = {
    'next-seo', 'next-sitemap', 'react-helmet', 'react-helmet-async', '@unhead/vue', '@vueuse/head', 'vue-meta',
    '@nuxtjs/sitemap', '@nuxtjs/robots', 'nuxt-schema-org', '@nuxtjs/seo', '@astrojs/sitemap', 'astro-seo',
    'gatsby-plugin-sitemap', 'gatsby-plugin-robots-txt', 'gatsby-plugin-react-helmet', 'schema-dts',
    'web-vitals', '@vercel/analytics', '@vercel/speed-insights', 'next-intl', 'next-i18next', 'react-i18next',
    'i18next', 'vue-i18n', '@nuxtjs/i18n', 'sharp', '@next/third-parties', 'partytown', '@builder.io/partytown',
    'django-meta', 'flask-talisman', 'helmet', 'compression', 'meta-tags', 'sitemap_generator', 'jekyll-seo-tag',
    'jekyll-sitemap', 'artesaos/seotools', 'spatie/laravel-sitemap', 'sitemap', 'rss', '@astrojs/rss', 'feed',
}
HEAVY_PACKAGES = {'moment', 'lodash', 'jquery', 'rxjs', 'three', 'chart.js', 'd3', 'antd', '@mui/material',
                  'bootstrap', 'aos', 'gsap', 'framer-motion', 'animejs', 'firebase', 'aws-sdk', 'core-js',
                  'styled-components', '@emotion/react', 'highlight.js', 'prismjs', 'video.js', 'swiper', 'slick-carousel'}


def _package_json(index, path, deps, manifests, records):
    try:
        document = json.loads(index.files[path])
    except ValueError as error:
        manifests.append({'file': path, 'type': 'package.json', 'parse_error': str(error)[:200]})
        return
    if not isinstance(document, dict):
        return
    for section in ('dependencies', 'devDependencies', 'peerDependencies'):
        for name, version in (document.get(section) or {}).items():
            deps.setdefault(name.lower(), {'name': name, 'version': version, 'manifest': path,
                                           'dev': section != 'dependencies'})
    entry = {'file': path, 'type': 'package.json', 'name': document.get('name'),
             'workspaces': document.get('workspaces'), 'scripts': document.get('scripts') or {},
             'type_field': document.get('type'), 'engines': document.get('engines')}
    text = index.files[path]
    for key in ('dependencies', 'devDependencies', 'scripts'):
        match = re.search(r'"' + key + r'"\s*:', text)
        if match:
            records.append(index.evidence('manifest-' + key, path, match.start(), block=True,
                                          summary=key + ' declared in ' + path))
    manifests.append(entry)


def _requirements(index, path, deps, manifests):
    for number, line in enumerate(index.lines(path), 1):
        match = re.match(r'\s*([A-Za-z0-9_.\-\[\]]+)\s*([=<>!~].*)?$', line.split('#')[0])
        if match and match.group(1):
            name = re.sub(r'\[.*\]', '', match.group(1)).lower()
            deps.setdefault(name, {'name': name, 'version': (match.group(2) or '').strip() or None,
                                   'manifest': path, 'line': number, 'dev': 'dev' in path})
    manifests.append({'file': path, 'type': 'requirements'})


def _toml(index, path, deps, manifests):
    try:
        document = tomllib.loads(index.files[path])
    except tomllib.TOMLDecodeError as error:
        manifests.append({'file': path, 'type': 'toml', 'parse_error': str(error)[:200]})
        return
    project = document.get('project') or {}
    names = list(project.get('dependencies') or [])
    poetry = (document.get('tool') or {}).get('poetry') or {}
    names += list((poetry.get('dependencies') or {}).keys())
    for raw in names:
        name = re.split(r'[\s<>=!~\[;]', raw, maxsplit=1)[0].lower()
        if name and name != 'python':
            deps.setdefault(name, {'name': name, 'version': raw, 'manifest': path, 'dev': False})
    if names:
        manifests.append({'file': path, 'type': PurePosixPath(path).name, 'name': project.get('name') or poetry.get('name')})


def _line_deps(index, path, kind, pattern, deps, manifests):
    for number, line in enumerate(index.lines(path), 1):
        match = re.search(pattern, line)
        if match:
            name = match.group(1).lower()
            deps.setdefault(name, {'name': name, 'version': match.group(2) if match.lastindex and match.lastindex > 1 else None,
                                   'manifest': path, 'line': number, 'dev': False})
    manifests.append({'file': path, 'type': kind})


def _composer(index, path, deps, manifests):
    try:
        document = json.loads(index.files[path])
    except ValueError:
        return
    for section in ('require', 'require-dev'):
        for name, version in (document.get(section) or {}).items():
            deps.setdefault(name.lower(), {'name': name, 'version': version, 'manifest': path,
                                           'dev': section == 'require-dev'})
    manifests.append({'file': path, 'type': 'composer.json', 'name': document.get('name')})


def framework_hints(index, deps):
    """Descriptive labels from the code-exploration skill; no agent depends on them."""
    from seo_agents.repo_analysis.tools.repository.source_index import glob_match
    from seo_agents.repo_analysis.tools.signal_profiles.signal_profile_runner import signal_profile
    hints = []
    for label, rule in signal_profile('code-exploration')['profile']['framework_markers'].items():
        evidence = [{'type': 'dependency', 'name': deps[d]['name'], 'version': deps[d].get('version'),
                     'manifest': deps[d].get('manifest')} for d in rule.get('dependencies', []) if d in deps]
        for pattern in rule.get('files', []):
            hits = [p for p in index.files if glob_match(p, pattern) and p.count('/') <= 2]
            if hits:
                evidence.append({'type': 'marker-file', 'pattern': pattern, 'files': hits[:5]})
        if evidence:
            hints.append({'label': label, 'evidence': evidence})
    return hints


def analyze_project(index):
    deps, manifests, records = {}, [], []
    for path in index.paths(exclude_tests=False):
        name = PurePosixPath(path).name
        if name == 'package.json':
            _package_json(index, path, deps, manifests, records)
        elif re.match(r'requirements.*\.txt$', name):
            _requirements(index, path, deps, manifests)
        elif name in {'pyproject.toml', 'Cargo.toml'}:
            _toml(index, path, deps, manifests)
        elif name == 'Gemfile':
            _line_deps(index, path, 'Gemfile', r'''^\s*gem\s+['"]([^'"]+)['"](?:\s*,\s*['"]([^'"]+))?''', deps, manifests)
        elif name == 'go.mod':
            _line_deps(index, path, 'go.mod', r'^\s*(?:require\s+)?([\w./\-]+\.[\w./\-]+)\s+(v[\w.\-+]+)', deps, manifests)
        elif name == 'composer.json':
            _composer(index, path, deps, manifests)
    languages = Counter(language_of(p) for p in index.files)
    top_level = Counter(p.split('/', 1)[0] if '/' in p else '.' for p in index.files)
    deploy = []
    for path in index.paths(exclude_tests=False):
        name = PurePosixPath(path).name
        if name in DEPLOY_FILES or re.match(r'nginx.*\.conf$', name):
            deploy.append(index.evidence_lines('deploy-config', path, 1, len(index.lines(path)),
                                               summary='Hosting/server configuration: ' + name))
    entries = [index.evidence_lines('entry-point', p, 1, min(len(index.lines(p)), 60), summary='Candidate application entry point')
               for p in index.paths() if ENTRY_NAMES.match(p)]
    return {
        'framework_hints': framework_hints(index, deps),
        'manifests': manifests,
        'dependencies': sorted(deps.values(), key=lambda d: d['name'].lower()),
        'seo_relevant_dependencies': sorted(d['name'] for k, d in deps.items() if k in SEO_PACKAGES),
        'heavy_client_dependencies': sorted(d['name'] for k, d in deps.items() if k in HEAVY_PACKAGES and not d.get('dev')),
        'languages': dict(languages.most_common()),
        'top_level_layout': dict(top_level.most_common()),
        'manifest_evidence': records,
        'deployment_config': deploy,
        'entry_points': entries,
        'asset_count': len(index.assets),
    }


class ProjectProfileTool:
    """Tool: project profile from manifests (package.json, requirements, composer, TOML) plus framework hints."""

    name = 'project_profile'

    def analyze(self, index):
        return analyze_project(index)
