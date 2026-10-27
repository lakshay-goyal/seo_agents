"""Trust check: authorship, policy pages, reviews, FAQ blocks."""

from seo_agents.exploration.checks.tools.crawl import scrape_eeat
from seo_agents.exploration.checks.tools.helpers import resolve_repo_path
from seo_agents.exploration.checks.tools.llm import summarize


def analyze_eeat_trust_signals(repo_path=None):
    repo = resolve_repo_path(repo_path)
    data = scrape_eeat.func(str(repo))
    report = {
        "check": "eeat_trust_signals",
        "repo": str(repo),
        "stats": data["signals"],
        "ai": summarize("eeat_trust_signals", data["signals"]),
    }
    for key, value in data.items():
        report[key] = value
    return report
