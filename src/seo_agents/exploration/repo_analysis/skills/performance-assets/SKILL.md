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

```json profile
{
  "name": "performance-assets",
  "signals": [
    {"id": "image-optimizer", "label": "Framework image optimizer", "tags": ["LCP", "CLS"], "regex": "from\\s+['\"](?:next/image|next/legacy/image|astro:assets|@nuxt/image|gatsby-plugin-image|@sveltejs/enhanced-img|@unpic/[\\w-]+)['\"]|<(?:NuxtImg|NuxtPicture|GatsbyImage|StaticImage|enhanced:img)\\b|\\bngSrc\\s*="},
    {"id": "priority-image", "label": "Prioritised/preloaded hero image", "tags": ["LCP"], "regex": "\\bpriority\\b(?:\\s*=\\s*\\{?true)?|fetchpriority\\s*=\\s*['\"]high|rel=['\"]preload['\"][^>]*as=['\"]image|loading\\s*=\\s*['\"]eager"},
    {"id": "font-loader", "label": "Font loading", "tags": ["LCP", "CLS"], "regex": "from\\s+['\"](?:next/font/[\\w]+|@fontsource[\\w/-]*)['\"]|@font-face|fonts\\.googleapis\\.com|use\\.typekit\\.net"},
    {"id": "font-display", "label": "font-display set", "tags": ["CLS"], "regex": "font-display\\s*:|display\\s*[:=]\\s*['\"]swap|&display=swap"},
    {"id": "preconnect", "label": "Resource hints (preconnect/dns-prefetch/preload)", "tags": ["LCP", "TTFB"], "regex": "rel\\s*=\\s*['\"](?:preconnect|dns-prefetch|preload|modulepreload)['\"]"},
    {"id": "third-party-script", "label": "Third-party script", "tags": ["INP", "LCP"], "regex": "<script[^>]+src=['\"]https?://[^'\"]+['\"][^>]*>|googletagmanager\\.com|google-analytics\\.com|gtag\\s*\\(|connect\\.facebook\\.net|hotjar|clarity\\.ms|intercom|segment\\.com|cdn\\.jsdelivr|unpkg\\.com", "ignore_case": true},
    {"id": "script-strategy", "label": "Script loading strategy (async/defer/next/script/Partytown)", "tags": ["INP", "LCP"], "regex": "<script[^>]*\\b(?:async|defer)\\b|strategy\\s*=\\s*['\"](?:lazyOnload|afterInteractive|worker)|partytown|from\\s+['\"]next/script['\"]"},
    {"id": "code-splitting", "label": "Code splitting / dynamic import", "tags": ["INP", "LCP"], "regex": "\\bimport\\s*\\(\\s*['\"`]|React\\.lazy\\s*\\(|defineAsyncComponent|loadChildren\\s*:|next/dynamic"},
    {"id": "vitals-measurement", "label": "Web vitals measurement", "tags": ["LCP", "CLS", "INP", "TTFB"], "regex": "from\\s+['\"]web-vitals['\"]|reportWebVitals|useReportWebVitals|@vercel/speed-insights|SpeedInsights|onLCP\\s*\\(|onCLS\\s*\\(|onINP\\s*\\("},
    {"id": "caching-compression", "label": "Caching/compression config", "tags": ["TTFB"], "regex": "Cache-Control|max-age=|immutable|compression\\s*\\(|gzip\\s+on|brotli|compress\\s*:\\s*true|ExpiresByType", "ignore_case": true},
    {"id": "css-in-js-runtime", "label": "Runtime CSS-in-JS", "tags": ["INP", "LCP"], "regex": "from\\s+['\"](?:styled-components|@emotion/(?:react|styled))['\"]"},
    {"id": "animation-heavy", "label": "Scroll/animation library on main thread", "tags": ["INP", "CLS"], "regex": "from\\s+['\"](?:aos|gsap|framer-motion|animejs|lottie-web|locomotive-scroll)['\"]|AOS\\.init"}
  ],
  "gaps": [
    {"id": "no-vitals-measurement", "label": "No web vitals measurement found", "when_none_of": ["vitals-measurement"]},
    {"id": "no-font-display", "label": "Fonts loaded but no font-display/swap found", "when_none_of": ["font-display"], "only_if_any": ["font-loader"]}
  ],
  "edit_guidance": "LCP: preload/prioritise the hero image, serve modern formats, inline critical CSS. CLS: width/height or aspect-ratio on every image/embed, font-display: swap with size-adjusted fallbacks. INP: defer third-party scripts, split large bundles, remove heavy client libraries. TTFB: cache static output and enable compression in hosting config."
}
```
