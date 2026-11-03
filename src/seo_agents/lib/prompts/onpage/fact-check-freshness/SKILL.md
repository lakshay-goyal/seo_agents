---
name: fact-check-freshness
description: After content is generated - verify every proposed claim against the page facts (unsupported claims block the change), check helpfulness and freshness, and record a per-page quality verdict. Deterministic.
kind: deterministic
order: 11
---
## What the function does (`checks.check_fact_check`)
1. Corpus = visible served text + data values only (title, description, headings, blocks, link anchors and
   aria-labels, image alts). JSX snippets, byte counts, HTTP codes and file paths are excluded.
2. For each change written by a model (intent, page_plan, media_links, advice): numbers in digits or words not in the
   corpus, capitalised terms not in the corpus, and (title/description/headings/content) sentences whose content words
   are mostly absent from the facts (keywords, brand and entities excepted) -> `status: blocked`, `blocked_by:
   fact_check`, `evidence.fact_check` and a client input to confirm. `[VERIFY: ...]` placeholders -> `needs_input`.
3. Helpfulness: does the direct answer address the primary keyword/intent; is each question answered (current page,
   proposal, or needs owner facts); which competitor `content_gaps` the proposal covers.
4. Freshness: years older than run year - 2 in freshness-sensitive copy (current and proposed) and future years.
5. Posts (page type article) without a visible date -> a `needs_input` change for the publication date. Personal and
   home pages never get a "Last updated" line.
6. `quality`: verdict pass | needs_work | fail with reasons (fail = blocked claims, no plan or intent not answered).
