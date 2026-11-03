---
name: explore-seo-media
description: Extract images, video, audio and iframes per page with alt, dimensions, loading and local asset size.
---

## Output per page
`items`: `[{file, line, source_id, kind, src, size_review, alt?, width?, height?, loading?,
fetchpriority?, srcset?, sizes?, asset_file, size_bytes, intrinsic_width, intrinsic_height}]`.
`kind`: img | picture-source | video | audio | iframe | background-image | svg-inline.
`size_review`: over_200_kib_review | within_200_kib_budget | unknown | not-an-image-budget.

## Hard rules
1. Elements: `img`, `Image` (next/image, astro:assets, nuxt-img), `source`, `video`, `audio`,
   `iframe`, Liquid `image_tag`/`image_url`, WordPress `the_post_thumbnail`/`wp_get_attachment_image`.
2. Include `alt` only when the attribute exists; `alt: ""` is meaningful (decorative).
   An expression alt (`alt={title}`) -> omit `alt`, set `value_origin: "expression"`.
3. Resolve `src` to a local asset: absolute `/x.png` -> `public/x.png` or `static/x.png`;
   imported modules (`import hero from './hero.png'`) via `index["refs"]`. Copy
   `size_bytes`/`width`/`height` from `assets` into `size_bytes`/`intrinsic_width/height`, else null.
4. Unresolved `src` (CDN, CMS, expression) -> `src: null`, `size_review: "unknown"`, limitation.

## Example
`<img src="/hero.png" alt="" width="1200" height="800">` at `index.html:12:4`, asset
`public/hero.png` 412331 B -> `{"kind": "img", "src": "/hero.png", "alt": "", "width": "1200",
"height": "800", "asset_file": "public/hero.png", "size_bytes": 412331,
"size_review": "over_200_kib_review", ...}`.

## Done when
Every media element in each page's composition is listed once; sizes come from `assets` only; `test_script` passes.
