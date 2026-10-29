---
name: explore-seo-metadata
description: Extract literal head declarations, metadata APIs and inherited layout metadata from frontend source.
---

Inspect the actual framework's head conventions: HTML head, framework metadata
exports, head composables/services/components, frontmatter and static site config.
Trace page declarations and ancestor layouts; retain source and scope rather than
assuming runtime override/merge semantics. Include title, description, robots,
canonical, Open Graph, Twitter cards and locale declarations when observed.

Resolve literal values and local lookup data only. Do not treat arbitrary title
object keys as SEO metadata without evidence of head usage. Distinguish missing
declarations from dynamic generators/CMS values. Explain unresolved inheritance,
title templates, generated URLs and social-image bindings in coverage and the
inheritance_note. Preserve exact values without optimizing or rewriting them.
