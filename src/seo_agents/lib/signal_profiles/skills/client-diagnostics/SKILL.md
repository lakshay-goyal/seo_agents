---
name: explore-client-diagnostics
description: Find the client-rendering hazards that hide content, links or metadata from crawlers.
---

Only SEO-relevant hazards are kept: data fetched in effects/mounted hooks, `document.title`
writes (runtime head), hash-based routing (`#/` URLs are not separate pages), navigation in click
handlers, and anchors without a crawlable `href` (missing, `href="#"` or `javascript:`). The rendering
agent folds these per file into `client_hazards_by_file`.

Runtime profile: [`profiles/client-diagnostics.json`](../../profiles/client-diagnostics.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). Python post-processing: `agents/client_diagnostics.py`. Every backticked token in this file is either in the profile or listed in `COVERED_BY_PYTHON` in `tests/test_skill_docs.py`. This file is human documentation only.
