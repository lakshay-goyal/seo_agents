"""Where a per-route head change (title, description, canonical, robots, Open Graph, JSON-LD) goes, per stack.

Pure data keyed by the framework fingerprint; no code is executed. The on-page stage and the site model use it
to decide whether a head edit is safe per route or would land in a shared shell and duplicate across every URL
(the PR #8 failure: a Vite SPA's index.html is served for every route).

    head_recipe(fingerprint) -> {
        framework, scope, mechanism, files, title, description, canonical, robots, og, json_ld,
        precondition, notes, alternatives
    }
    head_scope(fingerprint) -> 'per-route' | 'shared-shell' | 'mixed'

`scope`:
- per-route:    every page/template declares its own head (SSR/SSG frameworks, server templates);
- shared-shell: one static HTML shell serves every route; per-route head needs prerendering first
                (`precondition`), otherwise only a runtime head manager (seen by Googlebot, not by social or
                most AI crawlers);
- mixed:        per-route head exists, but a layout/theme can override or a common include supplies defaults.
"""

from __future__ import annotations

SHARED_SHELL = {'react-spa', 'vue-spa', 'svelte-spa', 'preact-spa', 'angular', 'static-spa'}
MIXED = {'wordpress-theme', 'wordpress-block-theme', 'shopify-theme', 'ghost-theme', 'drupal', 'magento', 'gatsby'}

