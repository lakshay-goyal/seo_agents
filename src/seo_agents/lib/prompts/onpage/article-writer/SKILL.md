---
name: article-writer
description: Write one complete, fresh, fact-checked blog article from a brief and competitor research - CTR title, H1, meta description, evergreen URL, answer-first intro, key takeaways, structured sections with lists, tables, a Mermaid diagram and images with alt text, FAQ, internal links and tags.
kind: llm
principles: P1, P2, P3, P4, P6, P7, P8, P10, P11
order: 23
---
## Inputs (packet)
- `brief`: primary_keyword, secondary_keywords, questions (must all be answered), search_intent, format, angle,
  audience, working_title, slug, url, pillar_url; `topics`: the user demand the article serves
- `run_date`: today (freshness); `lang`; `brand`; `business`: what the site offers
- `competitors`: ranking pages [{url, title, h1, outline, word_count, published, modified, formats, faq}] - structure only
- `patterns`: shared competitor patterns: word_count, h2 median, formats (share using tables/lists/images/faq/video),
  common_subtopics (covered by several ranking pages), question_headings, freshness, benchmarks
- `title_inspiration`: ranking titles, their patterns (numbers, year, brackets, questions, power words), people_also_ask
- `sources`: [{id: "S1", url, title, date, passage}] - the ONLY outside facts you may use; cite them as [S1]
- `client_facts`: facts about the client (only these may be said about the brand); `assets`: client images you may use
- `links`: site pages you may link to internally; `min_words`: the minimum body length; `words_per_section`: write
  about this many words in each of ~7 H2 sections (several paragraphs, a list or table, examples) to reach `min_words`

## Output (JSON only)
{"title": "<SEO title tag, {{A.TITLE_CHARS}} chars, primary keyword + one click-through hook>",
 "h1": "<{{A.H1_CHARS}} chars, contains the primary keyword, answers the searcher's question>",
 "meta_description": "<{{A.DESCRIPTION_CHARS}} chars, contains the primary keyword, a reason to click, no double quotes>",
 "intro": "<{{A.INTRO_WORDS}} words: answer the primary keyword directly in the first sentence>",
 "key_takeaways": ["<{{A.TAKEAWAYS}} short items>"],
 "sections": [{"heading": "<H2>", "blocks": [
    {"tag": "p", "text": "... <a href=\"/pricing\">descriptive anchor</a> ... [S2]"},
    {"tag": "h3", "text": "<sub-heading>"},
    {"tag": "ul", "items": ["<item text only>", "..."]},
    {"tag": "ol", "items": ["<step text only>", "..."]},
    {"tag": "table", "caption": "...", "header": ["...", "..."], "rows": [["...", "..."], ["...", "..."]]},
    {"tag": "diagram", "code": "flowchart LR\n  A[Plan] --> B[Run]", "alt": "<what the diagram shows>", "caption": "..."},
    {"tag": "figure", "src": "<one of assets[].src>", "alt": "<descriptive alt>", "caption": "..."},
    {"tag": "image_brief", "filename": "<descriptive-hyphenated-name.webp>", "alt": "<descriptive alt>", "caption": "...",
     "prompt": "<what the designer should create>"},
    {"tag": "blockquote", "text": "<a tip or key point>"}]}],
 "faq": [{"question": "...?", "answer": "<{{A.FAQ_ANSWER_WORDS}} words>"}],
 "tags": ["<{{A.TAGS}} lowercase topic tags, 1-3 words>"],
 "category": "<one blog category>",
 "link_from": [{"url": "<existing page from links[]>", "anchor": "<anchor that names this article's topic>"}]}

## Rules
1. Intent first: the article must resolve what the searcher of `primary_keyword` wants. Each `brief.questions` item
   gets its own H2 or `h3` that asks it, followed by a `p` of {{QUESTION_ANSWER_WORDS}} words that answers it directly
   (or a FAQ item). Then depth: steps, examples, trade-offs, specifics.
2. Learn from `competitors` and `patterns` (P11): cover `common_subtopics`, use the formats most ranking pages use,
   reach at least `min_words` body words ({{A.WORDS.max}} max), then add what they miss (`brief.angle`). Write
   {{A.H2_COUNT}} H2 sections. Never copy their sentences, headings or titles.
3. Title tag (CTR): look at `title_inspiration`, then write your own title of {{A.TITLE_CHARS}} chars that contains the
   primary keyword and at least one hook: a number ("7 ways"), the current year from `run_date` (never another year),
   brackets ("[Checklist]", "(With Examples)"), a question, or a benefit after a colon, or a power word (guide,
   step-by-step, examples, mistakes, checklist, vs). It must match the content. The H1 may be longer and more
   descriptive; both must differ from every competitor title.
4. Visual and structured: at least one `table` (comparison or summary), one `ul`/`ol` inside a section, one `diagram`
   (Mermaid: flowchart/sequenceDiagram/timeline/... on the first line), and one image (`figure` with an `assets` src
   when one shows the subject - e.g. the covers of the books discussed or screenshots of the projects described;
   prefer these real client images - else an `image_brief` for a new image). Use `ol` for ordered steps, `ul` for sets.
5. Bullet formatting: list items are plain text only (no "-", "*", "1." typed in), one line each, 1-40 words, either
   all ending with a period or none, no duplicates; 2-10 items per list.
6. Image SEO: every figure, image_brief and diagram has a unique, specific alt text ({{A.ALT_CHARS}} chars, 3+ words)
   that says what the image shows and, where natural, the topic - never "image of", never a file name, never stuffed.
   `image_brief.filename` is descriptive lowercase-hyphenated words (it becomes the image URL).
7. Facts and freshness: today is `run_date`. Every number, statistic, price, date or study must come from a `sources`
   passage and be cited as [S#] in the same paragraph, list item or table row/caption - only sources dated within the
   last 2 years may back a number, and only sources with a `date` may be cited at all. Claims about the brand only from `client_facts`. If a number would help but no
   source has it, leave it out. Small counts of your own structure ("3 steps") are fine.
   Paraphrase every source in your own words: never reuse a run of 12+ words from a passage (copies are rejected). Put titles of books, tools or
   studies in <em>...</em>.
8. Internal links: inline `<a href="/path">descriptive anchor</a>` to at least {{A.INTERNAL_LINKS.min}} different
   `links[].url` pages where they genuinely help the reader, always including `brief.pillar_url` when set. No external
   inline links (cite sources with [S#] instead). `link_from`: {{A.LINK_FROM}} existing pages that should link to this
   article, with an anchor that names its topic.
9. Keywords: primary keyword in the title, H1, meta description and the first sentence of the intro; elsewhere use
   secondary keywords and natural variants; never stuff. Paragraphs at most {{PARAGRAPH_MAX_WORDS.max}} words.
10. `tags`: {{A.TAGS}} lowercase topic tags for the blog taxonomy (internal tag pages); `category`: one broad section.

## Done when
- Every rule above holds; every number is cited to a fresh source or is in `client_facts`; nothing is copied.
