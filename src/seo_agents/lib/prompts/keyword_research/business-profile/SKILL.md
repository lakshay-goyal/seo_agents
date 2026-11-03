---
name: business-profile
description: Describe what the site offers and to whom (brand, site type, offering, audience, value props, seed topics, competitors, excluded terms). Runs once per site; every keyword iteration builds on it.
kind: llm
principles: K1, K9
---
## Inputs (packet)
- `site`: exploration-derived site packet (brand candidates, pages with titles/headings/copy, site-type hint, links)
- `market`: target country and language

## Output (JSON only)
{"brand": "<brand or person name exactly as written in the packet>",
 "site_type": "personal|business|ecommerce|publisher|docs|saas|other",
 "offering": "<one sentence: what is sold / provided, <= {{OFFERING_MAX_CHARS}} chars>",
 "audience": ["<who it is for>"],
 "value_props": ["<why choose it, as stated in the packet>"],
 "seed_topics": ["<2-5 word topics the site covers>"],
 "competitors": ["<competitor names/domains mentioned in the packet, else empty>"],
 "excluded_terms": ["<competitor brands and terms the site must not target>"]}

## Rules
1. `brand` must be copied verbatim from the packet.
2. Every list holds at most {{PROFILE_LIST_MAX}} items; empty lists are fine except `seed_topics`.
3. `competitors` only lists competitors the packet names. Do not guess market competitors.
4. `seed_topics` describe needs the site serves in its visitors' words, not generic categories.

## Done when
- Valid enums, brand present in the packet, at least one seed topic.
