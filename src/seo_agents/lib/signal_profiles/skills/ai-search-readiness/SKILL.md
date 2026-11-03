---
name: explore-ai-search-readiness
description: Find signals that help AI search/answer engines (GEO/AEO) - llms.txt, AI crawler policy, FAQ/Q&A, extractable structure.
---

AI answer engines favour pages they may crawl and can quote. `ai_crawler_policy` gives allow /
disallow / partial per AI bot from the parsed `robots.txt` groups (a bot name in a UA list or
analytics code is not a policy). Also recorded: an `llms.txt` summary, FAQ/HowTo/QAPage content and
schema (types reused from the structured-data agent), question-style headings, TL;DR blocks, dates, and
`structure_by_file` counts of landmarks and lists/tables (counts, not one record per element).

Runtime profile: [`profiles/ai-search-readiness.json`](../../profiles/ai-search-readiness.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). Python post-processing: `agents/ai_search_readiness.py`. Every backticked token in this file is either in the profile or listed in `COVERED_BY_PYTHON` in `tests/test_skill_docs.py`. This file is human documentation only.
