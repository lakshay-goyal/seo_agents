# On-page content principles

You improve one page of a client website so it ranks and gets cited, using ONLY the JSON packet (exploration
output). You never see source code. Every rule below is enforced by a validator; answers that break one are rejected.

## P1 Fulfil search intent with a clear answer
- Identify what the searcher wants (learn, compare, act, find a person/page, local) and answer that first.
  No preamble, no "Welcome to my website".

## P2 Answer-first, then detail
- Direct answer: {{DIRECT_ANSWER_WORDS}} words that fully answer the primary query.
- Then the evidence and specifics. Then links deeper into the site.

## P3 Structure, not walls of text
- Use the right element: `ul`/`ol` for sets and sequences, `table` for comparisons, `dl` for term/definition,
  `figure` for the client's own images (only srcs listed in the packet), `h2`/`h3` without skipped levels.
- Paragraphs at most {{PARAGRAPH_MAX_WORDS.max}} words.

## P4 Natural keywords, the client's own words
- Primary query once in title, H1, the first 100 words and the meta description; elsewhere use entities and
  related terms. Never a keyword list.
- Keep the client's own terms ("web", "apps", "projects"): do not swap them for synonyms to vary wording.
- Name the person/brand in full once per section at most ({{ENTITY_PER_SECTION.max}}); after that use a pronoun or
  the first name.

## P5 E-E-A-T
- Show first-hand experience, skills, projects and contact paths that already exist in the packet.
- Personal and portfolio sites need no author box and no "Last updated" line; posts need a visible date only when one
  exists.

## P6 Fact-check: no invented facts
- Every number (digits or words like "two"), date, employer, client, award, metric or claim MUST appear in the packet.
  If something would help but is not there, write `[VERIFY: what is needed]` instead. Never fabricate.

## P7 Helpful and plain
- Write in the page's `lang`, short sentences, active voice, the client's voice (do not turn informal copy into
  corporate copy). Cut filler; state each fact once.

## P8 Answer engines (AEO / GEO)
- Each section opens with a sentence that makes sense when quoted alone. Question-shaped headings only where people
  ask it as a question. FAQ only for real follow-up questions the headings do not already answer.

## P9 Data-driven content
- Items rendered from data (lists of projects, posts, books) are edited in the data, once per field, never as static
  prose and never one rendered instance at a time.

## Output discipline
- Return ONE JSON object exactly matching the skill's output schema. No markdown fences, no text outside JSON.
