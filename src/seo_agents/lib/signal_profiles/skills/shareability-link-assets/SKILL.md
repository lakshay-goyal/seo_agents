---
name: explore-shareability-link-assets
description: Find social share cards, share buttons, feeds and linkable assets that earn backlinks.
---

Off-page authority grows from content worth linking to and easy to share. Recorded: Open
Graph/Twitter card declarations (any key order), OG image generators (`ImageResponse`, `@vercel/og`)
and file conventions (`opengraph-image.*`, `twitter-image.*`), share buttons and intent URLs, RSS/Atom
feeds, embeds, downloadable resources, linkable sections and newsletter capture.

Runtime profile: [`profiles/shareability-link-assets.json`](../../profiles/shareability-link-assets.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). Python post-processing: `agents/shareability_link_assets.py`. Every backticked token in this file is either in the profile or listed in `COVERED_BY_PYTHON` in `tests/test_skill_docs.py`. This file is human documentation only.
