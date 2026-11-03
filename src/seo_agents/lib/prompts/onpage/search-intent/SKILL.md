---
name: search-intent
description: Decide the page's primary query, search intent, journey stage, user questions, site/page type and topic-cluster role. Every later step builds on this.
kind: llm
principles: P1, P6
order: 1
---
## Inputs (packet)
- `page`: url, title, description, headings, copy excerpt, images, visible links, lang
- `framework`: stack fingerprint (context only)
- `site_type_hint`: deterministic guess (personal | business | ecommerce | publisher | docs | other)
- `site`: brand candidates, site URLs with titles
- `exploration_keywords`: hints from exploration (may be empty)
- `researched` (optional): keywords from keyword research (search data). They are fixed; see rule 5.

## Output (JSON only)
{"brand_name": "<person/brand name exactly as written in the packet>",
 "site_type": "personal|business|ecommerce|publisher|docs|other",
 "page_type": "home|profile|about|contact|collection|article|product|docs|other",
 "primary_topic": "<2-6 words>",
 "intent": "informational|commercial|transactional|navigational|local",
 "journey_stage": "awareness|consideration|decision|retention",
 "primary_query": "<lowercase, 2-6 words>",
 "secondary_queries": ["<0-6 related queries>"],
 "entities": ["<people, brands, technologies, places named in the packet>"],
 "user_questions": ["<0-6 questions answerable from the packet>"],
 "cluster": {"role": "pillar|cluster|standalone", "pillar_url": "<site url or null>", "related_urls": ["<site urls>"]},
 "answer_gap": "<what the page fails to answer, <=200 chars>"}

## Rules
1. Queries are hypotheses (no search-volume data is available): pick what this site can credibly rank for. A personal
   portfolio ranks for the person's name + role, not for generic topics.
2. `entities` and `user_questions` come only from the packet. If unsure, return fewer items (empty lists are fine).
3. `pillar_url` / `related_urls` must be URLs from `site.urls`.
4. Name and person queries are navigational.
5. When `researched` is present, omit every field it contains (they are ignored); answer brand_name, site_type,
   page_type, primary_topic, entities, answer_gap and only the keyword fields `researched` lacks.

## Done when
- One primary query, valid enums, every entity present in the packet.
