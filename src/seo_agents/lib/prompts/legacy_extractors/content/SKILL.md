---
name: explore-seo-content
description: Extract all visible copy per page in composed order with section context and link/media references.
---

## Output per page
- `blocks`: `[{file, line, source_id, tag, text, section}]` — `text`, `tag`, `section` are strings.
  `section` is the semantic ancestor (main, nav, header, footer, aside, article, section) or "body".
- `word_count`: int = sum of `len(text.split())` over emitted blocks.
- `link_refs`: `[{source_id, report: "internal_links.json"}]`, `media_refs`: `[{source_id, report: "media.json"}]`.
- Optional `sections` for hierarchy.

## Hard rules
1. Take text from text-bearing leaf elements (h1–h6, p, li, a, button, label, td, figcaption,
   blockquote, span with literal text). Use `text_literal`; never emit both a parent and its child text.
2. Data-backed copy (mapped arrays, front matter, JSON/YAML) -> one block per string with
   `value_origin: "data"` and `origin_ref` = the `index["data"]` record id. Translated copy ->
   `value_origin: "i18n"`, `origin_ref: {"i18n_key": key}` and the catalog value as `text`.
3. Link/media references must reuse the exact `source_id` of the same occurrence in the
   earlier internal_links/media report for the same url/template (`query_prior_report`).
   If the earlier report lacks it, keep the block and add a limitation; omit the reference.
4. Initial-state copy only; tabs/modals/conditional branches go to limitations.

## Example
Vue `<p>{{ $t('home.intro') }}</p>` with `locales/en.json` key `home.intro` = "Fresh tea daily"
-> `{"tag": "p", "text": "Fresh tea daily", "section": "main", "file": "src/pages/index.vue",
"line": 8, "source_id": "src/pages/index.vue:8:4:p", "value_origin": "i18n",
"origin_ref": {"i18n_key": "home.intro"}}`.

## Done when
`word_count` equals the block sum, every block's fields are strings, references resolve, `test_script` passes.
