---
name: explore-sitemap-robots
description: Find sitemap and robots sources (static files, generators, templates) and indexing directives with values.
---

Sitemaps and robots rules exist as static files (`sitemap*.xml`, `.xml.gz`, `robots.txt`), as
code that generates them (`app/sitemap.ts`, `app/robots.ts`, `next-sitemap`, Nuxt/Gatsby/Astro plugins)
or as templates (Hugo `layouts/sitemap.xml`, Jekyll/11ty templates; reported as `generated: template`,
never as a parse error). `robots.txt` is parsed into user-agent groups; sitemap XML URLs, sitemap-index
children, `lastmod`, hreflang and image entries are counted.

Directives are split by source with their captured value: `meta-robots`, `x-robots-tag` and
`metadata-robots` (framework metadata API). `noindex` never matches `rel="nofollow"` or
`express.static({index: false})`.

Runtime profile: [`profiles/sitemap-robots.json`](../../profiles/sitemap-robots.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). Python post-processing: `agents/sitemap_robots.py`. Every backticked token in this file is either in the profile or listed in `COVERED_BY_PYTHON` in `tests/test_skill_docs.py`. This file is human documentation only.
