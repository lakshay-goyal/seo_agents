---
name: media-technical
description: Image technical fixes from measured data - width/height, lazy-loading of above-the-fold images, fetchpriority on the LCP image, alt length/duplicates, descriptive file names, weight/format/overserving at 1x and 2x DPR. Deterministic.
kind: deterministic
order: 6
---
## What the function does (`checks.check_media_technical`)
For images rendered from a data list the change targets the template line once (`scope: template`).

| Check | Pass condition | Output |
| --- | --- | --- |
| dimensions | `<img>` has width/height (intrinsic size known) | change `images.dimensions` (`set_attr`, one per location; template: `{item.width}`/`{item.height}`) |
| lazy above the fold | above-the-fold images are not `loading="lazy"` | change `images.loading` (eager + `fetchpriority="high"`; on a shared template only the first item loads eagerly) |
| LCP priority | the LCP image has `fetchpriority="high"` | change `images.fetchpriority` (`set_attr`, `high`); on a shared template/data map -> advice `images.fetchpriority` (set it on the first item only) |
| alt length | alt text <= the ALT_CHARS limit | advice `images.alt_length` (low) |
| duplicate alt | different images have different alt text | advice `images.alt_duplicate` (low) |
| file name | file stem is descriptive (not IMG_1234 / DSC / PXL / image1 / screenshot / long hex hash) | advice `images.filename` (low) |
| weight | transfer <= 200 KB, 2x-DPR overserve <= 1.5, PNG/JPEG has srcset | ONE aggregated advice `images.weight` listing bytes, natural/rendered size and 1x/2x ratios |

- LCP image = the image whose full src path (or decoded optimizer `url=` param) appears in `performance.mobile.lcp_element`;
  when the LCP element is not an image, none. Without Lighthouse data: the largest above-the-fold image >= ~200x200.
- File stems drop bundler fingerprints (`logo-Bx9k2LmQ.svg` -> `logo`); Next.js `/_next/image?url=...` uses the decoded
  `url=` path. Plain numbers (`2024.png`) are not flagged.
