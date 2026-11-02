---
name: explore-sitemap-robots
description: Find sitemap and robots sources (static files, generators, plugins) and per-page indexing directives.
---

Sitemaps and robots rules exist either as static files (`sitemap*.xml`,
`robots.txt`) or as code that generates them (route handlers, plugins, config).
Static `robots.txt` is parsed into user-agent groups; sitemap XML URLs are listed.
Per-page `noindex`/`nofollow` (meta robots, `X-Robots-Tag`, metadata `robots`
fields) can hide pages even when the sitemap lists them.

Runtime profile: [`profiles/sitemap-robots.json`](../../profiles/sitemap-robots.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). This file is human documentation only.
