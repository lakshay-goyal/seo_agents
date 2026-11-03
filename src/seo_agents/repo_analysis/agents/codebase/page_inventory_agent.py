"""Per-file tag inventory: title/meta, headings, visible text, links, images and media."""

from seo_agents.repo_analysis.agents.base_agent import RepoAnalysisAgent
from seo_agents.repo_analysis.tools.content.page_inventory import PageInventoryTool


class PageInventoryAgent(RepoAnalysisAgent):
    """Agent: Per-file tag inventory: title/meta, headings, visible text, links, images and media."""

    name = 'page_inventory'
    description = 'Per-file tag inventory: title/meta, headings, visible text, links, images and media.'
    signal_profile = 'content'
    requires = ()

    def run(self, index, results):
        return PageInventoryTool().analyze(index)
