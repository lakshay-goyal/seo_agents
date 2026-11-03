---
name: explore-seo-metadata
description: Extract title, description, canonical, robots, Open Graph, Twitter and hreflang declarations per page including inherited layouts.
---

## Output per page
- `declarations`: `[{file, line, source_id, title? | name?/property?/content? | rel?/href? | description?}]`
  — one record per declared value, values exactly as written (strings or null).
- `inheritance_note`: string explaining layout/template inheritance and title templates.

## Hard rules
1. Collect from the page template AND every layout in its composition (layouts first).
2. Recognise each stack's head API:
   `<title>/<meta>/<link>` in HTML/Liquid/PHP/Jinja/Blade/ERB/Handlebars; Next
   `export const metadata` / `generateMetadata`; Nuxt `useHead`/`useSeoMeta`/`definePageMeta`;
   SvelteKit `<svelte:head>`; Astro/Vue/React `<Head>`/`<Helmet>`; Angular `Title.setTitle`/`Meta.updateTag`;
   Remix `export const meta`; Gatsby `export const Head`; front matter `title`/`description`;
   WordPress `wp_head()`/SEO plugins and Shopify `{{ page_title }}` are runtime values.
3. Computed values (template literals, `{{ page_title }}`, `<?php wp_title(); ?>`) -> keep the
   declaration with the value null, `value_origin: "expression"`, `origin_ref` element id.
4. Title templates (`'%s | Acme'`, `{% if %}` suffixes) are described in `inheritance_note`.

## Example
`export const metadata = { title: 'Pricing', description: 'Plans for teams' }` at
`app/pricing/page.tsx:4` -> two records `{"title": "Pricing", "line": 4, ...}` and
`{"name": "description", "content": "Plans for teams", "line": 4, ...}`; layout
`app/layout.tsx:9` `title: {template: '%s | Acme'}` -> noted in `inheritance_note`.

## Done when
Each page lists its own and inherited declarations, computed ones are marked, `test_script` passes.
