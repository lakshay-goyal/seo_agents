---
name: heading-structure
description: The current heading outline - one visible H1 inside main content, no skipped levels, no empty, duplicate or overlong headings, H2 sections on long pages. Deterministic.
kind: deterministic
order: 13
---
## Checks (`checks.heading_structure_check.check_heading_structure`)
Reads `heading_records` in document order. Data-driven outlines (headings rendered from JSON/CMS templates) are
reported as advice only; no per-instance edits are proposed on a shared template. Skipped levels, order and duplicates
are judged on the route's own outline only: headings in main content (`in_main` None counts as main) that are not
data-driven. The primary H1 (first visible, non-empty, non-data-driven H1 in main content; fallback the first
non-empty) is `primary_h1(ctx)`, shared with page-plan so the H1 rewrite and the demotions act on the same element.

| Check | Pass condition | Output |
| --- | --- | --- |
| single H1 | exactly one H1 (empty ones count) | change `headings.h1_extra` per extra H1 with an edit location: a tag change h1 -> h2 with the text unchanged (`tag_from`/`tag_to`, html `<h2>text</h2>`), always status `needs_input` (no exact tag op) + advice `headings.multiple_h1`; QA never counts it as fixed |
| H1 in main | at least one H1 outside header/nav/footer | advice `headings.h1_outside_main` |
| H1 visible | no hidden H1 | advice `headings.h1_hidden` (high) |
| empty headings | every heading has text | advice `headings.empty` |
| skipped levels | no jump of more than one level (H2 -> H4) in the route's own outline | advice `headings.skipped_levels` (advice-only; QA `heading_structure` after = `advice`) |
| order | no H2-H6 before the first H1 (route's own outline) | advice `headings.before_h1` (low) |
| duplicates | heading texts are unique (route's own outline) | advice `headings.duplicate` (low) |
| length | headings <= 90 characters | advice `headings.too_long` (low) |
| sections | pages over 300 main-content words have an H2 | advice `headings.no_h2` |
