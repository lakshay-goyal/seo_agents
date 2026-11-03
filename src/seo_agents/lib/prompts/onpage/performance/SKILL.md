---
name: performance
description: Core Web Vitals and page speed from Lighthouse/CrUX (field data first), Lighthouse opportunities and failed SEO audits, compression, caching, server response time, font-display and JS-only rendering. Deterministic.
kind: deterministic
order: 19
---
## Checks (`checks.performance_check.check_performance`)
Mobile lab data is preferred over desktop; real-user field data (CrUX / PageSpeed Insights p75) wins over lab numbers and the source is stated in the evidence. PSI `CUMULATIVE_LAYOUT_SHIFT_SCORE` is CLS x100 and is divided by 100; CrUX and lab CLS are unitless.
Image lazy-loading / fetchpriority changes belong to `media_technical`; this check only gives advice and names the LCP element.

| Check | Pass condition | Output |
| --- | --- | --- |
| LCP | <= 2500 ms (poor > 4000) | advice (high when poor) |
| CLS | <= 0.1 (poor > 0.25) | advice (high when poor) |
| INP | <= 200 ms (poor > 500) | advice (high when poor) |
| TBT / TTFB / FCP | <= 200 / 800 / 1800 ms | advice (high when poor) |
| opportunities | no failing Lighthouse opportunity: render-blocking-resources, unused-javascript, unused-css-rules, unminified-javascript/css, uses-responsive-images, offscreen-images, modern-image-formats, uses-optimized-images, efficient-animated-content, prioritize-lcp-image, server-response-time, redirects, legacy/duplicated-javascript, uses-rel-preconnect | advice per audit with savings |
| Lighthouse SEO audits | none failed | advice low per audit |
| compression | no Lighthouse `uses-text-compression` opportunity (any form factor; the HTTP probe sends no Accept-Encoding, so its Content-Encoding is not used) | site change `platform.compression` (`chg_compression`); advice low on a local origin |
| caching | HTML has Cache-Control without no-store | advice low |
| server response | headers within 800 ms (probe timing; skipped when the page fetch was redirected) | advice |
| font-display | no `no-font-display` site gap (the only source) | advice low |
| JS dependency | title and links exist without JavaScript | advice high |