_RECIPES = {
    'next-app': {
        'mechanism': 'Metadata API: `export const metadata` / `export async function generateMetadata()` in page.tsx; '
                     'defaults + `title.template` + `metadataBase` in the nearest layout.tsx',
        'files': ['app/**/page.{tsx,jsx,ts,js}', 'app/**/layout.{tsx,jsx}'],
        'title': 'metadata.title (layout: title: {template: "%s | Brand", default})',
        'description': 'metadata.description',
        'canonical': 'metadata.alternates.canonical (relative path resolved against metadataBase)',
        'robots': 'metadata.robots; app/robots.ts for robots.txt',
        'og': 'metadata.openGraph / metadata.twitter; app/**/opengraph-image.{png,tsx}',
        'json_ld': '<script type="application/ld+json" dangerouslySetInnerHTML={{__html: JSON.stringify(data)}} /> in page.tsx',
        'sitemap': 'app/sitemap.ts',
    },
    'next-pages': {
        'mechanism': '`import Head from "next/head"` inside each page component; site defaults in pages/_app or a shared <Seo> component',
        'files': ['pages/**/*.{tsx,jsx,ts,js}', 'pages/_app.*', 'pages/_document.*'],
        'title': '<Head><title>…</title></Head>', 'description': '<Head><meta name="description" …/></Head>',
        'canonical': '<Head><link rel="canonical" href=…/></Head>', 'robots': '<Head><meta name="robots" …/></Head>',
        'og': '<Head><meta property="og:…"/></Head>', 'json_ld': '<Head><script type="application/ld+json" …/></Head>',
        'notes': ['<html lang> lives in pages/_document via <Html lang=…>.'],
    },
    'nuxt': {
        'mechanism': '`useSeoMeta({...})` / `useHead({...})` in the page <script setup>; defaults in nuxt.config `app.head`',
        'files': ['pages/**/*.vue', 'app.vue', 'layouts/*.vue', 'nuxt.config.ts'],
        'title': 'useSeoMeta({title}) (titleTemplate in app.head / useHead)', 'description': 'useSeoMeta({description})',
        'canonical': "useHead({link: [{rel: 'canonical', href}]})", 'robots': 'useSeoMeta({robots}); routeRules for X-Robots-Tag',
        'og': 'useSeoMeta({ogTitle, ogDescription, ogImage})', 'json_ld': "useHead({script: [{type: 'application/ld+json', innerHTML}]}) or useSchemaOrg",
    },
    'sveltekit': {
        'mechanism': '<svelte:head> in +page.svelte (data from +page.ts/+page.server.ts load); defaults in +layout.svelte',
        'files': ['src/routes/**/+page.svelte', 'src/routes/**/+layout.svelte', 'src/app.html'],
        'title': '<svelte:head><title>…</title></svelte:head>', 'description': '<svelte:head><meta name="description" …></svelte:head>',
        'canonical': '<svelte:head><link rel="canonical" href={$page.url.href}></svelte:head>', 'robots': '<svelte:head><meta name="robots" …>',
        'og': '<svelte:head><meta property="og:…"></svelte:head>', 'json_ld': '{@html `<script type="application/ld+json">…</script>`} inside <svelte:head>',
        'notes': ['<html lang> is in src/app.html (%lang% placeholder via hooks for i18n).'],
    },
    'remix': {
        'mechanism': '`export const meta: MetaFunction = () => [...]` in the route module; links export for canonical',
        'files': ['app/routes/*.{tsx,jsx}', 'app/root.tsx'],
        'title': 'meta: [{title}]', 'description': "meta: [{name: 'description', content}]",
        'canonical': "meta: [{tagName: 'link', rel: 'canonical', href}] (or links export)", 'robots': "meta: [{name: 'robots', content}]",
        'og': "meta: [{property: 'og:…', content}]", 'json_ld': "meta: [{'script:ld+json': {...}}]",
    },
    'astro': {
        'mechanism': 'Props passed from the page to the shared layout, which renders <head>; per-page front matter for .md/.mdx',
        'files': ['src/pages/**/*.{astro,md,mdx}', 'src/layouts/*.astro'],
        'title': '<Layout title=…> -> <title>{title}</title> in the layout', 'description': '<Layout description=…>',
        'canonical': '<link rel="canonical" href={new URL(Astro.url.pathname, Astro.site)}> in the layout',
        'robots': 'layout prop', 'og': 'layout props / astro-seo', 'json_ld': '<script type="application/ld+json" set:html={JSON.stringify(data)} />',
    },
    'gatsby': {
        'mechanism': '`export const Head = () => (...)` (Gatsby Head API) in the page/template; older sites use react-helmet <Seo>',
        'files': ['src/pages/**/*.{js,jsx,tsx}', 'src/templates/*.{js,jsx,tsx}', 'src/components/seo.*'],
        'title': 'export const Head = () => <title>…</title>', 'description': 'Head API <meta name="description">',
        'canonical': 'Head API <link rel="canonical">', 'robots': 'Head API <meta name="robots">', 'og': 'Head API <meta property="og:…">',
        'json_ld': 'Head API <script type="application/ld+json">',
    },
    'docusaurus': {
        'mechanism': 'Front matter (title, description, image, slug) per doc/blog page; themeConfig.metadata site-wide; <Head> in pages',
        'files': ['docs/**/*.md*', 'blog/**/*.md*', 'src/pages/**', 'docusaurus.config.*'],
        'title': 'front matter title', 'description': 'front matter description', 'canonical': 'automatic from url + baseUrl',
        'robots': 'themeConfig.metadata / <Head>', 'og': 'front matter image', 'json_ld': '<Head><script type="application/ld+json">',
    },
    'vitepress': {
        'mechanism': 'Front matter (title, description, head) per page; site `head` / `transformHead` in .vitepress/config',
        'files': ['**/*.md', '.vitepress/config.*'],
        'title': 'front matter title', 'description': 'front matter description', 'canonical': 'transformHead hook',
        'robots': 'front matter head', 'og': 'front matter head', 'json_ld': 'front matter head / transformHead',
    },
    'angular': {
        'mechanism': 'Title and Meta services (or route `title`) per routed component; needs SSR/prerender (@angular/ssr) for crawlers',
        'files': ['src/app/**/*.ts', 'src/index.html'],
        'title': "route `title` or Title.setTitle()", 'description': "Meta.updateTag({name: 'description', content})",
        'canonical': 'DOCUMENT link element via a service', 'robots': "Meta.updateTag({name: 'robots'})",
        'og': 'Meta.updateTag({property: "og:…"})', 'json_ld': 'Renderer2 script element',
        'precondition': 'prerender/SSR (@angular/ssr with prerender: true) so the served HTML carries per-route head',
    },
    'vite-spa': {
        'mechanism': 'Shared index.html shell: one static <head> for every route. Per-route head requires prerendering '
                     '(vite-ssg / vite-plugin-ssr (Vike) / react-snap / vite-react-ssg); without it only a runtime manager '
                     '(react-helmet-async, @vueuse/head, svelte-head, document.title=) which Googlebot renders but social '
                     'scrapers and most AI crawlers do not.',
        'files': ['index.html', 'src/main.*', 'src/App.*'],
        'title': 'shell <title> = site-wide default only; per-route via prerender + head manager in the route component',
        'description': 'shell <meta name="description"> = site-wide default only',
        'canonical': 'NEVER a fixed canonical in the shared shell (every route would canonicalise to it); per-route after prerender',
        'robots': 'shell meta robots applies to every route', 'og': 'per-route only after prerender; shell OG = site defaults',
        'json_ld': 'site-wide WebSite/Person JSON-LD may live in the shell; page-specific types need prerender',
        'precondition': 'prerender (vite-ssg / Vike / react-snap) so each route has its own HTML file',
        'alternatives': ['Migrate routes to a meta-framework (Next/Remix/Astro/Nuxt/SvelteKit) for per-route head.',
                         'Host-level: serve per-route HTML files generated at build time.'],
    },
    'static-html': {
        'mechanism': 'Each .html file owns its <head>', 'files': ['**/*.html'],
        'title': '<title> in the page file', 'description': '<meta name="description"> in the page file',
        'canonical': '<link rel="canonical"> in the page file', 'robots': '<meta name="robots"> in the page file',
        'og': '<meta property="og:…"> in the page file', 'json_ld': '<script type="application/ld+json"> in the page file',
    },
    'hugo': {
        'mechanism': 'Front matter (title, description, images, aliases) per content file; <head> partial in layouts/partials (or theme)',
        'files': ['content/**/*.md', 'layouts/partials/head*.html', 'layouts/_default/baseof.html', 'themes/*/layouts/**'],
        'title': 'front matter title (+ site title in the head partial)', 'description': 'front matter description -> head partial',
        'canonical': '<link rel="canonical" href="{{ .Permalink }}"> in the head partial', 'robots': 'front matter robots / head partial',
        'og': 'internal template "_internal/opengraph.html" or a partial', 'json_ld': 'head partial with page params',
        'notes': ['Override a theme partial by copying it to layouts/partials/ with the same path, never edit themes/ in place.'],
    },
    'jekyll': {
        'mechanism': 'Front matter (title, description, image, permalink) + jekyll-seo-tag ({% seo %}) in _includes/head.html / _layouts',
        'files': ['**/*.md', '_layouts/*.html', '_includes/head*.html', '_config.yml'],
        'title': 'front matter title', 'description': 'front matter description (jekyll-seo-tag)', 'canonical': 'jekyll-seo-tag (url + baseurl)',
        'robots': 'front matter / head include', 'og': 'jekyll-seo-tag + front matter image', 'json_ld': 'jekyll-seo-tag or head include',
    },
    'eleventy': {
        'mechanism': 'Front matter / directory data per page; <head> in the base layout (_includes/layouts/base.*)',
        'files': ['**/*.{md,njk,liquid,html}', '_includes/**', '_data/**'],
        'title': 'front matter title -> base layout', 'description': 'front matter description -> base layout',
        'canonical': '<link rel="canonical" href="{{ page.url | absoluteUrl }}"> in the base layout',
        'robots': 'front matter -> base layout', 'og': 'front matter -> base layout', 'json_ld': 'base layout using page data',
    },
    'shopify-theme': {
        'mechanism': 'layout/theme.liquid <head> renders page_title / page_description / canonical_url for every template; '
                     'per-resource values come from Shopify admin SEO fields (product/collection/page/article), not theme code',
        'files': ['layout/theme.liquid', 'snippets/meta-tags.liquid', 'sections/*.liquid', 'templates/*.json'],
        'title': 'Shopify admin: resource SEO title (theme renders {{ page_title }})', 'description': 'admin SEO description ({{ page_description }})',
        'canonical': '{{ canonical_url }} in theme.liquid', 'robots': 'templates/robots.txt.liquid; noindex via metafields',
        'og': 'snippets/meta-tags.liquid', 'json_ld': 'section/snippet using {{ product | structured_data }} or a JSON-LD snippet',
        'notes': ['Content values are platform data (edit kind: platform/cms), not theme literals.'],
    },
    'wordpress': {
        'mechanism': "wp_head() in header.php emits the head; title via add_theme_support('title-tag'); description/canonical/OG/JSON-LD "
                     'usually from an SEO plugin (Yoast/Rank Math/SEOPress) per post, or theme functions.php hooks',
        'files': ['header.php', 'functions.php', 'templates/*.html', 'parts/header.html'],
        'title': "add_theme_support('title-tag') + post title / SEO plugin field", 'description': 'SEO plugin per-post field (or wp_head hook)',
        'canonical': 'WordPress core rel_canonical / SEO plugin', 'robots': 'Settings › Reading / SEO plugin / wp_robots filter',
        'og': 'SEO plugin', 'json_ld': 'SEO plugin schema graph or a wp_head hook in functions.php',
        'notes': ['Per-post values live in the database (edit kind: cms); theme files hold the defaults and hooks.'],
    },
    'ghost-theme': {
        'mechanism': '{{ghost_head}} in default.hbs; per-post meta title/description/OG from Ghost admin',
        'files': ['default.hbs'], 'title': 'Ghost admin meta title', 'description': 'Ghost admin meta description',
        'canonical': '{{ghost_head}} (admin canonical URL)', 'robots': '{{ghost_head}}', 'og': 'Ghost admin', 'json_ld': '{{ghost_head}}',
    },
    'drupal': {
        'mechanism': 'Metatag module per entity; html.html.twig renders head placeholders', 'files': ['templates/html.html.twig'],
        'title': 'Metatag module', 'description': 'Metatag module', 'canonical': 'Metatag module', 'robots': 'Metatag module',
        'og': 'Metatag OG submodule', 'json_ld': 'Schema.org Metatag module',
    },
    'server-template': {
        'mechanism': 'Base layout template defines overridable head blocks; each view/template overrides them',
        'files': ['templates/base.html', 'resources/views/layouts/*.blade.php', 'app/views/layouts/application.html.erb', 'views/layout.*'],
        'title': 'Django/Jinja {% block title %}, Blade @section(\'title\') / @yield, Rails content_for :title / provide, EJS/Pug locals',
        'description': 'a meta_description block/section/local set per view', 'canonical': 'block/section using request.build_absolute_uri / url()->current() / request.original_url',
        'robots': 'per-view block', 'og': 'per-view block', 'json_ld': 'per-view block with a <script type="application/ld+json">',
    },
}

