"""Extract the internal_links inventory for JSON-based downstream analysis."""

from seo_agents.exploration.checks.tools.crawl import scrape_internal_links


def analyze_internal_links(repo_path=None):
    return scrape_internal_links.func(str(repo_path) if repo_path else "")
