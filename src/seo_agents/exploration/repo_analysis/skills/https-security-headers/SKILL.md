---
name: explore-https-security-headers
description: Find HTTPS enforcement, security headers and mixed-content URLs in code and hosting config.
---

Headers are configured in framework config (`headers()`, `routeRules.headers`),
hosting files (`vercel.json`, `netlify.toml`, `_headers`, `.htaccess`, nginx),
middleware (`helmet()`, `SecurityMiddleware`, `secure_headers`) or settings
(`SECURE_HSTS_SECONDS`, `force_ssl`). Each header name below is checked
everywhere; the result shows which are declared and where. Hard-coded `http://`
URLs (except localhost and XML namespaces) are mixed-content candidates.

Runtime profile: [`profiles/https-security-headers.json`](../../profiles/https-security-headers.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). This file is human documentation only.