FRAMEWORK_RECIPE = {
    'next': None,  # router-dependent
    'nuxt': 'nuxt', 'sveltekit': 'sveltekit', 'remix': 'remix', 'astro': 'astro', 'gatsby': 'gatsby',
    'docusaurus': 'docusaurus', 'vitepress': 'vitepress', 'angular': 'angular',
    'react-spa': 'vite-spa', 'vue-spa': 'vite-spa', 'svelte-spa': 'vite-spa', 'preact-spa': 'vite-spa',
    'solid-start': 'remix', 'qwik': 'remix', 'shopify-hydrogen': 'remix',
    'static-html': 'static-html', 'hugo': 'hugo', 'jekyll': 'jekyll', 'eleventy': 'eleventy',
    'shopify-theme': 'shopify-theme', 'wordpress-theme': 'wordpress', 'wordpress-block-theme': 'wordpress',
    'ghost-theme': 'ghost-theme', 'drupal': 'drupal', 'magento': 'server-template',
    'django': 'server-template', 'flask': 'server-template', 'fastapi': 'server-template', 'laravel': 'server-template',
    'symfony': 'server-template', 'rails': 'server-template', 'express': 'server-template', 'aspnet': 'server-template',
}


def _framework(fingerprint):
    if isinstance(fingerprint, str):
        return fingerprint, None
    fingerprint = fingerprint or {}
    return fingerprint.get('primary') or 'unknown', fingerprint.get('router')


