from seo_agents.exploration.checks.onpage.content_arrangement import analyze_content_arrangement
from seo_agents.exploration.checks.onpage.page_structure import analyze_page_structure
from seo_agents.exploration.checks.onpage.internal_links import analyze_internal_links
from seo_agents.exploration.checks.onpage.metadata_social_cards import analyze_metadata_social_cards
from seo_agents.exploration.checks.onpage.media_alt_text import analyze_media_alt_text
from seo_agents.exploration.checks.onpage.url_information_architecture import analyze_url_information_architecture
from seo_agents.exploration.checks.onpage.eeat_trust_signals import analyze_eeat_trust_signals
from seo_agents.exploration.checks.technical.code_exploration import explore_code
from seo_agents.exploration.checks.technical.sitemap_robots import check_sitemap_and_robots
from seo_agents.exploration.checks.technical.canonicals_redirects import analyze_canonicals_redirects
from seo_agents.exploration.checks.technical.rendering_strategy import analyze_rendering_strategy
from seo_agents.exploration.checks.technical.react_diagnostics import diagnose_react
from seo_agents.exploration.checks.technical.state_management import analyze_state_management
from seo_agents.exploration.checks.technical.page_latency import measure_page_latency
from seo_agents.exploration.checks.technical.performance_assets import analyze_performance_assets
from seo_agents.exploration.checks.technical.https_security_headers import analyze_https_security_headers
from seo_agents.exploration.checks.technical.structured_data import analyze_structured_data
from seo_agents.exploration.checks.technical.mobile_parity import analyze_mobile_parity
from seo_agents.exploration.checks.technical.hreflang_i18n import analyze_hreflang_i18n
from seo_agents.exploration.checks.offpage.shareability_link_assets import analyze_shareability_link_assets
from seo_agents.exploration.checks.offpage.brand_entity_signals import analyze_brand_entity_signals
from seo_agents.exploration.checks.offpage.local_business_presence import analyze_local_business_presence
from seo_agents.exploration.checks.offpage.ai_search_readiness import analyze_ai_search_readiness


def analyze_onpage_seo() -> None:
    """
    Run on-page SEO exploration (what is on the page for keywords/intent/CTR).

    Covers 2026 best practices: search-intent match, unique titles/meta,
    H1/H2 outline, readable slugs, descriptive anchors, image alt, E-E-A-T.
    Exploration logic is static-only here: scan route/page components, Head/
    metadata exports, heading hierarchy, link anchors, and media props to map
    what a later SEO generator can rewrite vs. what needs templates.
    """
    # Logic: map content layout, intent match, and scannability per route template.
    analyze_content_arrangement()
    # Logic: map H1/H2/H3 hierarchy, sections, and semantic HTML per page template.
    analyze_page_structure()
    # Logic: map internal anchor graph, anchor text descriptiveness, and orphan/deep pages.
    analyze_internal_links()
    # Logic: scan Head/metadata exports and layouts for unique titles, meta descriptions, canonical, robots meta, OG/Twitter cards.
    analyze_metadata_social_cards()
    # Logic: scan <img>/<Image>/video/picture sources for alt text, filenames, sizes, lazy-load, modern formats, transcripts.
    analyze_media_alt_text()
    # Logic: scan file-based routes and link graph for short hyphenated slugs, hub/subfolder grouping, depth, breadcrumbs.
    analyze_url_information_architecture()
    # Logic: scan for author bylines, About/Contact/Privacy, testimonials, thin/duplicate boilerplate, TOC/FAQ blocks.
    analyze_eeat_trust_signals()


