"""Extract the metadata inventory for JSON-based downstream analysis."""

from seo_agents.exploration.checks.agents.extractor import extract_category


def analyze_metadata_social_cards(repo_path=None):
    return extract_category('metadata', repo_path)
