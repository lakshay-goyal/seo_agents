"""lib/tool_mapping.py and lib/models.py must describe the code exactly (no stale or missing entries)."""

import inspect
import unittest

from seo_agents.lib.models import AGENT_MODELS
from seo_agents.lib.prompts import GAP_WORKER_PROMPTS_DIR, ONPAGE_PROMPTS_DIR
from seo_agents.lib.tool_mapping import (AGENT_TOOLS, LEGACY_EXTRACTOR_DRAFT_TOOLS, LEGACY_EXTRACTOR_HANDOFF_TOOLS,
                                         LEGACY_EXTRACTOR_TOOLS, TOOL_CLASSES, tool_class)
from seo_agents.onpage.checks.checklist import CHECKS
from seo_agents.repo_analysis.agents.gap_workers.gap_task_prompts import TASK_SKILL
from seo_agents.repo_analysis.agents.legacy_llm_extractors.repository_retrieval_tools import repository_tools
from seo_agents.repo_analysis.repo_analysis import DETERMINISTIC_AGENTS
from seo_agents.repo_analysis.site_model.site_model_workflow import STAGES


class ToolMappingTests(unittest.TestCase):
    def test_every_tool_resolves_to_a_class(self):
        for name in TOOL_CLASSES:
            self.assertTrue(inspect.isclass(tool_class(name)), name)

    def test_every_mapped_tool_is_registered(self):
        for agent, tools in AGENT_TOOLS.items():
            for tool in tools:
                self.assertIn(tool, TOOL_CLASSES, agent + ' -> ' + tool)

    def test_every_agent_check_and_stage_has_a_mapping(self):
        for agent in DETERMINISTIC_AGENTS:
            self.assertIn(agent.name, AGENT_TOOLS, agent)
        for check in CHECKS:
            self.assertIn('onpage.' + check.name, AGENT_TOOLS, check)
        for stage in STAGES:
            self.assertIn('site_model.' + stage.name, AGENT_TOOLS, stage)
        for skill in TASK_SKILL.values():
            self.assertIn('gap_worker.' + skill, AGENT_TOOLS, skill)

    def test_agents_only_read_results_of_earlier_agents(self):
        seen = set()
        for agent in DETERMINISTIC_AGENTS:
            self.assertLessEqual(set(agent.requires), seen, agent)
            seen.add(agent.name)

    def test_legacy_extractor_tools_match_mapping(self):
        snapshot = {'files': {'a.html': '<h1>x</h1>'}, 'assets': {}, 'sha256': 's'}
        full = [t.name for t in repository_tools(snapshot, set(), draft={'extractor.py': ''}, handoff={'urls': {}})]
        self.assertEqual(full, list(LEGACY_EXTRACTOR_TOOLS))
        plain = [t.name for t in repository_tools(snapshot, set())]
        self.assertEqual(plain, [n for n in LEGACY_EXTRACTOR_TOOLS
                                 if n not in LEGACY_EXTRACTOR_DRAFT_TOOLS + LEGACY_EXTRACTOR_HANDOFF_TOOLS])


class ModelSettingsTests(unittest.TestCase):
    def test_every_model_entry_has_a_prompt(self):
        for key in AGENT_MODELS:
            stage, _, skill = key.partition('.')
            if stage == 'gap_worker':
                self.assertTrue((GAP_WORKER_PROMPTS_DIR / skill / 'SKILL.md').exists(), key)
            elif stage == 'onpage':
                self.assertTrue((ONPAGE_PROMPTS_DIR / skill / 'SKILL.md').exists(), key)

    def test_model_checks_and_gap_tasks_have_model_settings(self):
        for check in CHECKS:
            self.assertEqual(('onpage.' + check.skill) in AGENT_MODELS, check.uses_model, check)
        for skill in TASK_SKILL.values():
            self.assertIn('gap_worker.' + skill, AGENT_MODELS)

    def test_prompt_frontmatter_holds_no_model_settings(self):
        for directory in (GAP_WORKER_PROMPTS_DIR, ONPAGE_PROMPTS_DIR):
            for path in directory.glob('*/SKILL.md'):
                front = path.read_text().split('---')[1]
                for key in ('tier:', 'ladder:', 'votes:', 'temperature:'):
                    self.assertNotIn('\n' + key, front, path)


if __name__ == '__main__':
    unittest.main()
