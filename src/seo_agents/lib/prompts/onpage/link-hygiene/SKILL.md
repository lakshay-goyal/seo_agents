---
name: link-hygiene
description: Link attributes and internal-link health - internal nofollow, target=_blank without noopener, redirected or non-canonical internal hrefs, insecure links, hash routes, self links, link count, orphan pages, click depth and ambiguous anchors. Deterministic.
kind: deterministic
order: 15
---
## Checks (`checks.link_hygiene_check.check_link_hygiene`)
Internal = `kind: internal`, or an absolute URL on the site's host (www/non-www, http/https treated as the same site).
At most one change per (link, field). A fix with no edit location is not a change: all such fixes are listed in ONE
advice `links.no_edit_location`. A fix whose target is a shared template/data map (`scope` template or data - one line
renders every item) is advice `links.shared_template` (`links.href`/`links.rel` are per-instance fields, so the
reference validator also rejects them on a template line).

| Check | Pass condition | Output |
| --- | --- | --- |
| internal nofollow | internal links have no `rel="nofollow"` | change `links.rel` (`set_attr`, rel without nofollow; `remove` when no rel token is left) |
| noopener | `target="_blank"` links have `noopener` (or `noreferrer`) | advice `links.noopener` (low; browsers imply noopener since 2021) |
| redirected internal link | internal href is not redirected (link redirect chain / site redirected_links rows with a final URL) | change `links.href` (`set_attr`, final URL); a plain-URL redirected_links row (final URL unknown) -> advice `links.redirected` |
| canonical host | absolute internal hrefs use the canonical scheme and host (same-host `http://` included) | change `links.href` (root-relative path) |
| insecure external | no `http://` EXTERNAL links on an https site | advice `links.insecure` (low) |
| hash routes | no `#/` or `#!` routing links (internal / fragment links only) | advice `links.hash_routes` (high) |
| self link | main content does not link to this page | advice `links.self_link` (low) |
| link count | <= 150 links | advice `links.too_many` (low) |
| internal links | main content links to other pages (judged only when links carry `in_main` attribution) | advice `links.no_internal` |
| duplicate anchors | one anchor text -> one URL | advice `links.duplicate_anchor` (low) |
| orphan | `link_graph.orphan_candidate` is not True (non-home; skipped when links render only after JS or the site graph has nothing beyond home) | advice `links.orphan` (high) |
| click depth | <= 3 clicks from home | advice `links.click_depth` |
