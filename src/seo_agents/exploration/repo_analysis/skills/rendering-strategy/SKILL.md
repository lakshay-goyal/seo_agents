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

Runtime profile: [`profiles/rendering-strategy.json`](../../profiles/rendering-strategy.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). This file is human documentation only.
