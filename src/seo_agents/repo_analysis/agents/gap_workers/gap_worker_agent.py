"""Gap workers (stage F): cheap-model agents that resolve only what deterministic code could not.

Each attribution gap becomes a self-contained task packet (target <= ~6k tokens) that needs no tools. The model
answers with a constrained JSON object (candidate IDs and enums, never coordinates or edit kinds); code validates
it and derives kind/file/line/col from the chosen candidate.

    gap_task_builder.py       gaps -> task packets
    gap_task_prompts.py       task -> SKILL.md prompt (lib/prompts/gap_workers)
    gap_task_solver.py        model calls: validation ladder, votes, backoff, auth fail-fast, result cache
    gap_answer_validation.py  JSON response schemas + validators
    gap_result_applier.py     resolved answer -> page model edit
    route_adapter_learner.py  URL rules for unrecognised frameworks (route-adapter task)

Model tiers per task: lib/models.py (AGENT_MODELS['gap_worker.<skill>']).
"""

from seo_agents.lib.models import agent_model
from seo_agents.lib.tool_mapping import tools_for
from seo_agents.repo_analysis.agents.gap_workers import gap_result_applier, gap_task_builder, gap_task_solver
from seo_agents.repo_analysis.agents.gap_workers import route_adapter_learner
from seo_agents.repo_analysis.agents.gap_workers.gap_task_prompts import TASK_SKILL


class GapWorkerAgent:
    """Agent (LLM, small tier first): build gap tasks, solve each one with a validated model answer, apply it."""

    TASKS = tuple(TASK_SKILL)

    @staticmethod
    def model_settings(task):
        """lib/models.py settings for a task type such as 'attribute_value'."""
        return agent_model('gap_worker.' + TASK_SKILL[task])

    @staticmethod
    def tools(task):
        return tools_for('gap_worker.' + TASK_SKILL[task])

    def build_tasks(self, gaps, pages, ctx, fingerprint):
        return gap_task_builder.build_tasks(gaps, pages, ctx, fingerprint)

    def solve(self, task, family=None, *, model_factory=None):
        return gap_task_solver.solve(task, family, model_factory=model_factory)

    def apply(self, task, result, page, ctx):
        return gap_result_applier.apply(task, result, page, ctx)

    def learn_route_adapter(self, snapshot, fingerprint, *, model_factory=None):
        return route_adapter_learner.learn_adapter(snapshot, fingerprint, model_factory=model_factory)

    def apply_learned_rules(self, files, rules):
        return route_adapter_learner.apply_learned_rules(files, rules)
