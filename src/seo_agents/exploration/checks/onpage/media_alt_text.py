"""Extract the media inventory for JSON-based downstream analysis."""

from seo_agents.exploration.checks.tools.crawl import scrape_media


def analyze_media_alt_text(repo_path=None):
    return scrape_media.func(str(repo_path) if repo_path else "")
