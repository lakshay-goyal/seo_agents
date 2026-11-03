---
name: structured-data
description: Strict JSON-LD @id graph that mirrors everything visible on the page (entity, WebSite, page node, breadcrumbs, primary image, Article/TechArticle, Product/SoftwareApplication with offers and ratings, FAQPage, HowTo, ItemList, VideoObject), validated against schema.org and Google rules. Deterministic.
kind: deterministic
order: 9
---
## What the function does (`checks.check_structured_data`)
Facts come only from the page as visitors see it (`structured_data.schema_facts`) plus the page plan's new visible copy.
Nothing is invented: a property only the owner knows becomes a client input instead.

- Entity: `Person` for personal sites, else `Organization`; `@id` = `<origin>/#person|#organization`.
  `sameAs` = profile/identity URLs only (no repositories, posts, share/intent links). `email`/`telephone` from
  `mailto:`/`tel:` links. `Organization.logo` = an `ImageObject` (`#logo`) from a logo asset (else client input);
  `Person.image` only from a portrait-like image, never a logo. `jobTitle` only when the title's role is in the copy.
- `WebSite` (`#website`) on every page so `isPartOf` always resolves; `publisher` -> entity.
- Page node (`<url>#webpage`): ProfilePage (personal home), WebPage, ItemPage (product), AboutPage, ContactPage,
  CollectionPage; `name`, `description`, `inLanguage`, `isPartOf`, `breadcrumb`, `primaryImageOfPage`, dates when
  visible, `about` entity and `mainEntity` -> the page's main node.
- Primary image (`#primaryimage`): largest above-the-fold content image (>= 50k pixels; never a logo/icon/SVG, a
  decorative image (alt="", background/pattern/spacer), an image repeated 3+ times, or an unsized image without a
  descriptive alt).
- `BreadcrumbList` on non-home pages from the real path hierarchy: only ancestors that exist as indexable pages,
  named by their title.
- Article pages (`/blog/...`, page type article): `BlogPosting`/`Article` (`#article`); docs pages: `TechArticle`.
  `headline` <= 110 chars (word boundary), author = visible byline Person or the entity, publisher = entity,
  `datePublished`/`dateModified` from article meta tags or a visible dated line (else client input for posts),
  `image`, `wordCount`, researched keywords.
- Product pages (page type product, or `/pricing|/products/x|/features|/platform|/solutions...` on non-personal
  sites, or visible prices): `SoftwareApplication`/`WebApplication` for software (Google `applicationCategory`,
  `operatingSystem`, `featureList` from the visible feature list) else `Product` (`brand` -> entity).
  One `Offer` per visible plan price (ISO 4217 currency, plain-number price, `UnitPriceSpecification` with UN/CEFACT
  `unitCode` for "per month/year", free plans = price 0). `AggregateRating` only from a first-party rating shown on
  the page; third-party review-site ratings (G2, Capterra, Trustpilot...) are never marked up.
  Missing price / rating / image -> client inputs.
- `CollectionPage` -> `ItemList` of its direct child pages.
- Featured items (`#showcase`): H3 entries under a Projects/Products/Selected Work-style H2 (never Experience/Skills/
  Team) become an `ItemList` of inline `CreativeWork` (or `Product` under a Products heading on business sites) with
  name, description, a matching image (each image used once) and `creator`/`brand` -> entity.
- `FAQPage`: the plan FAQ (identical text) plus visible question-shaped H2/H3 or `dt` with an answer. Items with
  `[VERIFY]`, `[S#]` or HTML are dropped. (FAQ rich results ended 2026-05; kept for page understanding and AEO.)
- `HowTo`: visible ordered steps under a step-like heading (Getting started, Steps, How to, Install...), or any
  ordered list on a how-to page; `HowToStep` position + text.
- `VideoObject` per embedded/hosted video (YouTube thumbnail derived from the id); `uploadDate` is a client input.

## Validation (`structured_data.schema_rules`)
- Errors: missing/duplicate/relative `@id`, refs that do not resolve, relative URLs, non-ISO dates, prices with
  symbols, unknown currencies/availability/applicationCategory, rating outside worst..best, self-serving
  Organization ratings, headline > 110, HTML or placeholders in text, FAQ/HowTo text not visible on the page,
  BreadcrumbList/ItemList/HowTo positions out of order, QAPage on non-forum pages.
- Optional nodes with errors are pruned (with their references); the page node, WebSite, entity and breadcrumb are
  always kept. `data.validation` lists errors, `needs_input` (Google-required properties the owner must supply),
  warnings and pruned nodes. A graph that still has errors (e.g. no production domain, so @id/url are relative) is
  never proposed: no change, only client inputs. Nested errors (an Offer, a rating, an Answer) prune their owner node.
  Ambiguous currency symbols ($, ¥, Rs) are resolved by the page locale or left out; unlabeled dates are ignored.
- Existing page JSON-LD (`head.json_ld[].value`, parsed by the site model) is merged before validation: extra
  properties of a node we regenerate (e.g. Organization `address`, `foundingDate`) are added to ours (our values win;
  a value that adds an error is reverted), and nodes of types we do not generate (Event, LocalBusiness, Course...)
  are carried over with an `@id`. Carried nodes with errors are pruned and, like any type that cannot be kept,
  become a client input so nothing is dropped silently. The new graph replaces the old block (`set_text`).
- Wrong-case types in existing markup (`product`) are named in the error (`use 'Product'`).
- Measurement (`html_summary`) records text placed directly in a `div`/`section`/`article` (>= 3 words) as a block,
  so FAQ accordions, cards and pricing tiles are visible facts.
- One `insert_node` (or `set_text` when JSON-LD exists) change targeting the head; blocked behind `chg_prerender` on a
  shared-shell SPA so a page's FAQPage/canonical never lands on every route. The script body escapes `</`.
