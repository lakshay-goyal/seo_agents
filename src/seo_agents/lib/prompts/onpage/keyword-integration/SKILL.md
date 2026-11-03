---
name: keyword-integration
description: Measure where the primary query appears today and flag stuffing. Deterministic; the same limits are enforced on every generated text.
kind: deterministic
order: 2
---
## What the function does (`checks.check_keyword_plan`)
1. Takes `primary_query`, `secondary_queries` and `entities` from search-intent (labelled `model-hypothesis`: there is
   no search-volume data).
2. Records current placements: title, H1, first 100 words, H2, meta description, image alt.
3. Records term density of the visible copy and flags terms above {{MAX_TERM_DENSITY_PCT.max}}% on pages of 150+ words.
4. Validators reuse the limits: primary phrase once in short fields (title/description/H1); body at most
   ~1 per 100 words; client terms are never swapped for synonyms; the full brand name at most once per section.
