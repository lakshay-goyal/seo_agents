---
name: explore-rendering-strategy
description: Classify how each route reaches the browser (static, SSG, SSR, ISR, CSR, hybrid) through a config -> layout -> page cascade.
---

Crawlers index what is in the HTML response first. Each signal carries a `mode`:
`static`, `SSG`, `ISR`, `SSR`, `CSR`, `CSR-component` and `hydration`. Framework config sets the
default (Next `output: 'export'`, Nuxt `ssr: false`, SvelteKit adapter-static, Astro `output`, Gatsby,
Angular without SSR, an SPA shell such as `<div id="root"></div>` or `<app-root>`), layouts override
config and page files override layouts. `route_modes` gives each page route `{mode, source_chain}`;
`csr_only` and `config_mode` summarise the project.

Runtime profile: [`profiles/rendering-strategy.json`](../../profiles/rendering-strategy.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). Python post-processing: `agents/rendering_strategy.py`. Every backticked token in this file is either in the profile or listed in `COVERED_BY_PYTHON` in `tests/test_skill_docs.py`. This file is human documentation only.
