"""Content check: how much readable text each page has."""

from seo_agents.exploration.checks.tools.crawl import scrape_content_blocks
from seo_agents.exploration.checks.tools.helpers import resolve_repo_path
from seo_agents.exploration.checks.tools.llm import summarize


def analyze_content_arrangement(repo_path=None):
    repo = resolve_repo_path(repo_path)
    data = scrape_content_blocks.func(str(repo))
    thin = 0
    duplicates = 0
    total_words = 0
    for page in data["pages"]:
        total_words = total_words + page["word_count"]
        if page["is_thin"]:
            thin = thin + 1
        if page["is_duplicate_boilerplate"]:
            duplicates = duplicates + 1
    page_count = len(data["pages"])
    if page_count:
        avg_words = round(total_words / page_count)
    else:
        avg_words = 0
    stats = {
        "page_count": page_count,
        "thin_page_count": thin,
        "duplicate_boilerplate_count": duplicates,
        "avg_word_count": avg_words,
    }
    return {
        "check": "content_arrangement",
        "repo": str(repo),
        "stats": stats,
        "pages": data["pages"],
        "ai": summarize("content_arrangement", stats),
    }
