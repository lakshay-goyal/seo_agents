---
name: explore-seo-eeat
description: Collect candidate authorship, experience, expertise and trust evidence with source and page context.
---

Trace visible page copy, bylines, author/profile components, dates, case studies,
qualifications, reviews and policies through the actual template/data conventions.
Inspect surrounding section context before classifying an ambiguous word. Record
candidate signals for authorship, experience, expertise, trust, reviews and dates.

Capture contact/about/editorial/privacy/terms links and potential external citations
with original href and source evidence. Observing a credential, review or company
claim does not verify its truth. Use candidate_evidence when evidence is present,
otherwise not_observed_in_static_content; never equate static absence with runtime
absence. Keep interpretation explicit about these limits and page-purpose context.

Each signal's evidence is a list of observation objects, never strings, source IDs
alone, or explanations of missing evidence. Build each observed item from its
source location: `{file, line, source_id, text}` with literal `text` as a string.
For example, a parsed byline can use `dict(ref, text=node['text'])`, where `ref`
contains the actual file, line and stable source_id described in the contract.
For an absent signal return
`{"signal": "authorship", "status": "not_observed_in_static_content", "evidence": []}`.
Put absence explanations and unresolved semantics in `interpretation` or
`coverage.limitations`; do not invent a location or text to fill an evidence item.
Trust links and citations are separate observation objects with
`{file, line, source_id, href}`; they do not replace signal evidence objects.
