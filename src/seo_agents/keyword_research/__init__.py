"""Keyword research stage: reads the exploration JSON (seo_reports/) and writes seo_reports/keywords/
(keyword_research.csv, keyword_map.json, pages/, summary). The on-page stage reads keyword_map.json.

Entry point: `keyword_research.KeywordResearchWorkflow` / `run_keyword_research()`. Contracts: contracts.py.
Imports are lazy so the sub-packages (providers/, generation/, judge/) can import `contracts` without pulling in LangGraph.
"""

__all__ = ['KeywordResearchWorkflow', 'build_graph', 'run_keyword_research']


def __getattr__(name):
    if name in __all__:
        from seo_agents.keyword_research import keyword_research
        return getattr(keyword_research, name)
    raise AttributeError(name)
