---
name: explore-seo-internal-links
description: Extract every anchor per page with href, anchor text, accessible name, rel and target.
---

## Output per page
`links`: `[{file, line, source_id, href, anchor_text, accessible_name, target_url, kind, rel, target}]`.
`kind`: internal | action (mailto/tel/javascript) | resource (pdf/images/files) | absolute-unclassified.
`target_url` = `resolve_url(page_url, href)` for relative hrefs; absolute hrefs stay as-is.
`rel`/`target` may be null; `href`/`anchor_text`/`target_url`/`kind` are strings.

## Hard rules
1. Links are elements with tag `a`, or link components (`Link`, `NuxtLink`, `router-link`,
   `RouterLink`, `NavLink`) using `href` or `to`.
2. Literal href -> use it. Expression href (`{item.href}`, `{{ product.url }}`,
   `<?php the_permalink(); ?>`) -> `href: ""`, `value_origin: "expression"` and the element
   id as `origin_ref`; resolve from data (`index["data"]` record id) when the mapped array is local.
3. `anchor_text` is the literal text; image-only links use the image alt as `accessible_name`;
   `aria-label` wins for `accessible_name`.
4. Include header/nav/footer links from layouts on every page they wrap.

## Example
`<Link href="/contact">Talk to sales</Link>` at `app/pricing/page.tsx:40:6` ->
`{"href": "/contact", "anchor_text": "Talk to sales", "accessible_name": "Talk to sales",
"target_url": "/contact", "kind": "internal", "rel": null, "target": null,
"file": "app/pricing/page.tsx", "line": 40, "source_id": "app/pricing/page.tsx:40:6:Link"}`.

## Done when
All composed files' links are present once per page, kinds are from the enum, `test_script` passes.
