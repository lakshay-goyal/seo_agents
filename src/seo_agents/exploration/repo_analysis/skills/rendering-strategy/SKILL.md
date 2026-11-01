---
name: explore-rendering-strategy
description: Classify how pages reach the browser (static, SSG, SSR, ISR, CSR, hybrid) from code signals in any framework.
---

Crawlers index what is in the HTML response first. Each signal carries a `mode`:
`static` (literal HTML), `SSG` (built at deploy), `ISR` (revalidated static),
`SSR` (rendered per request), `CSR` (empty shell, rendered by JS),
`CSR-component` (an island/component rendered only on the client) and
`hydration`. The project mode is inferred from the strongest signals; per-file
signals show which pages differ. An SPA shell (`<div id="root"></div>` with a
module script) means content is not in the initial HTML.

```json profile
{
  "name": "rendering-strategy",
  "signals": [
    {"id": "csr-mount", "label": "Client-side root mount", "mode": "CSR", "regex": "\\bcreateRoot\\s*\\(|ReactDOM\\.render\\s*\\(|createApp\\s*\\([^)]*\\)[\\s\\S]{0,200}?\\.mount\\s*\\(|bootstrapApplication\\s*\\(|bootstrapModule\\s*\\(|new\\s+App\\s*\\(\\s*\\{\\s*target|\\bmount\\s*\\(\\s*App|\\brender\\s*\\(\\s*\\(\\)\\s*=>"},
    {"id": "spa-shell", "label": "Empty SPA root element in HTML shell", "mode": "CSR", "regex": "<div\\s+id=['\"](?:root|app|__next|svelte)['\"]\\s*>\\s*</div>", "glob": "*.html"},
    {"id": "hydration", "label": "Hydration of server HTML", "mode": "hydration", "regex": "\\bhydrateRoot\\s*\\(|ReactDOM\\.hydrate\\s*\\(|createSSRApp\\s*\\(|provideClientHydration"},
    {"id": "server-render-api", "label": "Server rendering API / per-request data", "mode": "SSR", "regex": "renderToString\\s*\\(|renderToPipeableStream\\s*\\(|getServerSideProps\\b|getServerData\\b|export\\s+(?:async\\s+)?(?:function|const)\\s+loader\\b|force-dynamic|output\\s*:\\s*['\"]server['\"]|@angular/ssr|provideServerRendering|\\+page\\.server|export\\s+const\\s+csr\\s*=\\s*false", "block": false},
    {"id": "server-template", "label": "Server-rendered template response", "mode": "SSR", "regex": "res\\.render\\s*\\(|render_template\\s*\\(|TemplateResponse\\s*\\(|\\brender\\s*\\(\\s*request|return\\s+view\\s*\\(|class\\s+\\w+Controller\\s*<"},
    {"id": "static-generation", "label": "Static generation at build", "mode": "SSG", "regex": "getStaticProps\\b|getStaticPaths\\b|generateStaticParams\\b|force-static|output\\s*:\\s*['\"](?:export|static)['\"]|export\\s+const\\s+prerender\\s*=\\s*(?:true|['\"]auto['\"])|adapter-static|vite-ssg|ViteSSG|react-snap|\\bprerender\\s*:\\s*true|createPages\\b|RenderMode\\.Prerender"},
    {"id": "isr", "label": "Incremental/stale-while-revalidate regeneration", "mode": "ISR", "regex": "export\\s+const\\s+revalidate\\s*=|\\brevalidate\\s*:\\s*\\d+|\\b(?:isr|swr)\\s*:\\s*(?:true|\\d)|defer\\s*:\\s*true"},
    {"id": "hybrid", "label": "Per-route rendering rules", "mode": "hybrid", "regex": "\\brouteRules\\s*:|output\\s*:\\s*['\"]hybrid['\"]"},
    {"id": "server-default", "label": "Route file rendered on the server/at build by framework default (App Router, SvelteKit, Astro, Nuxt, Remix, Gatsby pages)", "mode": "server-default", "regex": "(?:^|/)app/(?:.*/)?(?:page|layout)\\.(?:tsx|jsx|ts|js|mdx)$|^src/routes/(?:.*/)?\\+(?:page|layout)\\.svelte$|^src/pages/.+\\.(?:astro|md|mdx)$|^(?:app/)?pages/.+\\.vue$|^app/routes/.+\\.(?:tsx|jsx)$", "files_only": true},
    {"id": "client-only", "label": "Client-only component/island", "mode": "CSR-component", "regex": "^\\s*['\"]use client['\"]|ssr\\s*:\\s*false|<ClientOnly\\b|client:(?:load|idle|visible|media|only)|export\\s+const\\s+ssr\\s*=\\s*false|clientLoader\\b"}
  ],
  "gaps": [],
  "edit_guidance": "Content that must rank should be in the initial HTML: prefer SSG/ISR for marketing/content pages, SSR for per-request data, and keep client-only components for interactivity below the fold. For pure SPAs add prerendering or migrate the affected routes to an SSR/SSG layer."
}
```
