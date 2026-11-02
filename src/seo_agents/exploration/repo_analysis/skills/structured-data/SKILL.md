---
name: explore-structured-data
description: Find JSON-LD, microdata and schema helpers, and extract the Schema.org types they declare.
---

Structured data appears as `<script type="application/ld+json">` (literal or with
a serialised object), JSON-LD helper components/composables (`next-seo`
`*JsonLd`, `useSchemaOrg`, `schema-dts` types, Hugo/Jekyll schema partials) or
microdata (`itemscope`/`itemtype`). Every `"@type"` value is collected with its
location so an agent can see which entities already exist (Organization,
Person, WebSite, Article, Product, FAQPage, BreadcrumbList, LocalBusiness, ...).
Only syntax is checked here; Rich Results eligibility is not verified.

Runtime profile: [`profiles/structured-data.json`](../../profiles/structured-data.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). This file is human documentation only.
