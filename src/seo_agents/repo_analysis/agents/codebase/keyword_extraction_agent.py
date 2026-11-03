"""Keyword candidates (n-grams with file:line sources) from the page inventory and routes."""

from seo_agents.repo_analysis.agents.base_agent import RepoAnalysisAgent
from seo_agents.repo_analysis.tools.content.keyword_extraction import KeywordExtractionTool


class KeywordExtractionAgent(RepoAnalysisAgent):
    """Agent: Keyword candidates (n-grams with file:line sources) from the page inventory and routes."""

    name = 'keywords'
    description = 'Keyword candidates (n-grams with file:line sources) from the page inventory and routes.'
    signal_profile = 'keywords'
    requires = ('page_inventory', 'routes')

    def run(self, index, results):
        return KeywordExtractionTool().extract(results['page_inventory'], results['routes'])
