---
name: explore-state-management
description: Find state and data-fetching libraries and where page content depends on client state.
---

State libraries are neutral for SEO unless page content only exists in client
state. Record which store/query layer is used and where content components read
from it; server-prefetching (dehydrate, initialData, SSR stores) means content is
available in the initial HTML.

Runtime profile: [`profiles/state-management.json`](../../profiles/state-management.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). This file is human documentation only.
