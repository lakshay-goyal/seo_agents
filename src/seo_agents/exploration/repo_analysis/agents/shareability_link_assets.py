"""Social cards, share buttons, feeds and linkable assets."""

from seo_agents.exploration.repo_analysis.tools.skill_runner import run_skill


def analyze_shareability_link_assets(index):
    return run_skill(index, 'shareability-link-assets')
