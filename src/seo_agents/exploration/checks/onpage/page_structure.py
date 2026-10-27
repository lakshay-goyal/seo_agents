"""Extract the page_structure inventory for JSON-based downstream analysis."""

from seo_agents.exploration.checks.tools.crawl import scrape_headings


def analyze_page_structure(repo_path=None):
    return scrape_headings.func(str(repo_path) if repo_path else "")
