---
name: technical-onpage
description: Canonical, robots, lang, viewport, permanent host redirect, prerender/shared-shell, soft 404, trailing slash, mobile parity, slug. Deterministic.
kind: deterministic
order: 8
---
## Checks (`checks.check_technical`)
| Check | Pass condition | Output |
| --- | --- | --- |
| canonical | absolute, self-referencing for THIS route on the canonical host (host case-insensitive; trailing slash strict only when the site redirects one form, matching its 200 form) | change `technical.canonical`; skipped only on META-noindex pages (indexability reports the conflict; X-Robots-Tag noindex from preview hosts does not skip it); unknown or local origin (`origin_is_local`): path-only comparison, absent/relative/localhost canonical -> `[VERIFY: production origin]` + path, `needs_input` + client input for the production origin |
| robots | no `noindex`/`none` in meta robots (intentional-noindex URLs such as 404, thank-you, search, cart, checkout, account, login, admin are left alone) | change `technical.robots` keeping other directives (e.g. max-image-preview) and leading with `index, follow`; noindex only in X-Robots-Tag is handled by indexability |
| lang | `<html lang>` present | change `technical.lang` on the `<html>` element (value: Content-Language header, else the detected language); `needs_input` when only the head insertion point is known |
| viewport | width=device-width, no user-scalable=no/0, maximum-scale >= 5 (Lighthouse) | change `technical.viewport`: `width=device-width, initial-scale=1` + the other params kept (e.g. viewport-fit=cover), offending user-scalable / maximum-scale dropped |
| host redirect | no 302/303/307 in the page chain, permanent host redirect (apex <-> www; skipped on a local origin; http->https is owned by security `chg_http_to_https`) | site change `chg_redirect_permanent` (`platform_config`, where = hosting-specific: Vercel domains/vercel.json, netlify.toml, Cloudflare rules, nginx, .htaccess) |
| host redirect chain | one hop to the canonical origin (skipped on a local origin) | advice |
| served HTML | the route has its own head and content in the initial HTML | site change `chg_prerender` + advice; per-route head changes are `blocked` on it |
| soft 404 | unknown URLs return 404 | site change `chg_soft_404` |
| trailing slash | /x and /x/ are not both 200, handling is consistent, the redirect is 301/308 | advice |
| mobile parity | same headings/links on mobile | advice |
| slug | lowercase, hyphenated, ASCII, no spaces/underscores, path <= 75 chars, <= 4 folders, no long numeric ids (years 19xx/20xx are fine) | advice low |
