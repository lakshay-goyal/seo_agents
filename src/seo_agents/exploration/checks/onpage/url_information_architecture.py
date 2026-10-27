"""URLs check: slug shape, depth, dynamic segments, breadcrumbs."""

from seo_agents.exploration.checks.tools.crawl import scrape_urls
from seo_agents.exploration.checks.tools.helpers import resolve_repo_path
from seo_agents.exploration.checks.tools.llm import summarize


def analyze_url_information_architecture(repo_path=None):
    repo = resolve_repo_path(repo_path)
    data = scrape_urls.func(str(repo))
    bad_slugs = 0
    dynamic = 0
    for entry in data["urls"]:
        if entry["has_underscore"] or entry["has_uppercase"] or entry["overlong_slug"]:
            bad_slugs = bad_slugs + 1
        if entry["is_dynamic"]:
            dynamic = dynamic + 1
    stats = {
        "url_count": data["url_count"],
        "deep_url_count": data["deep_url_count"],
        "dynamic_count": dynamic,
        "bad_slug_count": bad_slugs,
        "filter_link_count": len(data["filter_query_links"]),
        "files_with_breadcrumbs": data["files_with_breadcrumbs"],
    }
    report = {
        "check": "url_information_architecture",
        "repo": str(repo),
        "stats": stats,
        "ai": summarize("url_information_architecture", stats),
    }
    for key, value in data.items():
        report[key] = value
    return report
