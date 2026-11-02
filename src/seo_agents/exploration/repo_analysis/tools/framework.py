"""Framework fingerprint: which stack, router and renderer a repository uses.

Deterministic and framework-agnostic. Evidence comes from dependency manifests,
configuration files and directory layout, so the same function recognises a
Next.js app, a Shopify theme, a WordPress theme, a Hugo site or a Django project.
Every detection keeps the evidence that triggered it; nothing is guessed silently.

The fingerprint drives three later stages:
- which route adapter / composition rules apply (`element_index`, `routes`);
- how to build and serve the site (`runtime/build_serve.py`);
- which few-shot examples a gap worker receives (progressive skill disclosure).
"""

from __future__ import annotations

import json
import posixpath
import re
from pathlib import PurePosixPath

# name -> detection rule. `deps` = any dependency name, `files` = any path regex,
# `content` = (path regex, content regex). The first rule with the highest score wins
# for `primary`; every matching rule is still listed in `candidates`.
FRAMEWORKS = {
    'next': {'deps': {'next'}, 'files': [r'(?:^|/)next\.config\.[cm]?[jt]s$'], 'renderer': 'hybrid',
             'build_outputs': ['out', '.next'], 'family': 'react'},
    'nuxt': {'deps': {'nuxt', 'nuxt3'}, 'files': [r'(?:^|/)nuxt\.config\.[jt]s$'], 'renderer': 'hybrid',
             'build_outputs': ['.output/public', 'dist'], 'family': 'vue'},
    'sveltekit': {'deps': {'@sveltejs/kit'}, 'files': [r'(?:^|/)svelte\.config\.[jt]s$'], 'renderer': 'hybrid',
                  'build_outputs': ['build', '.svelte-kit/output/prerendered/pages'], 'family': 'svelte'},
    'astro': {'deps': {'astro'}, 'files': [r'(?:^|/)astro\.config\.[cm]?[jt]s$'], 'renderer': 'static',
              'build_outputs': ['dist'], 'family': 'astro'},
    'remix': {'deps': {'@remix-run/react', '@remix-run/node', '@react-router/dev'}, 'files': [r'(?:^|/)remix\.config\.[jt]s$', r'(?:^|/)react-router\.config\.[jt]s$'],
              'renderer': 'ssr', 'build_outputs': ['build/client', 'public/build'], 'family': 'react'},
    'gatsby': {'deps': {'gatsby'}, 'files': [r'(?:^|/)gatsby-config\.[jt]s$'], 'renderer': 'static',
               'build_outputs': ['public'], 'family': 'react'},
    'angular': {'deps': {'@angular/core'}, 'files': [r'(?:^|/)angular\.json$'], 'renderer': 'csr',
                'build_outputs': ['dist'], 'family': 'angular'},
    'docusaurus': {'deps': {'@docusaurus/core'}, 'files': [r'(?:^|/)docusaurus\.config\.[jt]s$'], 'renderer': 'static',
                   'build_outputs': ['build'], 'family': 'react'},
    'vitepress': {'deps': {'vitepress'}, 'files': [r'(?:^|/)\.vitepress/config\.[cm]?[jt]s$'], 'renderer': 'static',
                  'build_outputs': ['.vitepress/dist', 'docs/.vitepress/dist'], 'family': 'vue'},
    'eleventy': {'deps': {'@11ty/eleventy'}, 'files': [r'(?:^|/)\.eleventy\.[cm]?js$', r'(?:^|/)eleventy\.config\.[cm]?js$'],
                 'renderer': 'static', 'build_outputs': ['_site'], 'family': 'templates'},
    'solid-start': {'deps': {'@solidjs/start', 'solid-start'}, 'files': [], 'renderer': 'hybrid',
                    'build_outputs': ['.output/public', 'dist'], 'family': 'solid'},
    'qwik': {'deps': {'@builder.io/qwik-city'}, 'files': [], 'renderer': 'hybrid', 'build_outputs': ['dist'], 'family': 'qwik'},
    'vue-spa': {'deps': {'vue'}, 'files': [], 'renderer': 'csr', 'build_outputs': ['dist'], 'family': 'vue', 'weak': True},
    'react-spa': {'deps': {'react-dom'}, 'files': [], 'renderer': 'csr', 'build_outputs': ['dist', 'build'], 'family': 'react', 'weak': True},
    'svelte-spa': {'deps': {'svelte'}, 'files': [], 'renderer': 'csr', 'build_outputs': ['dist', 'public'], 'family': 'svelte', 'weak': True},
    'preact-spa': {'deps': {'preact'}, 'files': [], 'renderer': 'csr', 'build_outputs': ['dist', 'build'], 'family': 'react', 'weak': True},
    'shopify-theme': {'deps': set(), 'files': [r'^(?:.*/)?layout/theme\.liquid$', r'^(?:.*/)?config/settings_schema\.json$'],
                      'renderer': 'server-liquid', 'build_outputs': [], 'family': 'liquid', 'needs_runtime': 'shopify'},
    'shopify-hydrogen': {'deps': {'@shopify/hydrogen'}, 'files': [], 'renderer': 'ssr', 'build_outputs': ['dist/client'], 'family': 'react'},
    'jekyll': {'deps': {'jekyll', 'github-pages'}, 'files': [r'^_config\.ya?ml$'], 'renderer': 'static',
               'build_outputs': ['_site'], 'family': 'liquid'},
    'hugo': {'deps': set(), 'files': [r'^(?:hugo|config)\.(?:toml|ya?ml|json)$', r'^(?:.*/)?layouts/_default/baseof\.html$'],
             'renderer': 'static', 'build_outputs': ['public'], 'family': 'go-template',
             'content': [(r'^(?:hugo|config)\.(?:toml|ya?ml)$', r'(?im)^\s*(?:baseURL|baseurl|theme)\s*[=:]')]},
    'wordpress-theme': {'deps': {'wordpress', 'roots/wordpress', 'johnpbloch/wordpress'}, 'files': [r'(?:^|/)wp-content/themes/', r'(?:^|/)wp-config(?:-sample)?\.php$'],
                        'renderer': 'server-php', 'build_outputs': [], 'family': 'php', 'needs_runtime': 'wordpress',
                        'content': [(r'(?:^|/)style\.css$', r'(?i)Theme Name\s*:'), (r'(?:^|/)functions\.php$', r'\b(?:add_action|add_theme_support|wp_enqueue_)')]},
    'wordpress-block-theme': {'deps': set(), 'files': [r'(?:^|/)theme\.json$'], 'renderer': 'server-php', 'build_outputs': [],
                              'family': 'php', 'needs_runtime': 'wordpress',
                              'content': [(r'(?:^|/)templates/[^/]+\.html$', r'<!--\s*wp:')], 'weak': True},
    'drupal': {'deps': {'drupal/core', 'drupal/core-recommended'}, 'files': [r'\.info\.yml$'], 'renderer': 'server-php', 'build_outputs': [],
               'family': 'twig', 'needs_runtime': 'drupal'},
    'laravel': {'deps': {'laravel/framework'}, 'files': [r'^artisan$', r'^resources/views/.+\.blade\.php$'], 'renderer': 'server-php',
                'build_outputs': [], 'family': 'blade', 'needs_runtime': 'php'},
    'symfony': {'deps': {'symfony/framework-bundle'}, 'files': [r'^templates/.+\.html\.twig$'], 'renderer': 'server-php', 'build_outputs': [],
                'family': 'twig', 'needs_runtime': 'php'},
    'magento': {'deps': {'magento/product-community-edition', 'magento/framework'}, 'files': [r'(?:^|/)registration\.php$'], 'renderer': 'server-php',
                'build_outputs': [], 'family': 'php', 'needs_runtime': 'php', 'weak': True},
    'django': {'deps': {'django'}, 'files': [r'(?:^|/)manage\.py$', r'(?:^|/)urls\.py$'], 'renderer': 'server-python', 'build_outputs': [],
               'family': 'jinja', 'needs_runtime': 'python'},
    'flask': {'deps': {'flask'}, 'files': [], 'renderer': 'server-python', 'build_outputs': [], 'family': 'jinja', 'needs_runtime': 'python'},
    'fastapi': {'deps': {'fastapi'}, 'files': [], 'renderer': 'server-python', 'build_outputs': [], 'family': 'jinja', 'needs_runtime': 'python', 'weak': True},
    'rails': {'deps': {'rails', 'railties'}, 'files': [r'^config/routes\.rb$', r'^app/views/'], 'renderer': 'server-ruby', 'build_outputs': [],
              'family': 'erb', 'needs_runtime': 'ruby'},
    'express': {'deps': {'express', 'koa', 'fastify', 'hapi'}, 'files': [], 'renderer': 'server-node', 'build_outputs': [],
                'family': 'node-templates', 'weak': True},
    'aspnet': {'deps': set(), 'files': [r'\.csproj$', r'\.cshtml$', r'\.razor$'], 'renderer': 'server-dotnet', 'build_outputs': [],
               'family': 'razor', 'needs_runtime': 'dotnet'},
    'ghost-theme': {'deps': set(), 'files': [r'(?:^|/)default\.hbs$', r'(?:^|/)post\.hbs$'], 'renderer': 'server-handlebars', 'build_outputs': [],
                    'family': 'handlebars', 'needs_runtime': 'ghost',
                    'content': [(r'(?:^|/)package\.json$', r'"engines"\s*:\s*\{[^}]*"ghost')]},
    'static-html': {'deps': set(), 'files': [r'(?:^|/)index\.html?$'], 'renderer': 'static', 'build_outputs': ['.'], 'family': 'html', 'weak': True},
}

