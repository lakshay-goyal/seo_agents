---
name: head-hygiene
description: Current title and meta description hygiene (length in characters and SERP pixels, placeholders, duplicates, brand suffix, keyword position) plus meta keywords, meta refresh and charset. Deterministic.
kind: deterministic
order: 12
---
## Checks (`checks.head_hygiene_check.check_head_hygiene`)
The planned title/description are validated by page-plan; this check reports on what the page serves TODAY. It never
changes `head.title` / `head.description` (page-plan owns them) - findings are advice with evidence.
On a `shared-shell` head (every route serves the same `<head>`) the placeholder / duplicate title and duplicate
description findings are reported only on the home page, with a note that the root fix is prerendering / per-route
heads (the `platform.prerender` change from technical-onpage); other routes do not repeat them.

| Check | Pass condition | Output |
| --- | --- | --- |
| title missing | a non-empty `<title>` | advice `head.title_missing` (high) |
| title length | 30-60 characters and <= ~580px (Arial 20px estimate) | advice `head.title_length` |
| placeholder title | not a framework default (React App, Vite + React, Home, Untitled, Document, Next.js, ...) | advice `head.title_placeholder` (high; shared shell: home page only) |
| brand only | title is more than the brand name | advice `head.title_brand_only` (high) |
| brand suffix | brand appears at most once; a missing suffix is a site-wide convention reported once, on the home page | advice `head.title_brand` (low) |
| title vs description | description does not repeat the title | advice `head.title_equals_description` |
| unique title | no exact / near (token Jaccard >= 0.8, brand ignored) match among other pages | advice `head.title_duplicate` (shared shell: home page only) |
| keyword position | primary keyword starts within the first 30 characters | advice `head.title_keyword_position` (low) |
| description | present, 120-160 characters, <= ~920px (Arial 14px) | advice `head.description_missing` / `head.description_length` |
| unique description | no exact / near match among other pages | advice `head.description_duplicate` (shared shell: home page only) |
| meta keywords | no `<meta name="keywords">` | change `head.meta_keywords` (`remove`) |
| meta refresh | no `<meta http-equiv="refresh">` | advice `head.meta_refresh` (high) |
| charset | charset in Content-Type or `<meta charset>` (only judged when raw meta tags were captured) | advice `head.charset` (low) |
