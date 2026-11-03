---
name: explore-seo-urls
description: Confirm the public route catalog, depths and query navigation for any framework.
---

## Output per page
`depth` (int: number of non-empty path segments) and `query_links`:
`[{file, line, source_id, href, target_url}]` for links whose href has a `?query`.

## Hard rules
1. You set the catalog every other agent must match. Start from `routes` (deterministic
   candidates) and keep only public pages: drop layouts, partials, components, API
   endpoints and error/404 templates.
2. One row per public URL template. Dynamic segments keep their pattern
   (`/blog/[slug]`, `/products/:handle`) with `is_dynamic: true`, `instance_status: "template"`.
3. Add concrete instances only when a captured data file lists them (front matter files,
   JSON/YAML collections, generateStaticParams literals). Each gets `instance_status:
   "concrete"` and `route_family` = the pattern.
4. Base path, locale prefixes and redirects you cannot resolve go to `coverage.limitations`.

## Steps
1. `query_index(kind="route")` and `list_routes` to see candidates and their methods.
2. Check router config and conventions for this stack:
   - Next app/pages, Nuxt `pages/`, SvelteKit `+page`, Astro `src/pages`, Remix `app/routes`.
   - SPA routers (`<Route path>`, `createBrowserRouter`, Vue Router `routes: [...]`).
   - Shopify themes: `templates/*.json|liquid` map to `/`, `/products/:handle`,
     `/collections/:handle`, `/pages/:handle`, `/blogs/:blog/:article`, `/cart`, `/search`.
   - WordPress themes: `front-page.php`/`index.php` -> `/`, `single.php` -> `/:post`,
     `page.php` -> `/:page`, `archive.php`/`category.php` -> archives, `404.php` excluded.
   - Hugo/Jekyll/Eleventy: content files + `permalink`; Django/Flask/Rails/Laravel: route declarations.
3. Write extractor.py that iterates `routes`, filters, and emits rows; `test_script`.

## Example
Input route `{url: "/products/[handle]", template: "templates/product.json", is_dynamic: true}`
-> `{"url": "/products/[handle]", "template": "templates/product.json", "is_dynamic": true,
"instance_status": "template", "depth": 2, "query_links": [], "coverage": {"mode": "static-source",
"complete": false, "limitations": ["Product handles come from the Shopify store, not the repository."]}}`

## Done when
- No component/layout/endpoint rows remain; every dynamic family is a template row.
- `depth` matches the URL; `test_script` passes.