def _prerendered(fingerprint):
    """Evidence that an SPA already prerenders (then head can be per-route)."""
    if isinstance(fingerprint, str):
        return False
    scripts = ' '.join((fingerprint.get('build') or {}).get('scripts') or [])
    deps = ' '.join(e.get('name', '') for c in fingerprint.get('candidates') or [] for e in c.get('evidence') or [])
    return any(word in scripts + ' ' + deps for word in ('vite-ssg', 'react-snap', 'vike', 'vite-plugin-ssr', 'prerender',
                                                          'vite-react-ssg', 'react-static'))


def head_scope(fingerprint):
    """'per-route' | 'shared-shell' | 'mixed' for the site's head."""
    framework, _ = _framework(fingerprint)
    if framework in SHARED_SHELL:
        return 'per-route' if _prerendered(fingerprint) else 'shared-shell'
    if framework in MIXED:
        return 'mixed'
    if framework == 'unknown':
        return 'mixed'
    return 'per-route'


def head_recipe(fingerprint):
    """Where per-route title/description/canonical/robots/OG/JSON-LD go for this stack (see module doc)."""
    framework, router = _framework(fingerprint)
    key = FRAMEWORK_RECIPE.get(framework)
    if framework == 'next':
        key = 'next-pages' if router == 'pages' else 'next-app'
    recipe = dict(_RECIPES.get(key) or {
        'mechanism': 'Unknown stack: find the template that renders <head> and whether it is shared by every route.',
        'files': [], 'title': None, 'description': None, 'canonical': None, 'robots': None, 'og': None, 'json_ld': None})
    scope = head_scope(fingerprint)
    recipe.setdefault('precondition', None)
    if scope != 'shared-shell' and key == 'vite-spa':
        recipe['precondition'] = None  # already prerendered
    recipe.setdefault('notes', [])
    recipe.setdefault('alternatives', [])
    return {'framework': framework, 'recipe': key or 'unknown', 'scope': scope, **recipe}


__all__ = ['head_recipe', 'head_scope']
