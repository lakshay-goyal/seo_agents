"""Metadata check: titles, descriptions, canonical, social cards."""

from seo_agents.exploration.checks.tools.crawl import scrape_metadata
from seo_agents.exploration.checks.tools.helpers import resolve_repo_path
from seo_agents.exploration.checks.tools.llm import summarize


def analyze_metadata_social_cards(repo_path=None):
    repo = resolve_repo_path(repo_path)
    data = scrape_metadata.func(str(repo))
    missing_title = 0
    missing_desc = 0
    missing_canonical = 0
    missing_og = 0
    for page in data["pages"]:
        if not page["title"]:
            missing_title = missing_title + 1
        if not page["has_meta_description"]:
            missing_desc = missing_desc + 1
        if not page["has_canonical"]:
            missing_canonical = missing_canonical + 1
        if page["og_tag_count"] == 0:
            missing_og = missing_og + 1
    stats = {
        "page_count": data["page_count"],
        "missing_title": missing_title,
        "missing_description": missing_desc,
        "missing_canonical": missing_canonical,
        "missing_og": missing_og,
        "duplicate_title_count": data["duplicate_title_count"],
    }
    return {
        "check": "metadata_social_cards",
        "repo": str(repo),
        "stats": stats,
        "duplicate_titles": data["duplicate_titles"],
        "pages": data["pages"],
        "ai": summarize("metadata_social_cards", stats),
    }
