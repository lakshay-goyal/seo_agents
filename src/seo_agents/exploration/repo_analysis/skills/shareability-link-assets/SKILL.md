---
name: explore-shareability-link-assets
description: Find social share cards, share buttons, feeds and linkable assets that earn backlinks.
---

Off-page authority grows from content worth linking to and easy to share. Record
Open Graph/Twitter card declarations (incl. generated OG images), share buttons
and intent URLs, RSS/Atom feeds, embeddable widgets, downloadable resources
(PDF, data, tools), blog/resources/case-study sections and newsletter capture.

```json profile
{
  "name": "shareability-link-assets",
  "signals": [
    {"id": "open-graph", "label": "Open Graph declaration", "regex": "og:(?:title|description|image|url|type|site_name)|openGraph\\s*:\\s*\\{", "block": false},
    {"id": "twitter-card", "label": "Twitter/X card declaration", "regex": "twitter:(?:card|title|description|image|site|creator)|twitter\\s*:\\s*\\{\\s*card"},
    {"id": "og-image-generator", "label": "Generated OG image", "regex": "(?:^|/)(?:opengraph|twitter)-image\\.(?:tsx|jsx|ts|js|png|jpg)$|ImageResponse\\s*\\(|@vercel/og|satori", "files_only": false},
    {"id": "share-intent", "label": "Share button / intent URL", "regex": "twitter\\.com/intent|x\\.com/intent|facebook\\.com/sharer|linkedin\\.com/(?:sharing|shareArticle)|reddit\\.com/submit|api\\.whatsapp\\.com/send|wa\\.me/\\?text|navigator\\.share\\s*\\(|react-share|vue-social-sharing", "ignore_case": true},
    {"id": "feed", "label": "RSS/Atom feed", "regex": "application/(?:rss|atom)\\+xml|@astrojs/rss|from\\s+['\"](?:rss|feed)['\"]|(?:^|/)(?:rss|feed|atom)\\.xml", "ignore_case": true},
    {"id": "linkable-section", "label": "Linkable content section (blog/resources/guides/case studies/tools)", "regex": "(?:^|/)(?:blog|posts?|articles?|resources|guides?|case-studies|research|tools?|glossary|docs)/", "files_only": true},
    {"id": "downloadable", "label": "Downloadable resource link", "regex": "href\\s*=\\s*['\"{][^'\"}]*\\.(?:pdf|csv|xlsx|zip)['\"}]|\\bdownload\\s*(?:=|\\b)", "ignore_case": true},
    {"id": "embed", "label": "Embed code / widget offered", "regex": "<iframe[^>]+src=|embed\\s*code|oembed", "ignore_case": true},
    {"id": "newsletter", "label": "Newsletter / subscribe capture", "regex": "newsletter|subscribe|mailchimp|convertkit|buttondown|substack|beehiiv", "ignore_case": true}
  ],
  "gaps": [
    {"id": "no-og", "label": "No Open Graph tags found", "when_none_of": ["open-graph"]},
    {"id": "no-twitter-card", "label": "No Twitter/X card found", "when_none_of": ["twitter-card"]},
    {"id": "no-feed", "label": "Content sections exist but no RSS/Atom feed found", "when_none_of": ["feed"], "only_if_any": ["linkable-section"]}
  ],
  "edit_guidance": "Add og:title/description/image/url and twitter:card in the shared metadata layer with per-page overrides; generate per-page OG images; expose an RSS feed for content sections; add share links to article templates."
}
```
