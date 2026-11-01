"""Extract the page_structure inventory for JSON-based downstream analysis."""

from seo_agents.exploration.repo_analysis.agents.llm.extractor import extract_category


def analyze_page_structure(repo_path=None):
    return extract_category('page_structure', repo_path)
