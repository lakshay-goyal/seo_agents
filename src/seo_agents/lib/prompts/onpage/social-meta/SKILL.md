---
name: social-meta
description: Open Graph and X card tags from the planned title/description, the canonical URL and a correctly sized share image. Deterministic.
kind: deterministic
order: 10
---
## What the function does (`checks.check_social_meta`)
- No OG/Twitter tags at all -> ONE `insert_node` change `head.social` with the whole tag block (no per-tag duplicates).
- Some tags exist -> one `set_attr`/`insert_node` change `head.og.<prop>` / `head.twitter.<prop>` per tag whose value differs.
- On a shared-shell SPA these head changes are `blocked` until `chg_prerender` (social crawlers do not run JavaScript).

| Tag | Expected value | Output |
| --- | --- | --- |
| `og:title` | planned title without the brand suffix | change when different |
| `og:description` | planned meta description | change when different |
| `og:type` | `article` for posts, otherwise `website` | change when different |
| `og:url` | the route's canonical (absolute) URL | change when different |
| `og:image` | the existing one made absolute (a relative/protocol-relative og:image is rewritten against the origin - social crawlers do not resolve relative paths), else a page image >= 1200 px wide at ~1.91:1 | change when different; no usable image -> `[VERIFY: 1200x630 share image]`, status `needs_input` + client input (a 1920x1080 screenshot is not a share image) |
| `og:site_name` | the brand name | change when different |
| `og:locale` | from the page lang with a region (`en-US` -> `en_US`); a bare language (`en`) proposes nothing | change when different |
| `twitter:card` | `summary_large_image` | change when different |
