"""State/data-fetching layers and whether content is prefetched for the initial HTML."""

from seo_agents.exploration.repo_analysis.tools.skill_runner import run_skill


def analyze_state_management(index):
    result = run_skill(index, 'state-management')
    found = set(result['found_signals'])
    result['content_prefetched_on_server'] = 'server-prefetch' in found if found & {'store', 'query-client'} else None
    return result
