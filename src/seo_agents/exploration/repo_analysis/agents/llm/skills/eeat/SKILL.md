---
name: explore-seo-eeat
description: Harvest candidate E-E-A-T evidence (authorship, dates, credentials, contact, policies, citations) per page.
---

## Output per page
- `signals`: `[{signal, status, evidence: [{file, line, source_id, text}]}]` for signals
  authorship, publish_date, updated_date, credentials, experience, contact, about, policies,
  reviews. `status`: candidate_evidence | not_observed_in_static_content (then `evidence: []`).
- `trust_links`: `[{file, line, source_id, href}]` to about/contact/privacy/terms/editorial pages.
- `citations`: `[{file, line, source_id, href}]` external references inside main content.
- `interpretation`: string — what was and was not observable statically.

## Hard rules
1. Evidence items are source-backed records with literal `text` at the cited line.
2. Look in bylines (`rel="author"`, `.author`, `the_author()`, `article.author`), `<time datetime>`,
   front matter (`author`, `date`), JSON-LD Person/Organization, footers (contact, policies).
3. Runtime values (CMS author, WordPress `the_author()`) are candidates with
   `value_origin: "expression"`; mention them in `interpretation`.
4. You collect candidates; you do not judge credibility.

## Example
`<time datetime="2024-05-01">May 1, 2024</time>` at `content/post.md` rendered by
`_layouts/post.html:7` -> signal `publish_date`, evidence `{"text": "May 1, 2024", "line": 7, ...}`.

## Done when
Every listed signal has a status, absent ones use `evidence: []`, `test_script` passes.
