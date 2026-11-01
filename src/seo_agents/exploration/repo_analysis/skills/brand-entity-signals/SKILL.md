---
name: explore-brand-entity-signals
description: Find brand/entity signals - Organization/Person schema, sameAs profiles, social links, authorship, about pages, logo.
---

Search engines and AI systems connect a site to an entity through consistent
name, logo, `sameAs` profile URLs, Organization/Person structured data, author
bylines and about/team pages. Record every social profile URL with its location
so profiles can be reconciled with `sameAs`.

```json profile
{
  "name": "brand-entity-signals",
  "signals": [
    {"id": "entity-schema", "label": "Organization/Person/WebSite schema", "regex": "['\"]@type['\"]\\s*:\\s*['\"](Organization|Person|WebSite|Corporation|Brand|ProfilePage|NewsMediaOrganization)['\"]", "value_group": 1, "block": false},
    {"id": "same-as", "label": "sameAs profile list", "regex": "['\"]?sameAs['\"]?\\s*:", "block": true},
    {"id": "social-profile", "label": "Social profile link", "regex": "https?://(?:www\\.)?(?:twitter\\.com|x\\.com|linkedin\\.com/(?:in|company)|github\\.com|facebook\\.com|instagram\\.com|youtube\\.com|tiktok\\.com|medium\\.com|dribbble\\.com|behance\\.net|threads\\.net|mastodon\\.\\w+|bsky\\.app)/[\\w@./-]+", "ignore_case": true},
    {"id": "author", "label": "Author/byline declaration", "regex": "name\\s*=\\s*['\"]author['\"]|\\bauthors?\\s*:\\s*['\"\\[{]|rel\\s*=\\s*['\"]author['\"]|itemprop\\s*=\\s*['\"]author|class(?:Name)?\\s*=\\s*['\"][^'\"]*byline"},
    {"id": "site-name", "label": "Site/brand name declaration", "regex": "og:site_name|siteName\\s*:|applicationName\\s*:|application-name|\\bsite_?[Nn]ame\\s*[:=]"},
    {"id": "logo", "label": "Logo asset/declaration", "regex": "['\"]logo['\"]\\s*:|(?:^|/)logo[\\w-]*\\.(?:svg|png|webp|jpg)|alt\\s*=\\s*['\"][^'\"]*logo", "ignore_case": true},
    {"id": "about-page", "label": "About/team/company page", "regex": "(?:^|/)(?:about|about-us|team|company|our-story|who-we-are)(?:/|\\.|$)", "files_only": true}
  ],
  "gaps": [
    {"id": "no-entity-schema", "label": "No Organization/Person schema found", "when_none_of": ["entity-schema"]},
    {"id": "no-same-as", "label": "Social profiles linked but no sameAs found", "when_none_of": ["same-as"], "only_if_any": ["social-profile"]}
  ],
  "edit_guidance": "Add one Organization (or Person) JSON-LD in the root layout with name, url, logo and sameAs listing the social profile URLs found; keep brand name identical in og:site_name, titles and schema; add author bylines linking to author pages."
}
```
