---
name: explore-local-business-presence
description: Find NAP (name, address, phone), LocalBusiness schema, maps embeds, opening hours and location pages.
---

Gated: without a LocalBusiness-family schema, a postal address or geo coordinates the agent
reports `status: not-applicable` (no phone-number or map-library noise on non-local sites). Otherwise
it records phone values (including JSON-LD `telephone`), emails (including JSON-LD `email`), opening
hours, map embeds and contact/location pages (page files only, never Redux/Pinia store directories).

Runtime profile: [`profiles/local-business-presence.json`](../../profiles/local-business-presence.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). Python post-processing: `agents/local_business_presence.py`. Every backticked token in this file is either in the profile or listed in `COVERED_BY_PYTHON` in `tests/test_skill_docs.py`. This file is human documentation only.
