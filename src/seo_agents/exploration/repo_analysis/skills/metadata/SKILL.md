---
name: explore-metadata
description: Find every place that sets title, description, canonical, robots, Open Graph and Twitter tags, in any framework.
---

Metadata lives in two shapes, and both are recorded:

- **Literal tags** in any template (`<title>`, `<meta name|property>`, `<link rel>`,
  `<html lang>`) — parsed directly with their values.
- **Metadata APIs** (signals below): framework exports and head helpers such as
  `export const metadata`, `generateMetadata`, `<Head>`, `<Helmet>`, `useHead`,
  `useSeoMeta`, `<svelte:head>`, Angular `Meta`/`Title`, Remix `meta`, Gatsby
  `Head`, Blade `@section('title')`, Jinja `{% block title %}`, `content_for :title`,
  front matter `title:`/`description:`. Whole objects/functions are captured so the
  real values and logic are visible.

A route without a page-level declaration inherits from layouts or the HTML shell.
Runtime assignments (`document.title =`) are invisible to non-rendering crawlers.

Runtime profile: [`profiles/metadata.json`](../../profiles/metadata.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). This file is human documentation only.
