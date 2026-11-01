"""Repository profile: manifests, dependencies, languages, entry points, hosting config."""

from seo_agents.exploration.repo_analysis.tools.project import analyze_project
from seo_agents.exploration.repo_analysis.tools.skill_runner import run_skill


def explore_code(index):
    profile = analyze_project(index)
    profile['skill'] = run_skill(index, 'code-exploration')
    return profile
