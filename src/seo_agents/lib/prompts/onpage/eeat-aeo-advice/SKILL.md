---
name: eeat-aeo-advice
description: Optional advice (not copy) on E-E-A-T, AEO and GEO gaps tied to this page, plus the facts only the owner can supply.
kind: llm
principles: P5, P6, P8
order: 5
---
## Inputs (packet)
- `eeat_observed`: deterministic signals (authorship, dates, trust pages, citations)
- `site_type`, `page_type`, `brand`, `facts`, `profiles` (sameAs candidates), `topics` (allowed topic ids)
- `plan` (planned H1/answer), `keywords` (primary, questions, intent, journey_stage, source), `entities`
- `content`: has_faq, has_steps, has_dates, external_sources (what the page actually contains)

## Output (JSON only)
{"advice": [{"topic": "<one of topics>", "text": "<10-300 chars, concrete and specific to this page>"}],
 "missing_facts": ["<fact only the owner can supply, e.g. [VERIFY: years of experience]>"]}

## Rules
1. At most 8 advice items, each tied to this page and its primary keyword; skip anything already in `eeat_observed`.
2. E-E-A-T: experience (first-hand evidence: own projects, photos, results), expertise (credentials, author bio),
   authority (citations, profiles), trust (sources for claims, contact path, policy pages for business/ecommerce).
3. AEO/GEO: entity consistency (one name everywhere), `geo.same_as` (profiles), `geo.llms_txt`, quotable entity
   statements; `aeo.faq_schema` only if `content.has_faq`, `aeo.howto` only if `content.has_steps`.
4. Personal/portfolio pages: no author box, byline or "last updated" advice (posts excepted).
5. Advice only - you write no page copy here. No invented facts or numbers.
