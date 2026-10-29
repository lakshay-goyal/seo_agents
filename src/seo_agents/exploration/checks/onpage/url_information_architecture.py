"""Extract the urls inventory for JSON-based downstream analysis."""

from seo_agents.exploration.checks.agents.extractor import extract_category


def analyze_url_information_architecture(repo_path=None):
    return extract_category('urls', repo_path)
