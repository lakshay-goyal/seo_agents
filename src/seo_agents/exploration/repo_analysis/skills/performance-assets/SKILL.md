---
name: explore-performance-web-vitals
description: Find code-level causes of poor Core Web Vitals (LCP, CLS, INP, TTFB) and existing measurement in any framework.
---

Each signal carries `tags` naming the vital it influences. Images: missing
dimensions (CLS), missing lazy loading below the fold, no priority/preload for
the hero image (LCP), legacy formats and oversized local files. Fonts: missing
`font-display`, external font CSS without preconnect. Scripts: render-blocking
third-party scripts, analytics/tag managers, heavy dependencies (INP). Caching and
compression config (TTFB). Existing optimisers (`next/image`, `NuxtImg`,
`astro:assets`, `NgOptimizedImage`, `next/font`, `next/script`, Partytown) and
vitals measurement (`web-vitals`, `reportWebVitals`, Speed Insights) are recorded
so changes build on them.

Local asset sizes and dimensions come from the repository snapshot.

Runtime profile: [`profiles/performance-assets.json`](../../profiles/performance-assets.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). This file is human documentation only.
