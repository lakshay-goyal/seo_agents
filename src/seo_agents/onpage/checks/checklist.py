"""Registry of the on-page checks, in execution order (one LangGraph node each, see onpage_analysis.py).

Four checks call a model (intent, page_plan, media_links, advice); the rest are deterministic. New
checks must sit before QaConsolidationCheck, which consolidates every earlier check generically. A check whose
`depends_on` checks did not finish for a page is skipped for that page; the others still run.
"""

from __future__ import annotations

from seo_agents.onpage.checks.accessibility_check import LinkAccessibilityCheck
from seo_agents.onpage.checks.content_quality_check import ContentQualityCheck
from seo_agents.onpage.checks.crawlability_check import CrawlabilityCheck
from seo_agents.onpage.checks.eeat_advice_check import EeatAdviceCheck
from seo_agents.onpage.checks.fact_check_freshness_check import FactCheckFreshnessCheck
from seo_agents.onpage.checks.head_hygiene_check import HeadHygieneCheck
from seo_agents.onpage.checks.heading_structure_check import HeadingStructureCheck
from seo_agents.onpage.checks.hreflang_i18n_check import HreflangI18nCheck
from seo_agents.onpage.checks.indexability_check import IndexabilityCheck
from seo_agents.onpage.checks.keyword_plan_check import KeywordPlanCheck
from seo_agents.onpage.checks.link_hygiene_check import LinkHygieneCheck
from seo_agents.onpage.checks.media_links_check import MediaLinksCheck
from seo_agents.onpage.checks.media_technical_check import MediaTechnicalCheck
from seo_agents.onpage.checks.page_plan_check import PagePlanCheck
from seo_agents.onpage.checks.performance_check import PerformanceCheck
from seo_agents.onpage.checks.qa_consolidation_check import QaConsolidationCheck
from seo_agents.onpage.checks.search_intent_check import SearchIntentCheck
from seo_agents.onpage.checks.security_check import SecurityCheck
from seo_agents.onpage.checks.social_meta_check import SocialMetaCheck
from seo_agents.onpage.checks.structured_data_check import StructuredDataCheck
from seo_agents.onpage.checks.technical_check import TechnicalOnpageCheck

CHECKS = (
    SearchIntentCheck(),          # 1  model (small)
    KeywordPlanCheck(),           # 2
    PagePlanCheck(),              # 3  model (mid)
    MediaLinksCheck(),            # 4  model (small)
    EeatAdviceCheck(),            # 5  model (small, optional: SEO_ONPAGE_ADVICE=0)
    MediaTechnicalCheck(),        # 6
    LinkAccessibilityCheck(),     # 7
    TechnicalOnpageCheck(),       # 8
    StructuredDataCheck(),        # 9
    SocialMetaCheck(),            # 10
    FactCheckFreshnessCheck(),    # 11
    HeadHygieneCheck(),           # 12 current title/description/meta hygiene
    HeadingStructureCheck(),      # 13 H1 count/visibility, hierarchy
    ContentQualityCheck(),        # 14 thin / near-duplicate / readability / placeholder copy
    LinkHygieneCheck(),           # 15 rel, redirects, mixed/insecure, orphans, depth
    IndexabilityCheck(),          # 16 robots directives, canonical edge cases, robots.txt, redirect chains
    HreflangI18nCheck(),          # 17 hreflang + html lang validity
    SecurityCheck(),              # 18 HTTPS, HSTS, security headers, mixed content
    PerformanceCheck(),           # 19 Core Web Vitals, Lighthouse opportunities, compression/cache
    CrawlabilityCheck(),          # 20 robots.txt rules, sitemap, host redirects, crawl errors, favicon, 404, pagination
    QaConsolidationCheck(),       # 21 (must stay last: consolidates every check above)
)
DEPENDS = {check.name: list(check.depends_on) for check in CHECKS if check.depends_on}
