---
name: explore-seo-page-structure
description: Extract heading hierarchy and semantic landmarks per page from composed templates.
---

## Output per page
- `headings`: `[{file, line, source_id, level: 1..6, text}]` in page order (layouts first,
  then the template and the components it renders).
- `landmarks`: `[{file, line, source_id, landmarks: {tag: count}}]` one record per file
  with main/nav/header/footer/aside/article/section counts.

## Hard rules
1. Use `index["elements_by_file"]`: an element is a heading when `heading_level` is set
   (covers `<h2>`, `<Heading level={2}>`, `<Typography variant="h1">`, Markdown `#`).
2. `text` is the literal heading text (`text_literal`). When the text is an expression
   (`{title}`, `{{ product.title }}`, `<?php the_title(); ?>`), emit the heading with
   `text: ""`, `value_origin: "expression"`, `origin_ref: <element id>` and note it in limitations.
3. Follow composition: for a component element with `component_ref`, include that file's
   headings at that point. Visit each file once per page.
4. Never merge alternative branches (`{% if %}`, ternaries) into one hierarchy silently;
   note them in limitations.

## Example
Element `{id: "e_1a", line: 22, col: 8, tag: "h1", text_literal: "Simple pricing", heading_level: 1}`
in `app/pricing/page.tsx` -> `{"file": "app/pricing/page.tsx", "line": 22, "level": 1,
"text": "Simple pricing", "source_id": "app/pricing/page.tsx:22:8:h1"}`.
Liquid `<h1>{{ product.title }}</h1>` -> `{"text": "", "value_origin": "expression",
"origin_ref": "e_…", ...}`.

## Done when
Every page lists headings from its whole composition, levels are ints 1–6, and `test_script` passes.
