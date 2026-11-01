---
name: explore-sitemap-robots
description: Find sitemap and robots sources (static files, generators, plugins) and per-page indexing directives.
---

Sitemaps and robots rules exist either as static files (`sitemap*.xml`,
`robots.txt`) or as code that generates them (route handlers, plugins, config).
Static `robots.txt` is parsed into user-agent groups; sitemap XML URLs are listed.
Per-page `noindex`/`nofollow` (meta robots, `X-Robots-Tag`, metadata `robots`
fields) can hide pages even when the sitemap lists them.

```json profile
{
  "name": "sitemap-robots",
  "signals": [
    {"id": "sitemap-file", "label": "Static sitemap file", "regex": "(?:^|/)sitemap[\\w-]*\\.xml$", "files_only": true},
    {"id": "robots-file", "label": "Static robots.txt", "regex": "(?:^|/)robots\\.txt$", "files_only": true},
    {"id": "sitemap-generator", "label": "Sitemap generator code/plugin", "regex": "next-sitemap|@astrojs/sitemap|gatsby-plugin-sitemap|@nuxtjs/sitemap|vite-plugin-sitemap|sitemap_generator|jekyll-sitemap|laravel-sitemap|django\\.contrib\\.sitemaps|MetadataRoute\\.Sitemap|SitemapStream|['\"`/]sitemap(?:\\.xml)?['\"`]", "block": true},
    {"id": "sitemap-route-file", "label": "Sitemap route/endpoint file", "regex": "(?:^|/)sitemap(?:\\.xml)?(?:/\\+server|/route)?\\.(?:ts|js|tsx|jsx|py|rb|php)$|(?:^|/)sitemap\\[\\.\\]xml", "files_only": true},
    {"id": "robots-generator", "label": "Robots generator code/plugin", "regex": "MetadataRoute\\.Robots|gatsby-plugin-robots-txt|@nuxtjs/robots|enableRobotsTXT|['\"`/]robots\\.txt['\"`]", "block": true},
    {"id": "robots-route-file", "label": "Robots route/endpoint file", "regex": "(?:^|/)robots(?:\\.txt)?(?:/\\+server|/route)?\\.(?:ts|js|py|rb|php)$", "files_only": true},
    {"id": "noindex", "label": "noindex / nofollow directive", "regex": "noindex|nofollow|index\\s*:\\s*false|X-Robots-Tag", "ignore_case": true},
    {"id": "feed", "label": "RSS/Atom feed", "regex": "(?:^|/)(?:rss|feed|atom)[\\w-]*\\.(?:xml|ts|js)$", "files_only": true}
  ],
  "gaps": [
    {"id": "no-sitemap", "label": "No sitemap file or generator found", "when_none_of": ["sitemap-file", "sitemap-generator", "sitemap-route-file"]},
    {"id": "no-robots", "label": "No robots.txt or generator found", "when_none_of": ["robots-file", "robots-generator", "robots-route-file"]}
  ],
  "edit_guidance": "Prefer generating the sitemap from the same route/content source the app uses so new pages are included automatically; reference the sitemap URL from robots.txt; remove noindex from pages that should rank."
}
```
