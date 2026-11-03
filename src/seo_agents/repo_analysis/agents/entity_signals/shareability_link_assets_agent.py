"""Social cards, share buttons, feeds and linkable assets."""

from seo_agents.repo_analysis.tools.signal_profiles.signal_profile_runner import run_signal_profile
from seo_agents.repo_analysis.agents.base_agent import RepoAnalysisAgent


def analyze_shareability_link_assets(index):
    return run_signal_profile(index, 'shareability-link-assets')


class ShareabilityLinkAssetsAgent(RepoAnalysisAgent):
    """Agent: Social cards, share buttons, feeds and linkable assets."""

    name = 'shareability_link_assets'
    description = 'Social cards, share buttons, feeds and linkable assets.'
    signal_profile = 'shareability-link-assets'
    requires = ()

    def run(self, index, results):
        return analyze_shareability_link_assets(index)
