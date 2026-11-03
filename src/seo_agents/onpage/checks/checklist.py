"""Registry of the on-page checks, in execution order (one LangGraph node each, see onpage_analysis.py).

Four checks call a model (intent, page_plan, media_links, advice); the rest are deterministic. A check whose
`depends_on` checks did not finish for a page is skipped for that page; the others still run.
"""

from __future__ import annotations

from seo_agents.onpage.checks.accessibility_check import LinkAccessibilityCheck
from seo_agents.onpage.checks.eeat_advice_check import EeatAdviceCheck
from seo_agents.onpage.checks.fact_check_freshness_check import FactCheckFreshnessCheck
from seo_agents.onpage.checks.keyword_plan_check import KeywordPlanCheck
from seo_agents.onpage.checks.media_links_check import MediaLinksCheck
from seo_agents.onpage.checks.media_technical_check import MediaTechnicalCheck
from seo_agents.onpage.checks.page_plan_check import PagePlanCheck
from seo_agents.onpage.checks.qa_consolidation_check import QaConsolidationCheck
from seo_agents.onpage.checks.search_intent_check import SearchIntentCheck
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
    QaConsolidationCheck(),       # 12
)
DEPENDS = {check.name: list(check.depends_on) for check in CHECKS if check.depends_on}
