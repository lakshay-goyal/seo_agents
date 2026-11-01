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

```json profile
{
  "name": "metadata",
  "signals": [
    {"id": "metadata-export", "label": "Exported metadata object", "regex": "export\\s+(?:const|let)\\s+metadata\\b", "block": true},
    {"id": "metadata-generator", "label": "Metadata generator function", "regex": "export\\s+(?:async\\s+)?function\\s+generateMetadata\\b", "block": true},
    {"id": "head-component", "label": "Head component (next/head, Helmet, Head API)", "regex": "<(?:Head|Helmet|HelmetProvider|NextSeo|DefaultSeo|SEO|Seo|BaseHead|MetaTags)\\b", "block": true},
    {"id": "head-export", "label": "Exported Head/meta/links function", "regex": "export\\s+(?:const|function|let)\\s+(?:Head|meta|links)\\b", "block": true},
    {"id": "head-composable", "label": "Head composable (useHead/useSeoMeta/definePageMeta)", "regex": "\\b(?:useHead|useSeoMeta|useServerSeoMeta|definePageMeta|useMeta)\\s*\\(", "block": true},
    {"id": "head-option", "label": "head()/metaInfo option or app.head config", "regex": "\\b(?:head|metaInfo)\\s*(?:\\(\\s*\\)\\s*\\{|:\\s*\\{)", "block": true},
    {"id": "svelte-head", "label": "<svelte:head> block", "regex": "<svelte:head>"},
    {"id": "meta-service", "label": "Title/Meta service call", "regex": "\\.(?:setTitle|addTag|addTags|updateTag)\\s*\\(", "block": true},
    {"id": "template-block", "label": "Server template head block/section", "regex": "\\{%-?\\s*block\\s+(?:title|meta|head|description|extra_head)\\b|@(?:section|yield)\\s*\\(\\s*['\"](?:title|description|meta)['\"]|content_for\\s*\\(?\\s*:(?:title|description|head)|\\b(?:set|display)_meta_tags\\b|\\{%\\s*seo\\b"},
    {"id": "templated-title", "label": "Title built from template variables", "regex": "<title>[^<]*(?:\\{\\{|\\{%|<%|\\{)"},
    {"id": "runtime-title", "label": "Runtime document.title / meta mutation", "regex": "document\\.title\\s*=|document\\.querySelector\\([^)]*meta"},
    {"id": "site-config", "label": "Site-wide metadata config (siteMetadata, site, metadataBase, baseURL)", "regex": "\\b(?:siteMetadata|metadataBase|siteUrl|baseURL|site)\\s*[:=]\\s*", "glob": "*config*"},
    {"id": "title-template", "label": "Title template", "regex": "\\b(?:titleTemplate|template)\\s*:\\s*['\"`][^'\"`]*%s", "block": false}
  ],
  "gaps": [
    {"id": "no-description", "label": "No meta description declaration or API found", "when_none_of": ["metadata-export", "metadata-generator", "head-component", "head-export", "head-composable", "head-option", "svelte-head", "meta-service", "template-block", "@literal:description"]}
  ],
  "edit_guidance": "Change page-specific metadata where the page's own declaration lives (metadata export, head component, composable or template block in the route file); change defaults in the layout or HTML shell. Keep one title/description per URL and use the site config (metadataBase/siteUrl) for absolute canonical/og:url values."
}
```
