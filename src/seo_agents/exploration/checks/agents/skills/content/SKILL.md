---
name: explore-seo-content
description: Extract page copy and content hierarchy from the observed frontend framework and local data.
---

Identify page routes and their entry components, layouts, local content/data imports,
translation files and templates. Resolve static composition and enumerable maps from
source, preserving order and provenance. Distinguish initial copy from alternate
interaction states; do not combine mutually exclusive branches as one visible page.

Capture full literal copy, headings, navigation, buttons and accessible UI labels,
with section context. Avoid double counting parent/child descendant text, counting
code or treating expressions as user-facing strings. Keep link/media source IDs
consistent with the shared contract. Compute word_count from emitted blocks.
Report unresolved server/CMS data, bindings, conditionals and CSS visibility as gaps.

When earlier link/media reports are available, references are checked against those
reports during your own verification. A reference must resolve for the same URL
and template, not merely exist in another page or share a source line. Query the
earlier links/items to check exact source IDs. Do not guess columns or use nearby
elements as substitutes. If you observe content whose corresponding link/media
record is missing from the earlier report, retain the copy in blocks and explain
the unresolved reference in coverage.limitations; omit that unsupported reference.
Do not remove content blocks to make reference validation pass.
For a mapped link, the array row supplying its label/href and the rendered anchor
template have different source locations. Match the earlier report's element
occurrence for the reference; retain data-row provenance separately on the block.

Every block's text, tag and section must be strings. A section is a semantic tag,
not a section descriptor object or null. Use body as the fallback when no semantic
parent is established, retain that uncertainty in coverage, and store richer
hierarchy separately. Before submitting, inspect every emitted block's field types
and compute word_count only from those literal text strings.
