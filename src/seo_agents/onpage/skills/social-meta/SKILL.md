---
name: social-meta
description: Open Graph and X card tags from the planned title/description, the canonical URL and a correctly sized share image. Deterministic.
kind: deterministic
order: 10
---
## What the function does (`checks.check_social_meta`)
- No OG/Twitter tags at all -> ONE `insert_node` change with the whole tag block (no per-tag duplicates).
- Some tags exist -> one `set_attr`/`insert_node` change per tag whose value differs.
- `og:title` = planned title without the brand suffix; `og:url` = the route's canonical URL; `og:type` = article for
  posts, otherwise website; `twitter:card` = summary_large_image.
- `og:image`: the existing one, or a page image that is >= 1200 px wide with a ~1.91:1 aspect ratio. Otherwise
  `[VERIFY: 1200x630 share image]`, status `needs_input` and a client input (a 1920x1080 screenshot is not a share image).
- On a shared-shell SPA these head changes are `blocked` until `chg_prerender` (social crawlers do not run JavaScript).
