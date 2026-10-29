"""Extract the eeat inventory for JSON-based downstream analysis."""

from seo_agents.exploration.checks.agents.extractor import extract_category


def analyze_eeat_trust_signals(repo_path=None):
    return extract_category('eeat', repo_path)
