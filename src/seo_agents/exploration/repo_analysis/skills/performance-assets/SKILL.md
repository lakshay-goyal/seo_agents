---
name: explore-performance-web-vitals
description: Find code-level causes of poor Core Web Vitals (LCP, CLS, INP, TTFB) and existing measurement in any framework.
---

Each signal carries `tags` naming the vital it influences. Recorded: image optimisers (`next/image`,
`NuxtImg`, `astro:assets`, `ngSrc`), priority/eager hero images, font loaders and `font-display`,
connection hints (`preconnect`, `dns-prefetch`) separately from `preload`/`modulepreload`, third-party
scripts and loading strategy, code splitting (comments and JSDoc `import('x')` ignored), caching and
compression config, CSS-in-JS runtimes, heavy client libraries (animation, WebGL/shaders, maps) and
vitals measurement (`web-vitals`, `reportWebVitals`, Speed Insights).

Python adds `large_assets`, `legacy_format_assets` (only PNG/JPEG/GIF files that source references and
that bypass an image pipeline) and `heavy_client_dependencies` (WebGL libraries tagged). Per-image
dimensions and lazy-loading come from the page inventory and the rendered site model, not from this profile.

Runtime profile: [`profiles/performance-assets.json`](../../profiles/performance-assets.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). Python post-processing: `agents/performance_assets.py`. Every backticked token in this file is either in the profile or listed in `COVERED_BY_PYTHON` in `tests/test_skill_docs.py`. This file is human documentation only.
