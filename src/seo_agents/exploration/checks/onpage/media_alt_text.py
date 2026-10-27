"""Media check: image alt text, filenames, loading behaviour."""

from seo_agents.exploration.checks.tools.crawl import scrape_media
from seo_agents.exploration.checks.tools.helpers import resolve_repo_path
from seo_agents.exploration.checks.tools.llm import summarize


def analyze_media_alt_text(repo_path=None):
    repo = resolve_repo_path(repo_path)
    data = scrape_media.func(str(repo))
    empty_alt = 0
    no_loading = 0
    for item in data["items"]:
        if item["alt_empty"]:
            empty_alt = empty_alt + 1
        if item["loading"] == "unspecified":
            no_loading = no_loading + 1
    stats = {
        "media_count": data["media_count"],
        "by_kind": data["by_kind"],
        "missing_alt_count": data["missing_alt_count"],
        "empty_alt_count": empty_alt,
        "unspecified_loading": no_loading,
    }
    return {
        "check": "media_alt_text",
        "repo": str(repo),
        "stats": stats,
        "items": data["items"],
        "ai": summarize("media_alt_text", stats),
    }
