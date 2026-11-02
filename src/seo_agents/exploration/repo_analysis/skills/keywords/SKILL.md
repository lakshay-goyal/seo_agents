---
name: explore-keywords
description: Derive keyword candidates from titles, headings, descriptions, URLs, front matter and body text, with occurrences.
---

Keywords are derived only from the repository's own text. Each term is scored by
where it appears (`weights`): a word in a title or H1 counts more than one in body
text. Unigrams, bigrams and trigrams are produced after stop-word removal; every
candidate keeps sample occurrences (file + line + exact source line) so an agent
can see real usage and place keywords into titles, headings and copy.

These are on-site lexical candidates, not search volume or ranking data. Declared
`<meta name="keywords">` and front matter `tags`/`keywords` are kept separately.

Runtime profile: [`profiles/keywords.json`](../../profiles/keywords.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). This file is human documentation only.
