"""Every route (URL pattern -> entry template) from file conventions and router configs."""

from seo_agents.repo_analysis.agents.base_agent import RepoAnalysisAgent
from seo_agents.repo_analysis.tools.routing.route_discovery import RouteDiscoveryTool


class RouteDiscoveryAgent(RepoAnalysisAgent):
    """Agent: Every route (URL pattern -> entry template) from file conventions and router configs."""

    name = 'routes'
    description = 'Every route (URL pattern -> entry template) from file conventions and router configs.'
    signal_profile = 'routes'
    requires = ()

    def run(self, index, results):
        return RouteDiscoveryTool().discover(index)
