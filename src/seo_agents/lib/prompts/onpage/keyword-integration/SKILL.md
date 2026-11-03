---
name: keyword-integration
description: Measure where the target keywords appear today, which questions/prompts the page already answers, stuffing, and the alignment gaps. Deterministic; the same limits are enforced on every generated text.
kind: deterministic
order: 2
---
## What the function does (`checks.check_keyword_plan`)
1. Keywords come from search-intent: researched (`keyword-research`, seo_reports/keywords/keyword_map.json, matched by
   page id then url) or model hypotheses (`model-hypothesis`) when no keyword map exists.
2. Placements of the primary AND each secondary keyword: title, H1, first 100 words, H2, meta description, image alt,
   body count.
3. Questions (AEO): is each answered by an H2/H3 that asks it plus a following paragraph?
4. Conversational prompts (GEO): is there a self-contained, entity-explicit passage ({{QUOTABLE_PASSAGE_WORDS}} words)?
5. Stuffing: terms above {{MAX_TERM_DENSITY_PCT.max}}% on pages of 150+ words, or a keyword above ~1 per 100 words.
6. `alignment_gaps`: missing placements, unanswered questions/prompts, competitor topics (`content_gaps`).
7. Validators reuse the limits: each keyword at most once in short fields (title/description/H1); body at most
   ~1 per 100 words; client terms are never swapped for synonyms; the full brand name at most once per section.
