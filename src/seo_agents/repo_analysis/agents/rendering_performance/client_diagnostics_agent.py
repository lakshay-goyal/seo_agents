"""Client-rendering hazards (any JS framework): effect fetches, runtime head, hash routing, JS-only navigation."""

from seo_agents.repo_analysis.tools.signal_profiles.signal_profile_runner import run_signal_profile
from seo_agents.repo_analysis.agents.base_agent import RepoAnalysisAgent


def diagnose_client_rendering(index):
    return run_signal_profile(index, 'client-diagnostics')


class ClientDiagnosticsAgent(RepoAnalysisAgent):
    """Agent: Client-rendering hazards (any JS framework): effect fetches, runtime head, hash routing, JS-only navigation."""

    name = 'client_diagnostics'
    description = 'Client-rendering hazards (any JS framework): effect fetches, runtime head, hash routing, JS-only navigation.'
    signal_profile = 'client-diagnostics'
    requires = ()

    def run(self, index, results):
        return diagnose_client_rendering(index)
