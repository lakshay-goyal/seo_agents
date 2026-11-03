---
name: link-accessibility
description: Accessible names for icon-only links (only when no text, aria-label, img alt or title exists), JS-only links, broken links. Deterministic.
kind: deterministic
order: 7
---
## What the function does (`checks.check_accessibility`)
- Skips links that do not render (`visible: false`).
- A link with no visible text AND no `aria_label`/`img_alt`/`title` -> `set_attr aria-label` at the link's edit
  location (GitHub, LinkedIn, Email, ... from the href). Links that already have an aria-label get no change.
- `href="#"` / `javascript:` -> advice (crawlers cannot follow it).
- HTTP >= 400 (except 401/403/429/999 bot-blocking codes) -> `links.broken` change with `[VERIFY: working URL]`.
