---
name: explore-content
description: Extract headings, visible text, front matter, links and images from any template or content file.
---

Content is extracted from every template-like file (HTML, JSX/TSX, Vue, Svelte,
Astro, Markdown/MDX, server templates) by structure, not by framework:

- Headings: `<h1>`–`<h6>` tags and Markdown `#` headings, with level.
- Text: literal text between tags and Markdown paragraphs (code fences and
  expressions excluded).
- Front matter: YAML/TOML header of content files (title, description, tags, date,
  author, slug, draft).
- Links: `<a href>`, `<Link to|href>`, `<NuxtLink>`, `<RouterLink>`, Markdown links.
- Images/media: `<img>`, `<Image>`, `<picture>/<source>`, `<video>`, `<iframe>`,
  Markdown images, with alt/width/height/loading attributes.
- Content data: string values in JSON/YAML data files that look like prose.

Text inside `{expressions}` is dynamic and not resolved. The `code` of every
record shows the exact markup, so an agent can rewrite copy in place.

Runtime profile: [`profiles/content.json`](../../profiles/content.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). Every backticked token in this file is either in the profile or listed in `COVERED_BY_PYTHON` in `tests/test_skill_docs.py`. This file is human documentation only.
