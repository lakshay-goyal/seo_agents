"""Extract the content inventory for JSON-based downstream analysis."""

from seo_agents.exploration.checks.agents.extractor import extract_category


def analyze_content_arrangement(repo_path=None):
    return extract_category('content', repo_path)
