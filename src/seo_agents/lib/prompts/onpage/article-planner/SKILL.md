---
name: article-planner
description: Plan new blog articles for the site from real user demand (researched questions, content gaps, People Also Ask, competitor sub-topics) - one brief per article with primary keyword, questions, format, angle, evergreen URL slug and pillar page.
kind: llm
principles: P1, P10, P11
order: 22
---
## Inputs (packet)
- `business`: brand, site_type, offering, audience, value_props, seed_topics (what the site sells / is about)
- `existing_pages`: [{url, title, primary}] - topics the site already targets (do not plan an article that competes)
- `pool`: [{id, text, kind, page}] - user demand: `gap` (competitors cover it, the site does not), `question`, `prompt`,
  `paa` (People Also Ask), `subtopic` (shared by ranking competitor pages), `seed` (business topics)
- `count`: maximum number of articles; `url_prefix`: the blog folder (e.g. /blog); `links`: site URLs [{url, title}]
- `run_date`, `lang`

## Output (JSON only)
{"articles": [{
  "topic_ids": ["t3", "t7"],
  "primary_keyword": "<2-8 word phrase a searcher types, built from the words of its topics>",
  "secondary_keywords": ["<natural variants, max 6>"],
  "questions": ["<up to 6 pool texts of kind question/paa/prompt, copied verbatim>"],
  "search_intent": "informational | commercial | transactional",
  "format": "guide | how-to | listicle | comparison | explainer | checklist | case-study | glossary",
  "angle": "<what makes this article more useful than the ranking pages, 10-240 chars>",
  "audience": "<who it is for>",
  "working_title": "<15-90 chars>",
  "slug": "<2-6 lowercase words joined by hyphens>",
  "pillar_url": "<one of links[].url the article supports, or null>"}]}

## Rules
1. Plan up to `count` articles, each serving a different search intent. Prefer `gap`, `question` and `paa` topics
   that fit the business; skip topics unrelated to what the site offers.
2. `primary_keyword` must be what users search: at least half its words come from its `topic_ids` texts. Never a
   primary that an `existing_pages[].primary` already targets, and no two articles with overlapping primaries.
3. `questions`: the user questions this article must answer, copied verbatim from the pool.
4. `format` follows intent: how-to for a process, comparison for "X vs Y" / alternatives, listicle for "best/top/ways",
   explainer/guide for "what is", checklist for audits and launches.
5. URL slug: short, precise, readable words that tell Google and the reader what the page is about. Lowercase,
   hyphens, 2-6 words, no repeated word, no repeat of the folder word in `url_prefix` (no /blog/blog-tips), no year,
   no ids or numbers, no filler words at the start or end, and it includes a word of the primary keyword.
6. `pillar_url`: the existing page this article supports in its topic cluster (a service/product/home page), or null.
