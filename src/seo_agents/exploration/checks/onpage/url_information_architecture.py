"""Extract the urls inventory for JSON-based downstream analysis."""

from seo_agents.exploration.checks.tools.crawl import scrape_urls


def analyze_url_information_architecture(repo_path=None):
    return scrape_urls.func(str(repo_path) if repo_path else "")
