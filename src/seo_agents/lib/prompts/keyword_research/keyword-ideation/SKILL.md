---
name: keyword-ideation
description: Propose keyword candidates for one page (primary, secondary, question, conversational; long tail first) that match what the page offers, learning from the judge's feedback on earlier iterations.
kind: llm
principles: K1, K2, K3, K4, K5, K6, K7, K8, K9
---
## Inputs (packet)
- `page`: url, page_type, lang, title, description, headings, copy excerpt, image alts, existing_queries
- `business`: brand, site_type, offering, audience, value_props, seed_topics, competitors, excluded_terms
- `needs`: how many keywords of each kind are still missing (`primary`, `secondary`, `question`, `conversational`,
  `short_tail`); `limit`: how many candidates to propose; `iteration`: 0 for the first round
- `competitor_keywords`: keywords competitor pages rank for (provider data: volume, kd, position, competitor_url)
- `accepted_on_page`, `rejected_on_page` (with rejection `codes` and `reasons`), `rejection_summary`
- `accepted_elsewhere`, `other_pages`: what the rest of the site already owns

## Output (JSON only)
{"page_intent": "informational|commercial|transactional|navigational|local",
 "candidates": [
   {"keyword": "<lowercase search phrase>",
    "kind": "primary|secondary|question|conversational",
    "tail": "long|short",
    "intent": "informational|commercial|transactional|navigational|local",
    "rationale": "<which user purpose it resolves and why this page satisfies it, <= {{RATIONALE_MAX_CHARS}} chars>",
    "adapted_from": "<the competitor keyword it adapts, or null>"}]}

## Rules
1. `page_intent` is what this page offers, judged from its type and content. Each candidate's `intent` must be one
   this page can satisfy.
2. Propose about `limit` candidates. Cover every kind with `needs` > 0, at least `needs[kind]` of each; do not propose
   kinds whose need is 0. Proposing more than needed is good: the judge rejects many.
3. `tail` is `long` for >= {{LONG_TAIL_MIN_WORDS}} words, `short` for 1-{{SHORT_TAIL_MAX_WORDS}} words. Short tail only
   when `needs.short_tail` > 0, and only as primary/secondary.
4. `question` keywords read as questions (start with a question word). `conversational` keywords are full prompts
   for an AI assistant.
5. On iteration > 0, read `rejected_on_page` and `rejection_summary` first and change direction as K8 says.
6. No numbers about keywords, no URLs or domains, no competitor brands, no keyword already listed in the packet's
   accepted/rejected lists, no duplicates.

## Done when
- Every needed kind is covered, every keyword is specific to this page, valid enums, rationale on every item.
