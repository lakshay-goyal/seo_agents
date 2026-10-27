"""Extract the eeat inventory for JSON-based downstream analysis."""

from seo_agents.exploration.checks.tools.crawl import scrape_eeat


def analyze_eeat_trust_signals(repo_path=None):
    return scrape_eeat.func(str(repo_path) if repo_path else "")
