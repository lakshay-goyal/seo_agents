---
name: explore-client-diagnostics
description: Find client-side rendering hazards that hide content or metadata from crawlers or hurt hydration, in any JS framework.
---

Look for code that only produces SEO-relevant output after JavaScript runs or
that differs between server and client: data fetched in effects/mounted hooks,
`document.title` writes, raw HTML injection, browser-only globals during render,
non-deterministic values in render (hydration mismatch), hash-based routing
(`#/` URLs are not separate pages), and navigation without real `<a href>`.

```json profile
{
  "name": "client-diagnostics",
  "signals": [
    {"id": "effect-fetch", "label": "Data fetched after mount (effect/mounted hook)", "regex": "(?:\\b(?:useEffect|useLayoutEffect|onMounted|onMount)\\s*\\(|\\b(?:mounted|ngOnInit|componentDidMount)\\s*\\(\\s*\\)\\s*\\{)[\\s\\S]{0,300}?\\b(?:fetch|axios|\\$http|httpClient|\\.get)\\s*[(<]", "block": true},
    {"id": "runtime-head", "label": "Runtime head mutation", "regex": "document\\.title\\s*=|document\\.head\\.appendChild|setAttribute\\s*\\(\\s*['\"]content['\"]"},
    {"id": "raw-html", "label": "Raw HTML injection", "regex": "dangerouslySetInnerHTML|v-html=|\\{@html\\b|\\[innerHTML\\]|\\.innerHTML\\s*="},
    {"id": "browser-global", "label": "Browser-only global access", "regex": "typeof\\s+window\\s*[!=]==?|\\bwindow\\.(?:location|innerWidth|localStorage)|\\blocalStorage\\.getItem"},
    {"id": "nondeterministic-render", "label": "Non-deterministic value (hydration mismatch risk)", "regex": "Math\\.random\\s*\\(|new\\s+Date\\s*\\(\\s*\\)\\.(?:toLocale|get)|Date\\.now\\s*\\("},
    {"id": "hash-router", "label": "Hash-based routing", "regex": "createHashRouter|HashRouter|createWebHashHistory|useHash\\s*:\\s*true|withHashLocation|mode\\s*:\\s*['\"]hash['\"]"},
    {"id": "js-navigation", "label": "Navigation via JS click handler instead of link", "regex": "onClick\\s*=\\s*\\{[^}]*(?:navigate|router\\.push|history\\.push|location\\.href)|@click\\s*=\\s*['\"][^'\"]*(?:router\\.push|\\$router)|on:click=\\{[^}]*goto"},
    {"id": "lazy-content", "label": "Content behind lazy/conditional client rendering", "regex": "React\\.lazy\\s*\\(|\\blazy\\s*\\(\\s*\\(\\)\\s*=>|defineAsyncComponent\\s*\\(|next/dynamic"}
  ],
  "gaps": [],
  "edit_guidance": "Move content data loading to the server/build step (loader, getStaticProps, server component, load function), replace JS-only navigation with real links, use history-mode routing, set head tags through the framework head API rather than document.title, and render random/date values only after hydration."
}
```
