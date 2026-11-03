---
name: structured-data
description: JSON-LD @id graph built from the exploration facts and the page plan (WebSite, Person/Organization, page node, BreadcrumbList, FAQPage mirroring the visible FAQ). Deterministic.
kind: deterministic
order: 9
---
## What the function does (`checks.check_structured_data`)
- Entity: `Person` for personal sites, else `Organization`; `@id` = `<origin>/#person|#organization`; `sameAs` = profile
  URLs only (no share/intent links or repository blobs); `jobTitle` only when the title's role appears in the copy.
- `Person.image` only from a portrait-like image; a logo/favicon is never used (client input instead).
  `Organization.logo` may use a logo asset.
- Page node by page type: ProfilePage (personal home), WebPage, AboutPage, ContactPage, CollectionPage, BlogPosting
  (headline, author; datePublished requested from the owner when no date is visible). `WebSite` on the home page.
- `BreadcrumbList` on non-home pages. `FAQPage` only when the page plan has a visible FAQ, with identical question and
  answer text.
- One `insert_node` (or `set_text` when JSON-LD exists) change targeting the head; blocked behind `chg_prerender` on a
  shared-shell SPA so a page's FAQPage/canonical never lands on every route.
