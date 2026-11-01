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

```json profile
{
  "name": "https-security-headers",
  "signals": [
    {"id": "headers-config", "label": "Header configuration block", "regex": "\\b(?:async\\s+)?headers\\s*(?:\\(\\s*\\)|:)|\"headers\"\\s*:|\\[\\[headers\\]\\]|^\\s*Header\\s+(?:always\\s+)?set\\b|add_header\\s|helmet\\s*\\(|SecurityMiddleware|secure_headers|setHeaders\\s*\\(", "block": true},
    {"id": "hsts", "label": "Strict-Transport-Security", "regex": "Strict-Transport-Security|SECURE_HSTS_SECONDS|hsts\\s*[:(]|force_ssl", "ignore_case": true},
    {"id": "https-redirect", "label": "HTTP→HTTPS enforcement", "regex": "SECURE_SSL_REDIRECT|force_ssl|RewriteCond\\s+%\\{HTTPS\\}|return\\s+301\\s+https://|x-forwarded-proto|requireHTTPS|forceHttps", "ignore_case": true},
    {"id": "csp", "label": "Content-Security-Policy", "regex": "Content-Security-Policy|contentSecurityPolicy|content_security_policy|CSP_DEFAULT_SRC", "ignore_case": true},
    {"id": "x-frame-options", "label": "X-Frame-Options / frame-ancestors", "regex": "X-Frame-Options|frame-ancestors|frameguard|X_FRAME_OPTIONS", "ignore_case": true},
    {"id": "x-content-type-options", "label": "X-Content-Type-Options", "regex": "X-Content-Type-Options|noSniff|SECURE_CONTENT_TYPE_NOSNIFF", "ignore_case": true},
    {"id": "referrer-policy", "label": "Referrer-Policy", "regex": "Referrer-Policy|referrerPolicy|SECURE_REFERRER_POLICY", "ignore_case": true},
    {"id": "permissions-policy", "label": "Permissions-Policy", "regex": "Permissions-Policy|permissionsPolicy|Feature-Policy", "ignore_case": true},
    {"id": "insecure-url", "label": "Hard-coded http:// URL (mixed-content candidate)", "regex": "['\"(=]http://(?!localhost|127\\.0\\.0\\.1|0\\.0\\.0\\.0|www\\.w3\\.org|schemas\\.|ns\\.|purl\\.org|xmlns)[\\w.-]+"}
  ],
  "gaps": [
    {"id": "no-hsts", "label": "No HSTS declaration found (may be set by the host/CDN)", "when_none_of": ["hsts"]},
    {"id": "no-csp", "label": "No Content-Security-Policy found", "when_none_of": ["csp"]},
    {"id": "no-headers-config", "label": "No security header configuration found in code", "when_none_of": ["headers-config"]}
  ],
  "edit_guidance": "Add headers in the one place every response passes through: hosting config (vercel.json/netlify.toml/_headers/.htaccess/nginx) or framework headers()/middleware. Replace http:// asset and link URLs with https://."
}
```
