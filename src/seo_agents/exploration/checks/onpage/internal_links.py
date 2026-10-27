"""Links check: internal anchor graph and orphan pages."""

from seo_agents.exploration.checks.tools.crawl import scrape_internal_links
from seo_agents.exploration.checks.tools.helpers import resolve_repo_path
from seo_agents.exploration.checks.tools.llm import summarize


def analyze_internal_links(repo_path=None):
    repo = resolve_repo_path(repo_path)
    data = scrape_internal_links.func(str(repo))
    stats = {
        "edge_count": data["edge_count"],
        "unique_edges": data["unique_edges"],
        "generic_anchor_count": data["generic_anchor_count"],
        "orphan_route_count": len(data["orphan_routes"]),
    }
    report = {
        "check": "internal_links",
        "repo": str(repo),
        "stats": stats,
        "ai": summarize("internal_links", stats),
    }
    for key, value in data.items():
        report[key] = value
    return report