ROUTERS = {
    'next': lambda paths: 'app' if any(re.match(r'^(?:src/)?app/(?:.*/)?page\.[jt]sx?$', p) for p in paths)
            else 'pages' if any(re.match(r'^(?:src/)?pages/', p) for p in paths) else 'unknown',
    'remix': lambda paths: 'flat-routes' if any(re.match(r'^app/routes/', p) for p in paths) else 'config',
    'react-spa': lambda paths: 'declared',
    'vue-spa': lambda paths: 'declared',
    'shopify-theme': lambda paths: 'json-templates' if any(re.match(r'^(?:.*/)?templates/[^/]+\.json$', p) for p in paths) else 'liquid-templates',
    'wordpress-theme': lambda paths: 'template-hierarchy',
    'wordpress-block-theme': lambda paths: 'block-template-hierarchy',
}

PACKAGE_MANAGERS = (('pnpm-lock.yaml', 'pnpm'), ('yarn.lock', 'yarn'), ('bun.lockb', 'bun'), ('bun.lock', 'bun'),
                    ('package-lock.json', 'npm'), ('npm-shrinkwrap.json', 'npm'))


def _deps_from_manifests(files):
    """dependency name (lowercase) -> manifest path; across npm, composer, pip, gem, go."""
    deps = {}
    for path, text in files.items():
        name = PurePosixPath(path).name
        if 'node_modules' in path:
            continue
        try:
            if name == 'package.json':
                document = json.loads(text)
                if isinstance(document, dict):
                    for section in ('dependencies', 'devDependencies', 'peerDependencies'):
                        for dep in (document.get(section) or {}):
                            deps.setdefault(dep.lower(), path)
            elif name == 'composer.json':
                document = json.loads(text)
                if isinstance(document, dict):
                    for section in ('require', 'require-dev'):
                        for dep in (document.get(section) or {}):
                            deps.setdefault(dep.lower(), path)
            elif name in {'requirements.txt', 'requirements-dev.txt', 'Pipfile'} or name == 'pyproject.toml':
                for match in re.finditer(r'(?im)^\s*["\']?([A-Za-z0-9_.\-]+)\s*(?:[=<>!~\[;"\']|$)', text):
                    deps.setdefault(match.group(1).lower(), path)
            elif name in {'Gemfile', 'gems.rb'}:
                for match in re.finditer(r'''(?m)^\s*gem\s+['"]([^'"]+)['"]''', text):
                    deps.setdefault(match.group(1).lower(), path)
            elif name == 'go.mod':
                for match in re.finditer(r'(?m)^\s*(?:require\s+)?([\w.\-/]+)\s+v\d', text):
                    deps.setdefault(match.group(1).lower(), path)
        except ValueError:
            continue
    return deps


