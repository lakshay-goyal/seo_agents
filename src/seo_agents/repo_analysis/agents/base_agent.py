"""Base class shared by every deterministic repo-analysis agent (no model calls)."""

from __future__ import annotations

from seo_agents.lib.tool_mapping import tools_for


class RepoAnalysisAgent:
    """One analysis step over the shared repository snapshot.

    name            key of this agent's result in `explore_codebase()` and in repo_context.json
    description     one line: what the agent finds
    signal_profile  regex profile it applies (lib/signal_profiles/profiles/<signal_profile>.json), if any
    requires        earlier agents whose results `run()` reads (the workflow runs agents in order)
    tools           the tools this agent uses, from lib/tool_mapping.py
    """

    name = ''
    description = ''
    signal_profile = None
    requires = ()

    @property
    def tools(self):
        return tools_for(self.name)

    def run(self, index, results):
        """Analyse `index` (a SourceIndex); `results` holds the outputs of earlier agents by name."""
        raise NotImplementedError

    def __repr__(self):
        return type(self).__name__ + '(' + repr(self.name) + ')'
