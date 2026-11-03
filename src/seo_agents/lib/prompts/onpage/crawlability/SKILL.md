---
name: crawlability
description: Site crawlability - robots.txt rules (disallow-all, sitemap line, blocked CSS/JS, crawl-delay, AI crawler policy, syntax), sitemap coverage, www/non-www consolidation, crawl errors, orphans and click depth, redirected/blocked links, favicon, custom 404, pagination, AMP. Deterministic.
kind: deterministic
order: 20
---
## Checks (`checks.crawlability_check.check_crawlability`)
Site-wide changes use fixed change ids (deduplicated across pages by the site pass); site-wide advice is emitted once
(on the page marked `site_items_owner`: the home page if selected, else the first selected page). Missing / invalid /
SPA-fallback robots.txt / sitemap.xml / llms.txt files are created by the site pass (`chg_sitemap_xml`), not here.
A temporary host redirect is owned by technical-onpage (`chg_redirect_permanent`), soft 404 by technical-onpage
(`chg_soft_404`), http->https by https-security.

| Check | Pass condition | Output |
| --- | --- | --- |
| robots.txt disallow all | `User-agent: *` group does not `Disallow: /` | site change `platform.robots_disallow_all` (`chg_robots_disallow_all`, high) |
| robots.txt sitemap line | a `Sitemap:` directive is present | advice `crawl.robots_sitemap` |
| robots.txt assets | no Disallow on CSS/JS paths (/_next/, /static/, /assets/, *.js, *.css) | advice `crawl.robots_assets` (high) |
| robots.txt crawl-delay | no Crawl-delay (Google ignores it) | advice `crawl.robots_crawl_delay` (low) |
| AI crawler policy | explicit allow/disallow per AI bot when the gap `no-ai-crawler-policy` is reported | advice `crawl.ai_crawlers`; data lists allowed/blocked/partial bots |
| robots.txt syntax | every line is `key: value` with a known directive | advice `crawl.robots_syntax` (low) |
| sitemap coverage | an indexable page is listed in the live sitemap (only when /sitemap.xml is served and exploration discovered URLs from it) | per-page advice `crawl.sitemap_coverage` |
| sitemap entries | listed URLs are 200, indexable, not redirected | advice `crawl.sitemap_bad_entry` / `crawl.sitemap_bad_entries` (high) |
| host redirect | www/non-www redirect recorded; skipped on local origins | data `host` only (the permanent-redirect fix belongs to technical-onpage) |
| host consolidation | the alternate host does not also answer 200 | advice `crawl.host_consolidation` |
| crawl errors | no discovered URL fails with 4xx/5xx | advice `crawl.errors` (with linked_from) |
| orphans / depth | every page has inbound links; none deeper than 3 clicks | advice `site.orphans`, `site.click_depth` |
| redirected / blocked links | internal links point at final URLs; none blocked | advice `crawl.redirected_links`, `crawl.blocked_links` |
| favicon | a `rel=icon` link exists (gap `no-favicon` absent) | site change `platform.favicon` (`chg_favicon`, low) |
| custom 404 | a custom 404 page exists (gap `no-404` absent); skipped when a soft 404 is measured | site change `platform.custom_404` (`chg_custom_404`) |
| pagination | no reliance on rel=next/prev; paginated pages self-canonicalise | advice `crawl.pagination_rel` (low), `crawl.pagination_canonical` (high) |
| AMP | `rel=amphtml` noted | data `amp` |
