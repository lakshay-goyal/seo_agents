---
name: media-technical
description: Image technical fixes from measured data - width/height, lazy-loading of above-the-fold images, weight/format/overserving at 1x and 2x DPR. Deterministic.
kind: deterministic
order: 6
---
## What the function does (`checks.check_media_technical`)
- Missing width/height with known intrinsic size -> one `set_attr` change per `<img>` location. For images rendered from
  a data list the change targets the template line once (`scope: template`, values `{item.width}`/`{item.height}`).
- Above-the-fold image with `loading="lazy"` -> eager + `fetchpriority="high"`; on a shared template only the first
  item loads eagerly.
- Transfer > 200 KB, 2x-DPR overserve > 1.5, or PNG/JPEG without srcset -> ONE aggregated advice item listing each
  image with bytes, natural/rendered size and the 1x/2x ratios (resizing is an asset task, not a code edit).
