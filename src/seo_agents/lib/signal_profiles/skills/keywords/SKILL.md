---
name: explore-keywords
description: Derive keyword candidates from served titles, headings, descriptions, URLs, front matter and body text.
---

Keywords are derived only from the repository's served text; repository prose (README, CHANGELOG,
`docs/` without a docs-site generator) is excluded. Each term is scored by where it appears
(`weights`): a word in a title or H1 counts more than one in body text. Unigrams, bigrams and trigrams
are produced after stop-word removal; each keeps counts per field and at most two `file:line`
`examples` (no occurrence lists or snippets). `by_file` holds the top 20 per page file.

These are on-site lexical candidates, not search volume or ranking data. Declared
`<meta name="keywords">` and front matter `tags`/`keywords` are kept separately.

Runtime profile: [`profiles/keywords.json`](../../profiles/keywords.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). Python post-processing: `agents/keywords.py`. Every backticked token in this file is either in the profile or listed in `COVERED_BY_PYTHON` in `tests/test_skill_docs.py`. This file is human documentation only.
