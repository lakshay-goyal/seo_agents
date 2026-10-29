"""Extract the internal_links inventory for JSON-based downstream analysis."""

from seo_agents.exploration.checks.agents.extractor import extract_category


def analyze_internal_links(repo_path=None):
    return extract_category('internal_links', repo_path)
