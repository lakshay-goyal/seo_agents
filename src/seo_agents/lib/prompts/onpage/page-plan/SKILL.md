---
name: page-plan
description: One coherent plan for the page - title, meta description, H1, heading rewrites, answer-first opening, optional section rewrites and FAQ - with each fact assigned to exactly one slot.
kind: llm
principles: P1, P2, P3, P4, P6, P7, P8, P9, P10, P11
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
- `keywords` (only when researched): primary, secondary, short_tail, questions, conversational, questions_answered_now,
  content_gaps (topics competitors cover), alignment_gaps, journey_stage, cluster {role, pillar_url, links}
- `competitors` (only when ranking pages were fetched): pages_analyzed, word_count, h2_median, formats (share using
  tables/lists/images/faq), common_subtopics, question_headings, titles, title_patterns, people_also_ask

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
 "cta": {"text": "<descriptive anchor>", "href": "<one of links[].url>"} or null,
 "unanswerable": [{"question": "<question keyword verbatim>", "needed": "<fact the owner must supply>"}],
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
8. With `keywords`: `keywords.primary` in title and H1 (once each) and in the description and direct answer; secondary
   keywords only as natural variants. Every question not in `questions_answered_now` gets a section (or FAQ item)
   whose heading asks it and whose first block is a `p` of {{QUESTION_ANSWER_WORDS}} words answering it directly;
   if `facts` cannot answer it, list it in `unanswerable` instead. Cover `content_gaps` only where the facts support it.
   For each `conversational` prompt write one self-contained passage that names the brand/entity explicitly.
9. `cta`: one next step that fits the journey stage, linking the cluster pillar or a related page (null if none fits).
10. With `competitors` (P11): use the formats most ranking pages use (a `table` for comparisons, `ol` for steps, `ul`
   for sets), cover `common_subtopics` only where `facts` support them, and write a title with a click-through hook
   learned from `titles`/`title_patterns` (a number, brackets, a question or a benefit) that is your own and matches
   the page. Never copy a competitor heading or sentence.

## Done when
- Every limit above holds and every number/name is in `facts`.
