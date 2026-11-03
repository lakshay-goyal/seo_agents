---
name: technical-onpage
description: Canonical, robots, lang, viewport, permanent host redirect, prerender/shared-shell, soft 404, trailing slash, mobile parity, slug. Deterministic.
kind: deterministic
order: 8
---
## Checks (`checks.check_technical`)
| Check | Pass condition | Output |
| --- | --- | --- |
| canonical | absolute, self-referencing for THIS route on the canonical host | change `technical.canonical` |
| robots | no `noindex` in meta robots or X-Robots-Tag | change `technical.robots` |
| lang | `<html lang>` present | change `technical.lang` |
| viewport | contains width=device-width | change `technical.viewport` |
| host redirect | no 302/303/307 in the redirect chain | site change `chg_redirect_permanent` (`platform_config`, where = hosting-specific: Vercel domains/vercel.json, netlify.toml, Cloudflare rules, nginx, .htaccess) |
| served HTML | the route has its own head and content in the initial HTML | site change `chg_prerender` + advice; per-route head changes are `blocked` on it |
| soft 404 | unknown URLs return 404 | site change `chg_soft_404` |
| trailing slash | /x and /x/ are not both 200 | advice |
| mobile parity | same headings/links on mobile | advice |
| slug | lowercase, hyphenated, short | advice |
