"""Structure check: heading order and semantic HTML per template."""

from seo_agents.exploration.checks.tools.crawl import scrape_headings
from seo_agents.exploration.checks.tools.helpers import resolve_repo_path
from seo_agents.exploration.checks.tools.llm import summarize


def analyze_page_structure(repo_path=None):
    repo = resolve_repo_path(repo_path)
    data = scrape_headings.func(str(repo))
    missing_h1 = 0
    multiple_h1 = 0
    bad_order = 0
    missing_main = 0
    for page in data["pages"]:
        if page["h1_missing"]:
            missing_h1 = missing_h1 + 1
        if page["multiple_h1"]:
            multiple_h1 = multiple_h1 + 1
        if not page["heading_order_ok"]:
            bad_order = bad_order + 1
        if not page["has_main"]:
            missing_main = missing_main + 1
    stats = {
        "page_count": data["page_count"],
        "missing_h1": missing_h1,
        "multiple_h1": multiple_h1,
        "bad_order": bad_order,
        "missing_main": missing_main,
    }
    return {
        "check": "page_structure",
        "repo": str(repo),
        "stats": stats,
        "pages": data["pages"],
        "ai": summarize("page_structure", stats),
    }
