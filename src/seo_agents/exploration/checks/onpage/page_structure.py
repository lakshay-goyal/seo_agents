"""Extract the page_structure inventory for JSON-based downstream analysis."""

from seo_agents.exploration.checks.agents.extractor import extract_category


def analyze_page_structure(repo_path=None):
    return extract_category('page_structure', repo_path)
