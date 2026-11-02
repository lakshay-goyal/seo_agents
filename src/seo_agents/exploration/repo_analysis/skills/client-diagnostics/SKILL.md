---
name: explore-client-diagnostics
description: Find client-side rendering hazards that hide content or metadata from crawlers or hurt hydration, in any JS framework.
---

Look for code that only produces SEO-relevant output after JavaScript runs or
that differs between server and client: data fetched in effects/mounted hooks,
`document.title` writes, raw HTML injection, browser-only globals during render,
non-deterministic values in render (hydration mismatch), hash-based routing
(`#/` URLs are not separate pages), and navigation without real `<a href>`.

Runtime profile: [`profiles/client-diagnostics.json`](../../profiles/client-diagnostics.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). This file is human documentation only.
