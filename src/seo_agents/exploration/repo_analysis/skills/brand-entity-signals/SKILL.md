---
name: explore-brand-entity-signals
description: Find brand/entity signals - Organization/Person schema, sameAs profiles, social links, authorship, about pages, logo.
---

Search engines and AI systems connect a site to an entity through a consistent name, logo,
`sameAs` profile URLs, Organization/Person structured data (types from the structured-data agent),
author bylines and about/team pages (route/page files only, never components or data). Social profile
URLs exclude share/intent links and repository file links; `sameAs` arrays are parsed in full and
`profiles_missing_from_same_as` reconciles the two.

Runtime profile: [`profiles/brand-entity-signals.json`](../../profiles/brand-entity-signals.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). Python post-processing: `agents/brand_entity_signals.py`. Every backticked token in this file is either in the profile or listed in `COVERED_BY_PYTHON` in `tests/test_skill_docs.py`. This file is human documentation only.
