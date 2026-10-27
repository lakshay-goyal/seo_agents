"""Three SEO pillars: on-page (content), technical (crawl/speed), off-page (authority)."""

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


def analyze_onpage_seo(repo_path=None):
    """Run the 7 on-page checks through the LangGraph chain. Returns in-memory results."""
    from seo_agents.exploration.checks.graph import run_onpage_graph

    return run_onpage_graph(repo_path=repo_path)


def analyze_technical_seo():
    """Run the technical SEO checks (crawl, render, speed)."""
    explore_code()
    check_sitemap_and_robots()
    analyze_canonicals_redirects()
    analyze_rendering_strategy()
    diagnose_react()
    analyze_state_management()
    measure_page_latency()
    analyze_performance_assets()
    analyze_https_security_headers()
    analyze_structured_data()
    analyze_mobile_parity()
    analyze_hreflang_i18n()


def analyze_offpage_seo():
    """Run the off-page SEO readiness checks (shareability, brand, local, AI search)."""
    analyze_shareability_link_assets()
    analyze_brand_entity_signals()
    analyze_local_business_presence()
    analyze_ai_search_readiness()


def repo_analysis(github_url, repo_path=None):
    """Analyze the client checkout and return the on-page results from memory."""
    print("Starting analysis for repository: " + github_url)
    state = analyze_onpage_seo(repo_path=repo_path)
    artifacts = state.get("artifacts") or {}
    print("On-page reports in memory: " + str(sorted(artifacts)))
    analyze_technical_seo()
    analyze_offpage_seo()
    return state
