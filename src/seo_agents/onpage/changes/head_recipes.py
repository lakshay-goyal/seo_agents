"""Where a per-route head/body change goes on each stack, whether a page owns its <head>, and the hosting platform.

Prefers the exploration stage's `tools/head_recipes.py` when present; the local table is the fallback so the on-page
stage works on any exploration output (audit §10 "Will it improve SEO on any framework?", §15).
"""

from __future__ import annotations

import re

# family/primary -> recipe. `per_route` = the stack can give each route its own head without extra tooling.
LOCAL_RECIPES = {
    'next-app': {'mechanism': 'export const metadata / generateMetadata in app/**/page.tsx; title.template + metadataBase in app/layout.tsx; '
                              'alternates.canonical; opengraph-image.tsx; JSON-LD <script type="application/ld+json"> in the page',
                 'where': 'route page file (app/<route>/page.tsx)', 'per_route': True},
    'next-pages': {'mechanism': 'next/head <Head> in pages/<route>.tsx', 'where': 'route page file', 'per_route': True},
    'nuxt': {'mechanism': 'useSeoMeta()/useHead() in pages/<route>.vue; app.head in nuxt.config for defaults',
             'where': 'route page file', 'per_route': True},
    'sveltekit': {'mechanism': '<svelte:head> in src/routes/<route>/+page.svelte', 'where': 'route +page.svelte', 'per_route': True},
    'remix': {'mechanism': 'export const meta (and links for canonical) in app/routes/<route>.tsx', 'where': 'route module', 'per_route': True},
    'astro': {'mechanism': 'layout <head> props (title/description/canonical from Astro.url) passed by each page',
              'where': 'src/pages/<route>.astro frontmatter -> layout props', 'per_route': True},
    'gatsby': {'mechanism': 'export const Head = () => (...) in the page/template component', 'where': 'page or template component', 'per_route': True},
    'angular': {'mechanism': 'Title and Meta services per route (route data) + Angular SSR/prerender for crawlers',
                'where': 'route component / route config', 'per_route': False},
    'spa': {'mechanism': 'prerender each route (vite-ssg, vite-plugin-prerender, Vike, react-snap) and set the head per route with '
                         'react-helmet-async / @unhead/vue / svelte-head; runtime-only document.title works for Googlebot but not '
                         'for social previews or most AI crawlers',
            'where': 'route page component (after prerendering is enabled)', 'per_route': False},
    'hugo': {'mechanism': 'front matter (title, description) + layouts/partials/head.html reading .Title/.Description/.Permalink',
             'where': 'content/<page>.md front matter', 'per_route': True},
    'jekyll': {'mechanism': 'front matter + jekyll-seo-tag ({% seo %}) in _layouts/default.html', 'where': '<page>.md front matter', 'per_route': True},
    'eleventy': {'mechanism': 'front matter/data cascade + layout <head>', 'where': 'page front matter', 'per_route': True},
    'shopify-theme': {'mechanism': 'page_title / page_description / canonical_url in layout/theme.liquid (meta-tags snippet); '
                                   'per-resource SEO fields in Shopify admin', 'where': 'Shopify admin SEO fields or snippets/meta-tags.liquid',
                      'per_route': True},
    'wordpress-theme': {'mechanism': 'wp_head() + an SEO plugin (Yoast/Rank Math) or add_theme_support("title-tag"); per-post fields in admin',
                        'where': 'WordPress admin (post SEO fields) or header.php', 'per_route': True},
    'django': {'mechanism': '{% block title %}/{% block meta %} in base.html overridden per template', 'where': 'route template', 'per_route': True},
    'flask': {'mechanism': 'Jinja {% block head %} overridden per template', 'where': 'route template', 'per_route': True},
    'laravel': {'mechanism': "@section('title') / @stack('meta') in the Blade view", 'where': 'route Blade view', 'per_route': True},
    'rails': {'mechanism': 'content_for :title / :head in the view, yield in layouts/application', 'where': 'route view', 'per_route': True},
    'express': {'mechanism': 'res.render(view, {title, description}) + layout head', 'where': 'route handler / view', 'per_route': True},
    'static-html': {'mechanism': 'each HTML file owns its <head>', 'where': 'the page HTML file', 'per_route': True},
}
SPA_PRIMARIES = re.compile(r'(?i)(?:react|vue|svelte|preact|solid|lit|vite|cra|create-react-app|angular|ember|qwik)[-_ ]?spa|^spa$|^vite|^cra$')
SHELL_FILES = re.compile(r'(?i)(?:^|/)(?:public/)?index\.html?$|(?:^|/)src/app\.html$|(?:^|/)_document\.[jt]sx?$|(?:^|/)app/layout\.[jt]sx?$|'
                         r'(?:^|/)layouts?/(?:default|base|baseof|app|application|main|theme)\b|(?:^|/)_layouts/|(?:^|/)layout/theme\.liquid$|'
                         r'(?:^|/)header\.php$|(?:^|/)templates/base\.html$|(?:^|/)app\.vue$|(?:^|/)\+layout\.svelte$|(?:^|/)root\.[jt]sx$')


