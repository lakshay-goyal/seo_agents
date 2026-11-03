---
name: explore-mobile-parity
description: Find viewport configuration, zoom restrictions, responsive images, favicon/manifest and content hidden on mobile.
---

Google indexes the mobile rendering. Checked: the viewport declaration (meta tag or framework
viewport export; Next, Nuxt, SvelteKit and Gatsby inject a default, so `no-viewport` is not raised
there), zoom restrictions, responsive images (`srcset`, `sizes`, `<picture>`), media queries (a count,
not records), responsive utility classes that hide content (`hidden md:block`, `d-none d-md-block`,
`isMobile` branches), touch icons/manifest and the favicon (shown in search results).

Runtime profile: [`profiles/mobile-parity.json`](../../profiles/mobile-parity.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). Python post-processing: `agents/mobile_parity.py`. Every backticked token in this file is either in the profile or listed in `COVERED_BY_PYTHON` in `tests/test_skill_docs.py`. This file is human documentation only.
