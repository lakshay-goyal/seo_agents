---
name: content-quality
description: Thin content by page type, near-duplicate clusters, long paragraphs, Flesch readability (English), placeholder copy, heading-to-text ratio and repeated boilerplate. Deterministic.
kind: deterministic
order: 14
---
## Checks (`checks.content_quality_check.check_content_quality`)
Word count = max(main-content words, rendered words). An empty shared SPA shell is not reported as thin: that is
the prerender issue owned by technical-onpage. All findings are advice (content changes come from page-plan).

| Check | Pass condition | Output |
| --- | --- | --- |
| thin content | words >= threshold (home 250, article 600, about/contact/collection 150, other 300) | advice `content.thin` |
| near-duplicate | page is not in a site duplicate cluster | advice `content.duplicate` (high) listing peers |
| paragraph length | paragraphs <= 70 words | advice `content.long_paragraphs` (low, up to 5 samples) |
| readability | Flesch reading ease >= 40, scored over paragraph blocks only (tag `p`, or untagged text with sentence punctuation); English pages with 100+ paragraph words | advice `content.readability` (low) |
| placeholder copy | no `lorem ipsum`, `dolor sit amet`, `your text here`, `sample text`, `insert text/content here`, `placeholder text` | advice `content.placeholder` (high) |
| heading ratio | >= 20 words per heading when there are 4+ headings | advice `content.heading_ratio` (low) |
| boilerplate | no sentence (5+ words) repeated more than 3 times | advice `content.boilerplate` (low) |
