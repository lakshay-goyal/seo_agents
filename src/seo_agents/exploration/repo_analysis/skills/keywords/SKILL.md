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

```json profile
{
  "name": "keywords",
  "weights": {"title": 6, "h1": 5, "description": 4, "declared": 4, "h2": 3, "h3": 2, "url": 2, "alt": 1.5, "heading": 1.5, "body": 1},
  "limits": {"unigrams": 100, "bigrams": 60, "trigrams": 40, "occurrences": 8, "per_file": 15},
  "signals": [],
  "gaps": [],
  "edit_guidance": "Place the primary keyword of a page in its title, H1, meta description, URL slug and first paragraph; use bigram/trigram candidates for H2s and anchor text. Avoid targeting the same primary phrase on several URLs."
}
```