def analyze_technical_seo() -> None:
    """
    Run technical SEO exploration (can crawlers render, index, and load it fast).

    Covers 2026 best practices: crawlability/indexability, sitemap/robots,
    canonicals/redirects/status codes, HTTPS/security headers, rendering
    (SSR/SSG/ISR/CSR + hydration), Core Web Vitals (LCP <2.5s, INP <200ms,
    CLS <0.1), mobile parity, structured data validity, hreflang/i18n.
    Exploration logic is static-only here: scan framework config, routing,
    middleware, headers, bundles, and data-fetching to map crawl/render/
    performance risks for the later SEO fixer.
    """
    # Logic: map repo layout, framework, entry points, routing mode, and config surfaces for all later checks.
    explore_code()
    # Logic: check public/app sitemap generation, robots.txt rules, and disallow/block risks for CSS/JS/pages.
    check_sitemap_and_robots()
    # Logic: scan route config/middleware/Head for self-referencing canonicals, http/https/www/trailing-slash normalization, redirect chains, 404/soft-404 handling.
    analyze_canonicals_redirects()
    # Logic: detect SSR/SSG/ISR/CSR per route, hydration boundaries, and JS-dependent content that blocks crawlers.
    analyze_rendering_strategy()
    # Logic: extend rendering check for React/Next specifics: app vs pages router, metadata API, client components, state-driven content.
    diagnose_react()
    # Logic: map global/client stores and fetch patterns that delay or hide indexable content.
    analyze_state_management()
    # Logic: baseline latency surfaces: data-fetching waterfalls, blocking scripts, TTFB risks.
    measure_page_latency()
    # Logic: scan bundles, images, fonts, third-party tags for LCP/INP/CLS causes: oversized heroes, render-blocking JS, layout-shift CSS.
    analyze_performance_assets()
    # Logic: scan hosting/framework config for HTTPS redirects, HSTS, CSP, mixed-content and unsafe headers.
    analyze_https_security_headers()
    # Logic: scan JSON-LD/schema templates for Article/Product/Breadcrumb/FAQ/Organization/LocalBusiness coverage and visible-content match.
    analyze_structured_data()
    # Logic: scan layouts/CSS for responsive viewport, same primary content on mobile, tap targets, intrusive interstitials.
    analyze_mobile_parity()
    # Logic: scan locale routing, hreflang alternates, canonical per locale, and localized sitemap readiness.
    analyze_hreflang_i18n()


def analyze_offpage_seo() -> None:
    """
    Run off-page SEO exploration (can the repo earn/share authority outside itself).

    Covers 2026 best practices: quality-over-quantity backlinks, relevance,
    digital PR, brand/entity mentions, local visibility, social shareability.
    Note: a repo sandbox cannot crawl the live backlink graph, so exploration
    logic here is readiness-only: scan for linkable assets, share plumbing,
    entity/Organization data, and syndication hooks the later SEO agent can exploit.
    """
    # Logic: inventory link-worthy surfaces: blog/docs/tools/calculators/data/changelog/templates plus share buttons, embeds, RSS/Atom, UTM hooks.
    analyze_shareability_link_assets()
    # Logic: scan Organization/Person schema, sameAs social links, About/Contact/authors, contributors for entity-safe brand footprint.
    analyze_brand_entity_signals()
    # Logic: scan for NAP blocks, LocalBusiness schema, location pages, maps/reviews hooks for local-pack readiness.
    analyze_local_business_presence()
    # Logic: scan for llms.txt, /llms-full.txt, Markdown endpoints, FAQ/Q&A blocks, transcripts for AEO/GEO citation by AI assistants.
    analyze_ai_search_readiness()


def repo_analysis(github_url: str) -> None:
    """
    Perform analysis on a GitHub repository.

    This function creates a sandbox environment for the given GitHub repository,
    clones the repository inside the sandbox, and then runs analysis scripts
    to extract relevant information.

    Orchestration logic: sandbox/clone once (outer harness concern), then run
    the three SEO pillars in order — on-page (content/intent), technical
    (crawl/render/speed), off-page (earn/share authority) — so context builds
    from page templates to infra to external readiness for the later SEO generator.

    Args:
        github_url (str): The URL of the GitHub repository.
    """
    print(f"Starting analysis for repository: {github_url}")
    analyze_onpage_seo()
    analyze_technical_seo()
    analyze_offpage_seo()
