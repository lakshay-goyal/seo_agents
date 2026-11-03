---
name: explore-structured-data
description: Find JSON-LD, microdata and schema helpers, and extract the top-level Schema.org types they declare (the single @type source).
---

Structured data appears as `<script type="application/ld+json">` (literal body, or
`dangerouslySetInnerHTML={{__html: JSON.stringify(obj)}}` / `set:html` with the object literal resolved in
the same file), helper components/composables (`next-seo` `*JsonLd`, `useSchemaOrg`, `define*()`,
`schema-dts`), Liquid `| json`, or microdata (`itemscope`/`itemtype`). Self-closing script tags never
swallow later markup. Only top-level types (including `@graph` members and `@type` arrays) count as
`schema_types`; nested ones are `nested_schema_types`. Brand, local-business and AI-readiness agents
reuse this output. Only syntax is checked; Rich Results eligibility is not verified.

Runtime profile: [`profiles/structured-data.json`](../../profiles/structured-data.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). Python post-processing: `agents/structured_data.py`. Every backticked token in this file is either in the profile or listed in `COVERED_BY_PYTHON` in `tests/test_skill_docs.py`. This file is human documentation only.