def recipe_key(fingerprint):
    fp = fingerprint or {}
    primary = str(fp.get('primary') or fp.get('name') or '').lower()
    family = str(fp.get('family') or '').lower()
    router = str(fp.get('router') or '').lower()
    if primary.startswith('next'):
        return 'next-pages' if 'pages' in primary or router == 'pages' else 'next-app'
    for key in LOCAL_RECIPES:
        if key != 'spa' and (primary == key or primary.startswith(key)):
            return key
    if primary.startswith('wordpress'):
        return 'wordpress-theme'
    if primary.startswith(('11ty', 'eleventy')):
        return 'eleventy'
    if SPA_PRIMARIES.search(primary) or fp.get('renderer') == 'csr':
        return 'angular' if 'angular' in primary else 'spa'
    if family in ('html',) or primary in ('static', 'html'):
        return 'static-html'
    return primary or 'unknown'


STACK_ALIASES = {'vite-spa': 'spa', 'wordpress': 'wordpress-theme', 'server-template': 'django'}


def head_recipe(fingerprint):
    """{stack, mechanism, where, per_route, precondition, title/description/canonical/robots/og/json_ld?}."""
    try:  # exploration-stage recipes win when available
        from seo_agents.repo_analysis.tools.routing.head_recipes import head_recipe as shared
        found = shared(fingerprint)
        if isinstance(found, dict) and found.get('recipe') not in (None, 'unknown'):
            recipe = dict(found)
            recipe['stack'] = STACK_ALIASES.get(found['recipe'], found['recipe'])
            recipe.setdefault('where', ', '.join(found.get('files') or []) or 'route template')
            recipe['per_route'] = found.get('scope') != 'shared-shell'
            return recipe
    except Exception:  # noqa: BLE001 - optional module; any mismatch falls back to the local table
        pass
    key = recipe_key(fingerprint)
    recipe = dict(LOCAL_RECIPES.get(key) or {'mechanism': 'set the value in the route\'s own template/head', 'where': 'route template',
                                             'per_route': True})
    recipe['stack'] = key
    if key in ('spa', 'angular'):
        recipe['precondition'] = 'prerender each route (vite-ssg / Vike / react-snap / Angular SSR prerender)'
    return recipe


def is_shell_file(path):
    return bool(path and SHELL_FILES.search(str(path)))


def head_scope(fingerprint, page=None, site=None):
    """'per-route' | 'layout' | 'shared-shell'. shared-shell = every route is served the same static head (CSR SPA)."""
    page, site = page or {}, site or {}
    explicit = page.get('head_scope') or (site.get('head_scope') if isinstance(site.get('head_scope'), str) else None)
    if explicit:
        return explicit
    rendering = page.get('rendering') or {}
    raw, rendered = rendering.get('raw_html_words'), rendering.get('rendered_words')
    prerendered = raw is not None and bool(rendered) and raw >= 0.5 * rendered
    try:
        from seo_agents.repo_analysis.tools.routing.head_recipes import head_scope as shared
        found = shared(fingerprint)
        if found == 'shared-shell' and prerendered:
            return 'per-route'  # measured: the served HTML already carries the route content
        if found == 'shared-shell':
            return found
    except Exception:  # noqa: BLE001
        pass
    title = ((page.get('head') or {}).get('title') or {})
    edit = title.get('edit') if isinstance(title, dict) else None
    file = (edit or {}).get('file')
    key = recipe_key(fingerprint)
    shared_by = (edit or {}).get('shared_by') or []
    if key in ('spa', 'angular'):
        if not prerendered and (not file or re.search(r'(?i)(?:^|/)index\.html?$', file)):
            return 'shared-shell'
    if len(shared_by) > 1 or (file and is_shell_file(file) and key not in ('static-html',)):
        return 'layout'
    return 'per-route'


