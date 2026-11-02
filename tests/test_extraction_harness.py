"""P0 harness behaviors: grounding, routes+index inputs, canonical repeatability, draft REPL tools, fresh repairs."""

import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from seo_agents.exploration.repo_analysis.agents.llm.extractor import ExtractionPlan, run_exploration_agent, system_prompt
from seo_agents.exploration.repo_analysis.agents.llm.verification import (ObservationValidationError, canonical, compact_index,
                                                                          execute_script, validate_observations, verify_and_extract)
from seo_agents.exploration.repo_analysis.tools.agent_retrieval import repository_tools
from seo_agents.exploration.repo_analysis.tools.element_index import build_element_index
from seo_agents.exploration.repo_analysis.tools.repository_snapshot import snapshot_repository

try:
    from test_exploration_agents import ToolCallingModel, generated_script
except ImportError:  # pragma: no cover - run as a package module
    from tests.test_exploration_agents import ToolCallingModel, generated_script

INDEX_SCRIPT = '''def extract(files, assets, routes, index):
    pages = []
    for route in routes:
        headings = []
        for element in index['elements_by_file'].get(route['template'], []):
            if element.get('heading_level'):
                text = element.get('text_literal') or ''
                record = {'file': route['template'], 'line': element['line'], 'level': element['heading_level'],
                          'text': text, 'source_id': route['template'] + ':' + str(element['line']) + ':' + str(element['col']) + ':' + element['tag']}
                if not text:
                    record['value_origin'] = 'expression'
                    record['origin_ref'] = element['id']
                headings.append(record)
        pages.append({'url': route['url'], 'template': route['template'], 'is_dynamic': False, 'headings': headings,
                      'landmarks': [], 'coverage': {'mode': 'static-source', 'complete': False,
                                                    'limitations': ['Runtime values unresolved.']}})
    return {'schema_version': '2.0', 'category': 'page_structure', 'extraction': {}, 'pages': pages}
'''


