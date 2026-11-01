"""Client-rendering hazards (any JS framework): effect fetches, runtime head, hash routing, JS-only navigation."""

from seo_agents.exploration.repo_analysis.tools.skill_runner import run_skill


def diagnose_react(index):
    return run_skill(index, 'client-diagnostics')


diagnose_client_rendering = diagnose_react
