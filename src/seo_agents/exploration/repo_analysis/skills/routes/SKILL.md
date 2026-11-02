---
name: explore-routes
description: Discover every page route in any codebase from file-system conventions and code-declared routers.
---

Routes come from two places, and both are checked for every repository. Every row
carries `route`, `kind` (page, content-page, template, layout, not-found, endpoint,
server-route, route-prefix), `adapter` (the convention family), `is_dynamic` and
the source evidence.

1. **File-system conventions** (`conventions`): a path regex per adapter, first
   match wins. A convention may be gated on a marker path (`requires_path`) and
   may produce the URL from a named `route` group, a `route_map` keyed by the
   captured template `name`, or a `route_template` using captured groups.
   - Next.js app/pages (`next-app`, `file-pages`), SvelteKit, Remix flat routes,
     Nuxt/Astro `pages/` (including `[...slug]`), TanStack file routes.
   - Shopify themes (`shopify-theme`, gated on `layout/theme.liquid`):
     `templates/product.json` → `/products/:handle`, `collection` →
     `/collections/:handle`, `page.<variant>` → `/pages/:handle` (variant kept),
     `article` → `/blogs/:blog/:article`, `customers/login` → `/account/login`, 404.
   - WordPress classic (`wordpress-template-hierarchy`, gated on `functions.php`)
     and block themes (`wordpress-block-theme`, gated on `theme.json`):
     front-page/home/index → `/`, single → `/:post`, page → `/:page`,
     `page-{slug}` → `/{slug}`, `single-{cpt}`, `archive-{cpt}`, category, tag,
     author, search, 404; header/footer/parts are layouts; template-parts ignored.
   - Ghost (`.hbs`), Hugo (`layouts/`, `content/` with `_index`), Jekyll
     (`_posts`, root Markdown pages), server template/view folders (Django/Jinja,
     Twig, ERB, EJS, Handlebars, Blade), Angular component templates, plain HTML/PHP.
   Route groups `(group)`, slots `@slot`, `index`/`_index` and private `_`
   segments are normalised away; `[param]`, `:param`, `$param`, `{param}` and `*`
   mark dynamic routes. `dot_segments` turns `blog.$slug` into `/blog/:slug`.
2. **Code declarations** (`declarations`): router configs and server handlers.
   Structural `parser`s keep nesting: `jsx-routes` (nested `<Route>` trees, index
   routes), `object-routes` (`{ path, children: [...] }` in React Router, Vue
   Router, Angular `Routes`), `django-urls` (`include()` prefixes),
   `rails-routes` (`root`, `get`, `resources`/`resource`, `namespace`, `scope`,
   nested resources). Regex declarations cover Express/Fastify, NestJS,
   Flask/FastAPI, Laravel (`Route::get`, `Route::resource`), Gatsby `createPage`
   and front-matter `permalink`/`url`. `requires` limits router-object parsing to
   files that look like router code.

Next.js `basePath` and `i18n.locales` from `next.config.*` are applied to Next
routes (`file_route` keeps the file-derived path; `locale_variants` lists
non-default locale URLs).

Record the source file of each route so an agent can edit the page itself.
Layout/template rows are the shared shells that wrap pages.

Runtime profile: [`profiles/routes.json`](../../profiles/routes.json), validated by
`profiles/schema.json`; behaviour is tested in `tests/test_skill_profiles.py`.
This file is human documentation only.
