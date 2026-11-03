---
name: indexability
description: Robots meta / X-Robots-Tag directives, canonical edge cases and conflicts, robots.txt blocking, page redirect loops and chains. Deterministic.
kind: deterministic
order: 16
---
## Checks (`checks.indexability_check.check_indexability`)
Directives are tokenised for Googlebot (`robots` + `googlebot` meta, X-Robots-Tag including UA-prefixed parts such as `googlebot: noindex`).
URLs that are noindexed on purpose (404, thank-you, search, cart, checkout, account, login, admin, staging) only get the data flag `intentional_noindex`.
The plain self-canonical and meta-noindex fixes stay in `technical` (`technical.canonical`, `technical.robots`).

| Check | Pass condition | Output |
| --- | --- | --- |
| none | no `none` (= noindex, nofollow) | advice high |
| page nofollow | no page-level `nofollow` | advice high |
| snippets | no `nosnippet`, `max-snippet` is -1 or a positive number | advice |
| image preview | `max-image-preview:large` (not none/standard), no `noimageindex` | advice low |
| unavailable_after | date not in the past | advice high |
| meta vs header | meta robots and X-Robots-Tag agree | advice |
| header noindex | X-Robots-Tag has no noindex for an indexable page (also when the meta says noindex too: technical fixes the meta, this removes the header) | site change `platform.x_robots_tag` (`chg_x_robots_tag`); advice on preview/local hosts |
| googlebot meta | `<meta name="googlebot">` has no noindex/none (`googlebot-news` ignored) | change `indexability.googlebot_meta` (`set_attr` content) when the tag has an edit, else advice high |
| noindex + canonical | a noindex page does not canonicalise elsewhere | advice |
| duplicate canonical | exactly one rel=canonical | advice high listing the canonical hrefs |
| canonical form | absolute, https, same host (leading `www.` ignored), no query/fragment | advice (cross-domain / http: high) only when `technical.canonical` cannot fix it: origin unknown/local, or the page is meta-noindex |
| canonical target | target is a 200, indexable, non-redirecting model page (`site_pages`) | advice high |
| canonical agreement | og:url and HTTP `Link: rel=canonical` match the canonical | advice |
| robots.txt | the URL is allowed for Googlebot (its group overrides `*`; longest match, `*`/`$` wildcards) | advice high (blocked while indexable) |
| redirects | no loop/limit, a Location on redirects, at most one hop | advice (loop/no location: high); `final_url` in data |
