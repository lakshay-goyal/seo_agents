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

```json profile
{
  "name": "content",
  "signals": [
    {"id": "content-collection", "label": "Content collection / CMS schema", "regex": "defineCollection\\s*\\(|defineDocumentType\\s*\\(|createClient\\s*\\(\\s*\\{[^}]*(?:space|projectId|apiKey)", "block": true},
    {"id": "cms-fetch", "label": "CMS / headless content fetch", "regex": "(?:contentful|sanity|strapi|prismic|storyblok|graphcms|hygraph|datocms|wordpress|ghost|directus|payload)[\\w.]*\\.(?:get|fetch|query)\\w*\\s*\\(|/wp-json/|groq`", "ignore_case": true, "block": true}
  ],
  "gaps": [
    {"id": "no-h1", "label": "No literal <h1> or Markdown '# ' heading found in any template", "when_none_of": ["@heading:1"]}
  ],
  "edit_guidance": "Edit copy where the text record points (template markup, Markdown body or data file value). Content coming from CMS fetches must be changed in the CMS, not in code."
}
```
