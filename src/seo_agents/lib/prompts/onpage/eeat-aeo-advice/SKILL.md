---
name: eeat-aeo-advice
description: Optional advice (not copy) on E-E-A-T and answer-engine gaps, plus the facts only the owner can supply.
kind: llm
principles: P5, P6
order: 5
---
## Inputs (packet)
- `eeat_observed`: deterministic signals (authorship, dates, trust pages, citations)
- `site_type`, `page_type`, `brand`, `facts`, `profiles`, `topics` (allowed topic ids), `plan` (planned H1/answer)

## Output (JSON only)
{"advice": [{"topic": "<one of topics>", "text": "<10-300 chars, concrete and specific to this page>"}],
 "missing_facts": ["<fact only the owner can supply, e.g. [VERIFY: years of experience]>"]}

## Rules
1. At most 6 advice items; skip anything already present in `eeat_observed`.
2. Personal/portfolio pages: no author box, byline or "last updated" advice (posts excepted).
3. Advice only - you write no page copy here. No invented facts or numbers.
