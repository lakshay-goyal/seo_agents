---
name: explore-mobile-parity
description: Find viewport configuration, responsive design signals and content hidden on mobile.
---

Google indexes the mobile rendering. Check the viewport declaration (meta tag or
framework viewport export), zoom restrictions, responsive images (`srcset`,
`sizes`, `<picture>`), media queries / responsive utility classes, and content
removed on small screens (`hidden md:block`, `d-none d-md-block`,
`display:none` in mobile media queries, `isMobile` branches).

```json profile
{
  "name": "mobile-parity",
  "signals": [
    {"id": "viewport", "label": "Viewport declaration", "regex": "name\\s*=\\s*['\"]viewport['\"]|export\\s+const\\s+viewport\\b|generateViewport|viewport\\s*:\\s*['\"{]", "block": false},
    {"id": "zoom-restricted", "label": "Zoom restricted (user-scalable=no / maximum-scale=1)", "regex": "user-scalable\\s*=\\s*(?:no|0)|maximum-scale\\s*=\\s*1(?:\\.0)?\\b|userScalable\\s*:\\s*false|maximumScale\\s*:\\s*1\\b", "ignore_case": true},
    {"id": "responsive-images", "label": "Responsive images (srcset/sizes/picture)", "regex": "\\bsrcset\\s*=|\\bsrcSet\\s*=|\\bsizes\\s*=|<picture\\b"},
    {"id": "media-queries", "label": "CSS media queries", "regex": "@media\\s*\\(?[^{]*(?:max|min)-width"},
    {"id": "mobile-hidden-content", "label": "Content hidden on small screens", "regex": "\\bhidden\\s+(?:sm|md|lg):(?:block|flex|grid|inline)|\\bd-none\\s+d-(?:sm|md|lg)-|\\bhidden-(?:xs|sm)\\b|\\bisMobile\\b|useMediaQuery\\s*\\("},
    {"id": "touch-icon", "label": "Mobile/PWA icons & manifest", "regex": "apple-touch-icon|rel=['\"]manifest['\"]|(?:^|/)(?:manifest|site)\\.webmanifest$|theme-color"}
  ],
  "gaps": [
    {"id": "no-viewport", "label": "No viewport declaration found (some frameworks add one by default)", "when_none_of": ["viewport"]}
  ],
  "edit_guidance": "Declare `width=device-width, initial-scale=1` once in the HTML shell/layout or viewport export without zoom limits; keep the same primary content, headings, links and structured data on mobile as desktop; use srcset/sizes for large images."
}
```
