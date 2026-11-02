---
name: explore-canonicals-redirects
description: Find canonical declarations, redirect/rewrite rules, trailing-slash policy and 404 handling in code and hosting config.
---

Canonicals: `<link rel="canonical">`, `alternates.canonical`, `canonical` props.
Redirects live in framework config (`redirects()`, `routeRules`, `redirects:`),
hosting config (`vercel.json`, `netlify.toml`, `_redirects`, `.htaccess`, nginx
`return 301`/`rewrite`), middleware and handler code (`redirect(`, `res.redirect`,
`permanentRedirect`). Trailing-slash settings decide which URL variant is primary.
A custom 404 page/handler matters for soft-404s.

Runtime profile: [`profiles/canonicals-redirects.json`](../../profiles/canonicals-redirects.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). This file is human documentation only.
