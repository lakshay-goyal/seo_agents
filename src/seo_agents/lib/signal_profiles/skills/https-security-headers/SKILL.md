---
name: explore-https-security-headers
description: Find HTTPS enforcement, HSTS and mixed-content URLs; other security headers are optional context.
---

For SEO only the HTTPS redirect, HSTS and mixed content matter. Headers set by `<meta http-equiv>`
are ignored (browsers ignore HSTS/XFO there). Hosts that add HSTS automatically (Vercel, Netlify,
Cloudflare Pages, Firebase, GitHub Pages) suppress the `no-hsts` gap; served headers measured by the site
model remain authoritative. CSP, X-Frame-Options, Referrer-Policy and Permissions-Policy are recorded as
optional signals, never gaps. Hard-coded `http://` URLs (except localhost, XML namespaces and
`schema.org` contexts) are mixed-content candidates.

Runtime profile: [`profiles/https-security-headers.json`](../../profiles/https-security-headers.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). Python post-processing: `agents/https_security_headers.py`. Every backticked token in this file is either in the profile or listed in `COVERED_BY_PYTHON` in `tests/test_skill_docs.py`. This file is human documentation only.
