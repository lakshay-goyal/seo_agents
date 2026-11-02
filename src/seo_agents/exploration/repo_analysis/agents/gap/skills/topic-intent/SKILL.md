---
name: topic-intent
description: Label one page's primary topic and search intent from its title, headings and opening copy.
tier: small
votes: 3
---
## Output (JSON only)
{"primary_topic": "<2-6 words>", "intent": "informational|commercial|transactional|navigational|local", "target_queries": ["<query>", "<query>", "<query>"], "reason": "<=160 chars"}

## Rules
1. Use only the given text; do not invent products, places or brands.
2. intent: transactional = buy/book/sign up now; commercial = compare/evaluate; informational = learn; navigational = find a brand/page; local = place-bound service.
3. target_queries are realistic searches this page could rank for, lowercase, 2–6 words.

## Inputs
- `url`, `title`, `description`, `headings` (H1–H2), `copy` (first ~300 words)

## Example
title "Pricing | Acme", h1 "Simple pricing", copy "Starter $9 per seat..."
-> {"primary_topic": "acme pricing plans", "intent": "commercial", "target_queries": ["acme pricing", "acme plans cost", "team tool pricing"], "reason": "plan comparison with prices"}

## Done when
- intent from enum; exactly 3 queries; topic short.
