---
name: explore-ai-search-readiness
description: Find signals that help AI search/answer engines (GEO/AEO) - llms.txt, AI crawler rules, FAQ/Q&A, extractable semantic structure.
---

AI answer engines favour pages they may crawl and can quote: allow/deny rules for
AI user agents in robots, an `llms.txt` summary, FAQ/HowTo/QAPage content and
schema, question-style headings, concise definitions/TL;DR blocks, lists and
tables, semantic landmarks (`<main>`, `<article>`), and dated, attributed content.

Runtime profile: [`profiles/ai-search-readiness.json`](../../profiles/ai-search-readiness.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). This file is human documentation only.
