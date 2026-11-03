"""Base class for every on-page checklist item (run once per selected page, pages in parallel)."""

from __future__ import annotations

from seo_agents.lib.models import agent_model
from seo_agents.lib.tool_mapping import tools_for


class OnpageCheck:
    """One on-page checklist item.

    name        LangGraph node name and the key of this check's result per page
    skill       lib/prompts/onpage/<skill>/SKILL.md (the prompt for model checks, the documentation otherwise)
    uses_model  True when the check calls the content writer (model settings: lib/models.py 'onpage.<skill>')
    depends_on  checks that must have finished ('done') for the page, else this check is skipped for it
    """

    name = ''
    skill = ''
    uses_model = False
    depends_on = ()

    @property
    def model_settings(self):
        return agent_model('onpage.' + self.skill) if self.uses_model else None

    @property
    def tools(self):
        return tools_for('onpage.' + self.name)

    def run(self, ctx, prior, writer):
        """ctx: page context (exploration JSON only); prior: earlier check results for the page; writer: ContentWriter."""
        raise NotImplementedError

    def __repr__(self):
        return type(self).__name__ + '(' + repr(self.name) + ')'
