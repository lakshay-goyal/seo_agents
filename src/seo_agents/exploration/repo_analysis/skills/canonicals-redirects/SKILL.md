---
name: explore-canonicals-redirects
description: Find canonical declarations, redirect/rewrite rules, trailing-slash policy and 404 handling in code and hosting config.
---

Canonicals: `<link rel="canonical">`, `alternates.canonical`, `canonical` props.
Redirects live in framework config (`redirects()`, `routeRules`, `redirects:`),
hosting config (`vercel.json`, `netlify.toml`, `_redirects`, `.htaccess`, nginx
`return 301`/`rewrite`), middleware and handler code (`redirect(`, `res.redirect`,
`permanentRedirect`). Trailing-slash settings decide which URL variant is primary.
A custom 404 page/handler matters for soft-404s.

```json profile
{
  "name": "canonicals-redirects",
  "signals": [
    {"id": "canonical", "label": "Canonical declaration", "regex": "rel\\s*[=:]\\s*['\"]canonical['\"]|\\bcanonical\\s*[:=]", "ignore_case": true},
    {"id": "redirect-config", "label": "Redirect/rewrite config block", "regex": "\\b(?:async\\s+)?(?:redirects|rewrites)\\s*(?:\\(\\s*\\)|:)|\"(?:redirects|rewrites)\"\\s*:|\\[\\[redirects\\]\\]", "block": true},
    {"id": "redirect-file", "label": "Redirect rules file", "regex": "(?:^|/)(?:_redirects|\\.htaccess|nginx[\\w.-]*\\.conf|vercel\\.json|netlify\\.toml|staticwebapp\\.config\\.json)$", "files_only": true},
    {"id": "server-redirect", "label": "Redirect in code", "regex": "\\b(?:permanentRedirect|redirect|res\\.redirect|RedirectResponse|redirect_to|Redirect::to|HttpResponsePermanentRedirect)\\s*\\(|^\\s*(?:Redirect(?:Match|Permanent)?|RewriteRule)\\s|\\breturn\\s+30[178]\\b", "block": true},
    {"id": "middleware", "label": "Request middleware (can redirect/rewrite every URL)", "regex": "(?:^|/)(?:middleware|hooks\\.server)\\.(?:ts|js)$|(?:^|/)server/middleware/", "files_only": true},
    {"id": "trailing-slash", "label": "Trailing-slash policy", "regex": "trailingSlash\\s*[:=]|APPEND_SLASH|strict_slashes|trailing_slash", "ignore_case": true},
    {"id": "not-found", "label": "Custom 404 page/handler", "regex": "(?:^|/)(?:404|not-found|\\+error|error|_error)\\.(?:html|tsx|jsx|ts|js|vue|svelte|astro|md|php|erb)$", "files_only": true}
  ],
  "gaps": [
    {"id": "no-canonical", "label": "No canonical declaration found", "when_none_of": ["canonical"]},
    {"id": "no-404", "label": "No custom 404 page/handler found", "when_none_of": ["not-found"]}
  ],
  "edit_guidance": "Declare an absolute self-referencing canonical in the shared head (layout/metadata API) built from the site URL + route; put permanent redirects in the hosting or framework redirect config; keep one trailing-slash policy."
}
```
