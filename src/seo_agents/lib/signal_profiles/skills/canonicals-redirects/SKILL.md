---
name: explore-canonicals-redirects
description: Find canonical declarations, redirect vs rewrite rules, trailing-slash policy, 404 handling and soft-404 risk in code and hosting config.
---

Canonical values come from the canonical tag/property itself (`<link rel="canonical">`,
`alternates.canonical`), never from neighbouring lines; template expressions are tagged
`value_origin: expression` with a null value.

Every hosting/framework rule becomes `{from, to, status, kind: redirect|rewrite, file, line}` in
`redirect_rules`; `redirects` is the redirect-only view. Parsed sources: `_redirects`, `netlify.toml`
`[[redirects]]` (status 200 = rewrite), `vercel.json` (`redirects` default 308, `rewrites` are never
redirects), `firebase.json`, `staticwebapp.config.json`, `.htaccess` (`RewriteRule` without `[R]` is a
rewrite), nginx `return 301`/`rewrite`, `next.config` `redirects()`/`rewrites()`, Nuxt `routeRules`, Astro
`redirects:`, Gatsby `createRedirect`, Hugo `aliases` and Jekyll `redirect_from` (meta-refresh).

`soft_404_risks` lists host catch-all rewrites to the app shell (`/(.*)` -> `/index.html`) and client
catch-all routes (`path="*"`, `'**'`, `/:pathMatch(.*)*`, Nuxt `error.vue`): unknown URLs then answer 200.

Runtime profile: [`profiles/canonicals-redirects.json`](../../profiles/canonicals-redirects.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). Python post-processing: `agents/canonicals_redirects.py`. Every backticked token in this file is either in the profile or listed in `COVERED_BY_PYTHON` in `tests/test_skill_docs.py`. This file is human documentation only.
