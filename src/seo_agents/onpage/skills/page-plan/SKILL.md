---
name: page-plan
description: One coherent plan for the page - title, meta description, H1, heading rewrites, answer-first opening, optional section rewrites and FAQ - with each fact assigned to exactly one slot.
kind: llm
tier: mid
temperature: 0.3
principles: P1, P2, P3, P4, P6, P7, P8, P9
order: 3
---
## Inputs (packet)
- `page`: url, lang, is_home, current title/description, site_type, page_type
- `intent`: primary_query, secondary_queries, user_questions, entities, intent
- `brand`: the person/brand name; `other_titles`: titles of other pages (yours must differ)
- `headings`: [{id, level, text, editable}] - `editable:false` = rendered from data/a shared template: never rewrite it
- `sections`: [{id, heading, data_driven, blocks}] - `data_driven:true` = a list rendered from data: never replace it
- `facts`: [{id, text}] - the ONLY facts you may use
- `assets`: images you may use in `figure` blocks; `links`: site URLs you may link to
- `max_faq`: maximum FAQ items for this page (0 means no FAQ)

## Output (JSON only)
{"title": "<{{TITLE_CHARS}} chars, primary query first, brand last>",
 "description": "<{{DESCRIPTION_CHARS}} chars, no double quotes>",
 "h1": "<{{H1_CHARS}} chars>",
 "outline": [{"level": 2, "text": "<heading>", "replaces_id": "<heading id or null>"}],
 "direct_answer": "<{{DIRECT_ANSWER_WORDS}} words, answers primary_query>",
 "sections": [{"replaces_id": "<section id or null>", "heading": "...", "level": 2,
               "blocks": [{"tag": "p", "text": "..."}, {"tag": "ul", "items": ["...", "..."]}]}],
 "faq": [{"question": "...?", "answer": "<{{FAQ_ANSWER_WORDS}} words>"}],
 "fact_slots": {"f1": "direct_answer", "f4": "sections[0]"},
 "rationale": "<=250 chars"}

## Rules
1. Assign facts to slots FIRST (`fact_slots`), then write: each fact appears in one slot only. No sentence or
   8-word phrase may repeat across direct_answer, sections and FAQ.
2. `outline` lists only headings you change (`replaces_id`) or add (`replaces_id: null`); empty keeps the current ones.
   {{H2_COUNT}} H2s when non-empty. Keep good existing headings.
3. `sections` are optional: rewrite a section only when it is thin or unclear, never a `data_driven` one; a new
   section must answer something the page does not.
4. FAQ: at most `max_faq` items, only questions that no heading already covers (FAQ questions must not restate an H2).
   Use an empty list when nothing qualifies.
5. Name the brand in full at most once per section; use pronouns or the first name after that.
6. Keep the client's own words and voice. Facts only from `facts`; otherwise `[VERIFY: ...]`.
7. Paragraphs at most {{PARAGRAPH_MAX_WORDS.max}} words; links only to `links[].url` with descriptive anchors.

## Done when
- Every limit above holds and every number/name is in `facts`.