@patch.dict(os.environ, {'SEO_NO_LLM': '0'})
class ExtractionHarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        (self.repo / 'index.html').write_text('<title>Example</title>\n<main><h1>Observed heading</h1><p>Full copy.</p><a href="/about">About</a><img src="/cover.png" alt="Cover"></main>')
        (self.repo / 'sections').mkdir()
        (self.repo / 'sections' / 'main-product.liquid').write_text('<section>\n<h1>{{ product.title }}</h1>\n<h2>Details</h2>\n</section>')
        (self.repo / 'data.json').write_text('{"hero": {"title": "Fresh tea daily"}}')
        self.snapshot = snapshot_repository(self.repo)

    # ---- grounding -------------------------------------------------------
    def test_wrong_cited_line_is_rejected_with_bounded_feedback(self):
        report = execute_script(generated_script('content'), self.snapshot)
        report['pages'][0]['blocks'][0]['file'] = 'sections/main-product.liquid'
        report['pages'][0]['blocks'][0]['line'] = 4
        with self.assertRaises(ObservationValidationError) as caught:
            validate_observations('content', report, self.snapshot)
        issue = caught.exception.issues[0]
        self.assertEqual(issue['pointer'], '/pages/0/blocks/0/text')
        self.assertEqual((issue['file'], issue['line'], issue['field']), ('sections/main-product.liquid', 4, 'text'))
        self.assertNotIn('Observed heading', str(caught.exception))

    def test_value_origin_with_resolving_reference_is_accepted(self):
        report = execute_script(generated_script('content'), self.snapshot)
        block = report['pages'][0]['blocks'][0]
        block.update(text='Fresh tea daily', value_origin='data', origin_ref={'file': 'data.json', 'pointer': '/hero/title'})
        report['pages'][0]['word_count'] = sum(len(b['text'].split()) for b in report['pages'][0]['blocks'])
        validate_observations('content', report, self.snapshot)
        block['origin_ref'] = {'file': 'data.json', 'pointer': '/hero/missing'}
        with self.assertRaises(ObservationValidationError):
            validate_observations('content', report, self.snapshot)
        index = build_element_index(self.snapshot)
        record = next(d for d in index['data'] if d['pointer'] == '/hero/title')
        block['origin_ref'] = record['id']
        validate_observations('content', report, self.snapshot, index=index)

    def test_markup_text_split_by_inline_tags_is_grounded(self):
        snapshot = dict(self.snapshot, files={'index.html': '<p>Hello <strong>tea</strong> &amp; friends</p>'})
        report = {'schema_version': '2.0', 'category': 'page_structure', 'extraction': {}, 'pages': [{
            'url': '/', 'template': 'index.html', 'is_dynamic': False, 'landmarks': [],
            'headings': [{'file': 'index.html', 'line': 1, 'source_id': 'index.html:1:0:p', 'level': 2, 'text': 'Hello tea & friends'}],
            'coverage': {'mode': 'static-source', 'complete': False, 'limitations': ['x']}}]}
        validate_observations('page_structure', report, snapshot)

    # ---- routes + index inputs ------------------------------------------
    def test_four_argument_scripts_receive_routes_and_the_element_index(self):
        index = build_element_index(self.snapshot)
        routes = [{'url': '/products/[handle]', 'template': 'sections/main-product.liquid', 'is_dynamic': True}]
        report = verify_and_extract('page_structure', INDEX_SCRIPT, self.snapshot, routes=routes, index=index)
        headings = report['pages'][0]['headings']
        self.assertEqual([h['level'] for h in headings], [1, 2])
        self.assertEqual(headings[0]['value_origin'], 'expression')
        self.assertEqual(headings[1]['text'], 'Details')
        self.assertIn('sections/main-product.liquid', compact_index(index)['elements_by_file'])

    def test_legacy_two_argument_scripts_still_run_and_bad_signatures_are_rejected(self):
        self.assertEqual(execute_script('def extract(files, assets):\n    return {"n": len(files)}', self.snapshot)['n'], 3)
        with self.assertRaisesRegex(ValueError, 'routes, index'):
            execute_script('def extract(files, assets, routes):\n    return {}', self.snapshot)

    # ---- canonical repeatability ----------------------------------------
    def test_record_order_differences_are_canonicalised_not_rejected(self):
        first = execute_script(generated_script('content'), self.snapshot)
        second = copy.deepcopy(first)
        second['pages'][0]['blocks'].reverse()
        self.assertEqual(canonical(first), canonical(second))
        with patch('seo_agents.exploration.repo_analysis.agents.llm.verification.execute_script', side_effect=[first, second]):
            result = verify_and_extract('content', generated_script('content'), self.snapshot)
        self.assertIn('record_order', result['extraction'])
        lines = [(b['line'], b['source_id']) for b in result['pages'][0]['blocks']]
        self.assertEqual(lines, sorted(lines))

    # ---- tools -----------------------------------------------------------
    def test_compact_tool_surface(self):
        tools = {t.name: t for t in repository_tools(self.snapshot, routes=[{'url': '/', 'template': 'index.html'}])}
        read = tools['read'].invoke({'path': 'sections/main-product.liquid', 'start_line': 2, 'end_line': 3})
        self.assertEqual(read['text'], '2| <h1>{{ product.title }}</h1>\n3| <h2>Details</h2>')
        found = tools['grep'].invoke({'pattern': r'<h\d>', 'regex': True, 'glob': '*.liquid'})
        self.assertEqual([(m['file'], m['line']) for m in found['matches']],
                         [('sections/main-product.liquid', 2), ('sections/main-product.liquid', 3)])
        self.assertNotIn('recovery', found['matches'][0])
        headings = tools['query_index'].invoke({'kind': 'element', 'tag': 'h1'})
        self.assertEqual({r['file'] for r in headings['records']}, {'index.html', 'sections/main-product.liquid'})
        page = tools['query_index'].invoke({'kind': 'element', 'page': '/', 'tag': 'h1'})
        self.assertEqual({r['file'] for r in page['records']}, {'index.html'})
        data = tools['query_index'].invoke({'kind': 'data'})
        self.assertEqual(data['records'][0]['value'], 'Fresh tea daily')
        self.assertIsNone(tools['resolve_import'].invoke({'from_file': 'index.html', 'specifier': 'react'})['resolved'])
        self.assertEqual(tools['resolve_import'].invoke({'from_file': 'index.html', 'specifier': './data.json'})['resolved'], 'data.json')

    def test_draft_tools_edit_and_test_without_spending_an_attempt(self):
        draft = {'extractor.py': ''}
        tester = Mock(return_value={'status': 'passed', 'pages': 1})
        tools = {t.name: t for t in repository_tools(self.snapshot, draft=draft, tester=tester)}
        self.assertIn('error', tools['write_file'].invoke({'path': '/etc/passwd', 'content': 'x'}))
        tools['write_file'].invoke({'path': 'extractor.py', 'content': 'def extract(files, assets):\n    return 1\n'})
        self.assertIn('error', tools['edit_file'].invoke({'path': 'extractor.py', 'old': 'missing', 'new': 'x'}))
        tools['edit_file'].invoke({'path': 'extractor.py', 'old': 'return 1', 'new': 'return 2'})
        self.assertIn('return 2', draft['extractor.py'])
        self.assertEqual(tools['test_script'].invoke({})['status'], 'passed')
        tester.assert_called_once_with(draft['extractor.py'])

    def test_agent_can_submit_plan_without_source_using_the_tested_draft(self):
        script = generated_script('content')

        class DraftModel(ToolCallingModel):
            _test_result: str = PrivateAttr(default='')

            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                self._calls += 1
                steps = [('read', {'path': 'index.html'}), ('write_file', {'path': 'extractor.py', 'content': script}),
                         ('test_script', {}), ('ExtractionPlan', {'framework': 'html', 'notes': 'draft'})]
                if self._calls == 4:
                    self._test_result = next(m.content for m in reversed(messages) if isinstance(m, ToolMessage) and m.name == 'test_script')
                name, args = steps[min(self._calls, 4) - 1]
                message = AIMessage(content='', tool_calls=[{'name': name, 'args': args, 'id': 'step-' + str(self._calls)}])
                return ChatResult(generations=[ChatGeneration(message=message)])

        model = DraftModel(source='')
        result = run_exploration_agent('content', snapshot=self.snapshot, model=model)
        self.assertIn('passed', model._test_result)
        self.assertTrue(result['run']['script_from_draft'])
        self.assertEqual(result['run']['script'], script)
        self.assertEqual(result['run']['attempts'], 1)
        self.assertEqual(result['run']['metrics']['test_script_passes'], 1)

    # ---- repair ----------------------------------------------------------
    def test_repairs_start_fresh_compact_conversations_with_the_draft_and_errors(self):
        valid = generated_script('content')
        invalid = valid.replace("section=node['section']", 'section=None')
        agent = Mock()
        agent.invoke.side_effect = [{'structured_response': ExtractionPlan('html', 'bad', invalid)},
                                    {'structured_response': ExtractionPlan('html', 'fixed', valid)}]

        def factory(**kwargs):
            next(t for t in kwargs['tools'] if t.name == 'read').invoke({'path': 'index.html'})
            return agent
        result = run_exploration_agent('content', snapshot=self.snapshot, model=Mock(), agent_factory=factory)
        self.assertEqual(result['run']['attempts'], 2)
        repair = agent.invoke.call_args_list[1].args[0]['messages']
        self.assertEqual(len(repair), 1)
        content = repair[0]['content']
        for expected in ('Verification failed', '/pages/0/blocks/0/section', 'extractor.py', 'index.html', 'section=None'):
            self.assertIn(expected, content)
        budget = agent.invoke.call_args_list[1].kwargs['config']['recursion_limit']
        self.assertLess(budget, agent.invoke.call_args_list[0].kwargs['config']['recursion_limit'])

    def test_prompt_is_static_and_budgets_live_in_the_user_message(self):
        prompt, _, _ = system_prompt('content')
        self.assertNotIn('discovery model rounds', prompt)
        self.assertTrue(prompt.index('Extraction contract') < prompt.index('explore-seo-content'))
        self.assertEqual(prompt, system_prompt('content')[0])
        captured = {}

        def factory(**kwargs):
            captured.update(kwargs)
            from langchain.agents import create_agent
            return create_agent(**kwargs)
        model = ToolCallingModel(source=generated_script('content'))
        with patch('seo_agents.exploration.repo_analysis.agents.llm.extractor.create_agent', side_effect=factory):
            run_exploration_agent('content', snapshot=self.snapshot, model=model, max_discovery_rounds=5)
        self.assertEqual(captured['system_prompt'], prompt)


if __name__ == '__main__':
    unittest.main()
