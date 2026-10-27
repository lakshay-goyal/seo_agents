"""Extract the content inventory for JSON-based downstream analysis."""

from seo_agents.exploration.checks.tools.crawl import scrape_content_blocks


def analyze_content_arrangement(repo_path=None):
    return scrape_content_blocks.func(str(repo_path) if repo_path else "")
