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

```json profile
{
  "name": "structured-data",
  "signals": [
    {"id": "json-ld-script", "label": "JSON-LD script tag", "regex": "application/ld\\+json", "block": false},
    {"id": "json-ld-helper", "label": "JSON-LD helper/component/composable", "regex": "<\\w*JsonLd\\b|useSchemaOrg\\s*\\(|defineOrganization|definePerson|defineWebSite|from\\s+['\"]schema-dts['\"]|WithContext<|['\"]script:ld\\+json['\"]|_internal/schema\\.html", "block": true},
    {"id": "schema-type", "label": "Schema.org @type value", "regex": "['\"]@type['\"]\\s*:\\s*['\"]([\\w]+)['\"]", "value_group": 1},
    {"id": "schema-context", "label": "Schema.org @context", "regex": "['\"]@context['\"]\\s*:\\s*['\"]https?://schema\\.org", "ignore_case": true},
    {"id": "microdata", "label": "Microdata", "regex": "itemscope|itemtype\\s*=\\s*['\"]https?://schema\\.org/(\\w+)", "value_group": 1}
  ],
  "gaps": [
    {"id": "no-structured-data", "label": "No structured data found", "when_none_of": ["json-ld-script", "json-ld-helper", "schema-type", "microdata"]}
  ],
  "edit_guidance": "Emit JSON-LD from the page/layout that owns the entity: WebSite + Organization/Person in the root layout, Article/Product/FAQPage/BreadcrumbList in the matching page templates, built from the same data the page renders."
}
```
