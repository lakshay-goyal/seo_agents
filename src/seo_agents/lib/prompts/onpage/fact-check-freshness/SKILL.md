---
name: fact-check-freshness
description: Verify numbers (digits and spelled-out), names and placeholders in model-written changes against the visible text; route unverified items to client_inputs; flag stale years. Deterministic.
kind: deterministic
order: 11
---
## What the function does (`checks.check_fact_check`)
1. Corpus = visible served text + data values only (title, description, headings, blocks, link anchors and
   aria-labels, image alts). JSX snippets, byte counts, HTTP codes and file paths are excluded.
2. For each change written by a model (intent, page_plan, media_links, advice): numbers in digits or words that are not
   in the corpus, capitalised terms not in the corpus, and `[VERIFY: ...]` placeholders -> `status: needs_input`, an
   entry in `client_inputs` and `evidence.fact_check`.
3. Years older than run year - 2 in freshness-sensitive copy -> advice.
4. Posts (page type article) without a visible date -> a `needs_input` change for the publication date. Personal and
   home pages never get a "Last updated" line.
