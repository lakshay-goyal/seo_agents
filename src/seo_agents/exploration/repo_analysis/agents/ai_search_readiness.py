"""llms.txt, AI crawler policy, FAQ/Q&A structure and extractable content (GEO/AEO)."""

from seo_agents.exploration.repo_analysis.tools.skill_runner import run_skill


def analyze_ai_search_readiness(index):
    return run_skill(index, 'ai-search-readiness')