def _version(files, manifest, dep):
    try:
        document = json.loads(files[manifest])
    except (KeyError, ValueError):
        return None
    for section in ('dependencies', 'devDependencies', 'peerDependencies', 'require', 'require-dev'):
        value = (document.get(section) or {}).get(dep) if isinstance(document, dict) else None
        if isinstance(value, str):
            match = re.search(r'\d+(?:\.\d+){0,2}', value)
            return match.group(0) if match else value
    return None


def detect_frameworks(snapshot):
    """Return the framework fingerprint for one snapshot.

    {primary, candidates:[{name, score, evidence}], family, router, renderer, version,
     package_manager, build: {command, output_dirs}, roots, needs_runtime, languages}
    """
    files = snapshot['files']
    inventory_paths = [row['path'] for row in snapshot.get('inventory', []) if row.get('kind') == 'file']
    paths = sorted(set(files) | set(inventory_paths) | set(snapshot.get('assets', {})))
    deps = _deps_from_manifests(files)
    candidates = []
    for name, rule in FRAMEWORKS.items():
        evidence, score = [], 0
        for dep in sorted(rule['deps']):
            if dep in deps:
                evidence.append({'kind': 'dependency', 'name': dep, 'file': deps[dep]})
                score += 3
        for pattern in rule['files']:
            regex = re.compile(pattern)
            hit = next((p for p in paths if regex.search(p) and 'node_modules' not in p), None)
            if hit:
                evidence.append({'kind': 'file', 'file': hit, 'pattern': pattern})
                score += 2
        for path_pattern, content_pattern in rule.get('content', []):
            path_regex, content_regex = re.compile(path_pattern), re.compile(content_pattern)
            hit = next((p for p in sorted(files) if path_regex.search(p) and content_regex.search(files[p])), None)
            if hit:
                evidence.append({'kind': 'content', 'file': hit, 'pattern': content_pattern})
                score += 3
        if score:
            if rule.get('weak'):
                score -= 1
            candidates.append({'name': name, 'score': score, 'family': rule['family'], 'evidence': evidence})
    candidates.sort(key=lambda c: (-c['score'], c['name']))
    # A meta-framework outranks the bare UI library it builds on.
    strong = [c for c in candidates if not FRAMEWORKS[c['name']].get('weak')]
    primary = (strong or candidates or [{'name': 'unknown', 'family': 'unknown'}])[0]
    name = primary['name']
    rule = FRAMEWORKS.get(name, {})
    manifest = next((e['file'] for e in primary.get('evidence', []) if e['kind'] == 'dependency'), None)
    version = None
    if manifest:
        version = _version(files, manifest, next(iter(sorted(rule.get('deps') or [''])), ''))
        for dep in sorted(rule.get('deps') or []):
            version = version or _version(files, manifest, dep)
    package_manager = next((pm for lock, pm in PACKAGE_MANAGERS
                            if any(PurePosixPath(p).name == lock for p in inventory_paths)), None)
    package_json = _site_package_json(files)
    if package_manager is None and package_json:
        package_manager = 'npm'
    scripts = {}
    if package_json:
        try:
            scripts = json.loads(files[package_json]).get('scripts') or {}
        except (ValueError, AttributeError):
            scripts = {}
    build_command = None
    for key in ('build', 'generate', 'export', 'build:prod', 'build:static'):
        if isinstance(scripts.get(key), str):
            build_command = [package_manager or 'npm', 'run', key]
            break
    if build_command is None:
        build_command = {'hugo': ['hugo', '--minify'], 'jekyll': ['bundle', 'exec', 'jekyll', 'build'],
                         'eleventy': ['npx', '@11ty/eleventy']}.get(name)
    root = posixpath.dirname(package_json) if package_json else ''
    output_dirs = [posixpath.join(root, d) if root else d for d in rule.get('build_outputs', [])]
    router = ROUTERS.get(name, lambda _: None)(paths)
    if name == 'next' and any(re.search(r"output\s*:\s*['\"]export['\"]", files.get(p, '')) for p in paths
                              if re.search(r'next\.config\.[cm]?[jt]s$', p)):
        output_dirs = [posixpath.join(root, 'out') if root else 'out']
    languages = sorted({PurePosixPath(p).suffix.lower() for p in files if PurePosixPath(p).suffix})
    return {
        'schema': 'framework-fingerprint/1.0',
        'primary': name, 'family': primary.get('family', 'unknown'), 'version': version,
        'router': router, 'renderer': rule.get('renderer', 'unknown'),
        'needs_runtime': rule.get('needs_runtime'),
        'package_manager': package_manager, 'package_json': package_json,
        'build': {'command': build_command, 'output_dirs': output_dirs, 'scripts': sorted(scripts)[:30],
                  'preview_script': next((k for k in ('preview', 'start', 'serve') if k in scripts), None)},
        'candidates': candidates[:8], 'source_extensions': languages[:60],
        'method': 'dependency manifests + configuration files + directory layout; no code executed',
    }


def _site_package_json(files):
    """The package.json that most likely owns the website (shallowest with a framework dep)."""
    manifests = sorted((p for p in files if PurePosixPath(p).name == 'package.json' and 'node_modules' not in p),
                       key=lambda p: (p.count('/'), p))
    for path in manifests:
        try:
            document = json.loads(files[path])
        except ValueError:
            continue
        if not isinstance(document, dict):
            continue
        names = {**(document.get('dependencies') or {}), **(document.get('devDependencies') or {})}
        if any(dep in names for rule in FRAMEWORKS.values() for dep in rule['deps']) or (document.get('scripts') or {}).get('build'):
            return path
    return manifests[0] if manifests else None
