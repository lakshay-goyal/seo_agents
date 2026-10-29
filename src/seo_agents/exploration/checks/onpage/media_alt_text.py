"""Extract the media inventory for JSON-based downstream analysis."""

from seo_agents.exploration.checks.agents.extractor import extract_category


def analyze_media_alt_text(repo_path=None):
    return extract_category('media', repo_path)
