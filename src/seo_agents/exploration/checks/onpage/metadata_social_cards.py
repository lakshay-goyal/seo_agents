"""Extract the metadata inventory for JSON-based downstream analysis."""

from seo_agents.exploration.checks.tools.crawl import scrape_metadata


def analyze_metadata_social_cards(repo_path=None):
    return scrape_metadata.func(str(repo_path) if repo_path else "")
