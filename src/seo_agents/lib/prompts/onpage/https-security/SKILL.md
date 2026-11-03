---
name: https-security
description: HTTPS, permanent http->https redirect, HSTS, security headers, mixed content and insecure forms. Deterministic.
kind: deterministic
order: 18
---
## Checks (`checks.security_check.check_security`)
On a local or unknown origin the served probes describe a dev server: site changes become advice and header findings are skipped.
`where` text is hosting-specific (vercel.json headers, netlify.toml [[headers]] / _headers, Cloudflare _headers / rules, nginx add_header, .htaccess Header set; GitHub Pages needs a CDN in front).

| Check | Pass condition | Output |
| --- | --- | --- |
| https | origin is https | site change `platform.https` (`chg_https`, high) |
| http -> https | http:// redirects permanently (301/308) to https:// (only when the probe was measured and the home fetch succeeded; sole owner of scheme redirects) | site change `platform.http_to_https` (`chg_http_to_https`) |
| HSTS | Strict-Transport-Security present (only when headers were measured; the `no-hsts` site gap is a fallback when nothing was measured, never on a local origin) | site change `platform.hsts` (`chg_hsts`, `max-age=31536000; includeSubDomains`) |
| HSTS max-age | >= 15768000 (6 months) | advice low |
| security headers | X-Content-Type-Options, Referrer-Policy, Content-Security-Policy, X-Frame-Options or frame-ancestors (only when page headers or a non-empty home probe were measured) | one advice low (trust, not ranking) |
| mixed content | no http:// images or head resources on an https page | change `images.src` (`set_attr` to https) for same-host images with an edit, else advice high |
| forms | no form action over http:// | advice high |
