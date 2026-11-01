---
name: explore-routes
description: Discover every page route in any codebase from file-system conventions and code-declared routers.
---

Routes come from two places, and both are checked for every repository:

1. **File-system conventions** (`conventions`): a path regex with a named
   `route` group. Covers app/pages directories, `+page` files, `src/pages`,
   `content/` trees, templates and plain `.html`/`.php` files. Route groups
   `(group)`, slots `@slot`, `index`/`_index` and private `_` segments are
   normalised away; `[param]`, `:param`, `$param`, `{param}` and `*` mark dynamic
   routes. `dot_segments` turns `blog.$slug` into `/blog/:slug`.
2. **Code declarations** (`declarations`): router configs and server handlers -
   `<Route path>`, `{ path: '...' }` arrays, `@app.route`, `path('...')`,
   `app.get('/...')`, `Route::get`, Rails `get`, front matter `permalink`.
   `requires` limits a pattern to files that look like router code.

Record the source file of each route so an agent can edit the page itself.
Layout/template rows are the shared shells that wrap pages.

```json profile
{
  "name": "routes",
  "conventions": [
    {"pattern": "^(?:src/)?app/(?P<route>(?:.*/)?)page\\.(?:tsx|jsx|ts|js|mdx|md)$", "kind": "page"},
    {"pattern": "^(?:src/)?app/(?P<route>(?:.*/)?)layout\\.(?:tsx|jsx|ts|js)$", "kind": "layout"},
    {"pattern": "^(?:src/)?app/(?P<route>(?:.*/)?)(?:not-found|error)\\.(?:tsx|jsx|ts|js)$", "kind": "not-found"},
    {"pattern": "^(?:src/)?app/(?P<route>(?:.*/)?)route\\.(?:ts|js)$", "kind": "endpoint"},
    {"pattern": "^(?:src/)?pages/api/(?P<route>.+)\\.(?:tsx|jsx|ts|js)$", "kind": "endpoint"},
    {"pattern": "^(?:src/|app/)?pages/(?P<route>.+)\\.(?:tsx|jsx|ts|js|mdx|md|vue|astro|svelte|html)$", "kind": "page", "exclude": "(?:^|/)_(?:app|document|error)$|^(?:404|500)$|^api/"},
    {"pattern": "^(?:src/|app/)?pages/(?P<route>404|500)\\.\\w+$", "kind": "not-found"},
    {"pattern": "^src/routes/(?P<route>(?:.*/)?)\\+page\\.svelte$", "kind": "page"},
    {"pattern": "^src/routes/(?P<route>(?:.*/)?)\\+layout\\.svelte$", "kind": "layout"},
    {"pattern": "^src/routes/(?P<route>(?:.*/)?)\\+server\\.(?:ts|js)$", "kind": "endpoint"},
    {"pattern": "^app/routes/(?P<route>[^/]+?)(?:/route)?\\.(?:tsx|jsx|ts|js|mdx)$", "kind": "page", "dot_segments": true},
    {"pattern": "^(?:src/)?routes/(?P<route>[^+]+)\\.(?:tsx|jsx)$", "kind": "page", "exclude": "(?:^|/)__root$|\\.lazy$"},
    {"pattern": "^(?:src/|app/)?layouts?/(?P<route>.+)\\.(?:vue|astro|svelte|tsx|jsx|html|njk|liquid)$", "kind": "layout"},
    {"pattern": "^(?:src/)?_(?:includes|layouts)/(?P<route>.+)\\.(?:html|njk|liquid)$", "kind": "layout"},
    {"pattern": "^_posts/\\d{4}-\\d{2}-\\d{2}-(?P<route>.+)\\.(?:md|markdown|html)$", "kind": "page"},
    {"pattern": "^(?:src/)?content/(?P<route>.+?)(?:/_?index)?\\.(?:md|mdx|markdown)$", "kind": "content-page"},
    {"pattern": "^(?:docs|blog|posts)/(?P<route>.+?)\\.(?:md|mdx)$", "kind": "content-page"},
    {"pattern": "^(?:.*/)?templates/(?P<route>.+)\\.(?:html|jinja|j2|twig)$", "kind": "template"},
    {"pattern": "^(?:.*/)?views/(?P<route>(?!layouts/|partials/|components/).+?)(?:\\.html)?\\.(?:erb|haml|slim|ejs|pug|hbs|handlebars|njk)$", "kind": "template"},
    {"pattern": "^resources/views/(?P<route>(?!layouts/|components/|partials/).+?)\\.blade\\.php$", "kind": "template"},
    {"pattern": "^(?:public/|static/|src/|docs/|site/|www/|dist/)?(?P<route>(?![^/]*/node_modules/)[^.]+?)\\.html?$", "kind": "page", "exclude": "(?:^|/)(?:_|partials/|includes/|components/|layouts?/|templates/|views/)|(?:^|/)(?:README|LICENSE)$"},
    {"pattern": "^(?:public/)?(?P<route>(?!vendor/|app/|config/|routes/|resources/|database/|tests?/|src/|lib/|includes?/)[^./]+)\\.php$", "kind": "page", "exclude": "^(?:functions|wp-config|header|footer|sidebar)$"}
  ],
  "declarations": [
    {"glob": "*.[jt]sx", "regex": "<Route\\b[^>]*?\\bpath\\s*=\\s*[{]?\\s*['\"`]([^'\"`]*)['\"`]", "kind": "page"},
    {"glob": "*.[jt]s*", "regex": "\\bpath\\s*:\\s*['\"`](/?[^'\"`]*)['\"`]", "kind": "page", "requires": "(?:createBrowserRouter|createHashRouter|createMemoryRouter|useRoutes|RouteObject|createRoute|createRouter|VueRouter|RouteRecordRaw|Routes\\b|RouterModule|provideRouter)"},
    {"glob": "*.vue", "regex": "\\bpath\\s*:\\s*['\"`](/?[^'\"`]*)['\"`]", "kind": "page", "requires": "createRouter"},
    {"glob": "*.[jt]s", "regex": "\\b(?:app|router|server|fastify|api)\\.(?:get|all)\\s*\\(\\s*['\"`](/[^'\"`]*)['\"`]", "kind": "server-route"},
    {"glob": "*.ts", "regex": "@Get\\s*\\(\\s*['\"`]([^'\"`]*)['\"`]", "kind": "server-route"},
    {"glob": "*.py", "regex": "@\\w+\\.(?:route|get|api_route)\\s*\\(\\s*['\"]([^'\"]*)['\"]", "kind": "server-route"},
    {"glob": "urls.py", "regex": "\\b(?:re_)?path\\s*\\(\\s*r?['\"]([^'\"]*)['\"]", "kind": "server-route"},
    {"glob": "routes.rb", "regex": "^\\s*(?:get|resources?|root)\\s*\\(?\\s*['\":]?([\\w/:\\-#]+)", "kind": "server-route"},
    {"glob": "web.php", "regex": "Route::(?:get|any|match|view)\\s*\\(\\s*['\"]([^'\"]*)['\"]", "kind": "server-route"},
    {"glob": "gatsby-node.*", "regex": "createPage\\s*\\(\\s*\\{[^}]*?path\\s*:\\s*[`'\"]([^`'\"]+)", "kind": "page"},
    {"glob": "*.md*", "regex": "^permalink:\\s*['\"]?([^'\"\\n]+)", "kind": "page"},
    {"glob": "*.njk", "regex": "^permalink:\\s*['\"]?([^'\"\\n]+)", "kind": "page"}
  ],
  "signals": [],
  "gaps": [],
  "edit_guidance": "Edit the route's source file for page-specific SEO; edit layout rows for site-wide head, header and footer changes. Code-declared routes point at the component/view rendered for that URL."
}
```