HOSTING_FILES = (('vercel', re.compile(r'(?:^|/)vercel\.json$')), ('netlify', re.compile(r'(?:^|/)(?:netlify\.toml|_redirects)$')),
                 ('cloudflare', re.compile(r'(?:^|/)(?:wrangler\.toml|_routes\.json)$')), ('firebase', re.compile(r'(?:^|/)firebase\.json$')),
                 ('nginx', re.compile(r'(?:^|/)nginx[^/]*\.conf$|(?:^|/)conf\.d/')), ('apache', re.compile(r'(?:^|/)\.htaccess$')))
REDIRECT_WHERE = {
    'vercel': 'Vercel project > Domains (set the apex to redirect permanently, 308) or vercel.json "redirects" with "permanent": true',
    'netlify': 'netlify.toml [[redirects]] with status = 301 (or _redirects "https://apex/* https://www.apex/:splat 301!")',
    'cloudflare': 'Cloudflare Redirect Rules / Bulk Redirects with status 301',
    'firebase': 'firebase.json hosting.redirects with "type": 301',
    'nginx': 'nginx server block: return 301 https://<canonical-host>$request_uri;',
    'apache': '.htaccess: RewriteRule ^(.*)$ https://<canonical-host>/$1 [R=301,L]',
}


def hosting(site, file_paths=()):
    """Hosting platform from exploration facts (explicit > response headers > config files)."""
    explicit = site.get('hosting')
    if isinstance(explicit, dict):
        explicit = explicit.get('platform') or explicit.get('name') or explicit.get('value')
    if isinstance(explicit, str) and explicit:
        return explicit.lower()
    headers = ((site.get('served') or {}).get('home_headers') or {})
    server = ' '.join(str(headers.get(k) or '') for k in ('server', 'x-vercel-id', 'x-nf-request-id', 'cf-ray')).lower()
    if headers.get('x-vercel-id') or 'vercel' in server:
        return 'vercel'
    if headers.get('x-nf-request-id') or 'netlify' in server:
        return 'netlify'
    if headers.get('cf-ray') or 'cloudflare' in server:
        return 'cloudflare'
    for name, pattern in HOSTING_FILES:
        if any(pattern.search(p) for p in file_paths):
            return name
    return None

SOFT_404_WHERE = {
    'vercel': 'vercel.json: replace the catch-all rewrite "/(.*)" -> /index.html with rewrites for real routes only, and ship a '
              '404.html (served with status 404)',
    'netlify': '_redirects / netlify.toml: list real SPA routes with status 200 and let unknown paths fall through to 404.html',
    'cloudflare': 'Pages: add a 404.html (disables the SPA fallback) or a Worker that returns 404 for unknown routes',
    'firebase': 'firebase.json: rewrite only real routes to /index.html; unknown paths serve 404.html',
    'nginx': 'nginx: try_files $uri $uri/ =404 for unknown paths (rewrite only real routes to /index.html)',
    'apache': '.htaccess: ErrorDocument 404 /404.html and rewrite only real routes',
}


class OnpageHeadRecipes:
    """Tool: where the framework sets per-route head tags, head scope and hosting for change targets."""

    recipe_key = staticmethod(recipe_key)
    head_recipe = staticmethod(head_recipe)
    is_shell_file = staticmethod(is_shell_file)
    head_scope = staticmethod(head_scope)
    hosting = staticmethod(hosting)
