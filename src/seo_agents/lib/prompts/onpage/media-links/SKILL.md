---
name: media-links
description: Descriptive alt text for images with missing/weak alts, better anchors for generic in-content links, and up to three contextual internal links.
kind: llm
principles: P4, P6
order: 4
---
## Inputs (packet)
- `images`: [{id, src, alt, alt_status, data_driven}] - only images whose alt is missing, generic or template-composed
- `links`: [{id, href, anchor, section}] - only links with generic/one-word in-content anchors
- `site_urls`: pages this page does not link to yet; `sections`: [{id, heading}] where a new link may go
- `intent`, `brand`, `h1`

## Output (JSON only)
{"alts": [{"id": "img1", "alt": "<{{ALT_CHARS}} chars: what the image shows and why it is here>", "decorative": false}],
 "anchors": [{"id": "lnk_...", "anchor": "<{{ANCHOR_WORDS}} words>", "reason": "..."}],
 "new_links": [{"href": "<one of site_urls>", "anchor": "...", "section_id": "s2", "reason": "..."}]}

## Rules
1. Alts describe the image (no "image of"); for `data_driven` images write the alt for that one item (it is stored in
   the item's data, the template reads it).
2. Navigation/header/footer anchors stay short ({{NAV_ANCHOR_WORDS}} words); never add the brand name to a nav label.
3. At most {{NEW_LINKS.max}} new links, only where the section topic matches the target page.
4. Omit any item you would not change (empty lists are fine). Facts only from the packet.
