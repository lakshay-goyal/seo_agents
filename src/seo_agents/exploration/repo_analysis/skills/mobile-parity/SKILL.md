---
name: explore-mobile-parity
description: Find viewport configuration, responsive design signals and content hidden on mobile.
---

Google indexes the mobile rendering. Check the viewport declaration (meta tag or
framework viewport export), zoom restrictions, responsive images (`srcset`,
`sizes`, `<picture>`), media queries / responsive utility classes, and content
removed on small screens (`hidden md:block`, `d-none d-md-block`,
`display:none` in mobile media queries, `isMobile` branches).

Runtime profile: [`profiles/mobile-parity.json`](../../profiles/mobile-parity.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). This file is human documentation only.
