"""Exercise generated scripts and the real agent/tool loop without a paid model."""

import copy
from contextlib import redirect_stdout
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from seo_agents.exploration.repo_analysis.agents.llm.extractor import ExtractionFailed, ExtractionPlan, repository_tools, run_exploration_agent
from seo_agents.exploration.repo_analysis.agents.llm.handoff import PRIOR_WORK, build_handoff
from seo_agents.exploration.repo_analysis.tools.repository_snapshot import snapshot_repository
from seo_agents.exploration.repo_analysis.agents.llm.verification import ObservationValidationError, execute_script, validate_observations, verify_and_extract
from seo_agents.exploration.repo_analysis.graph import CHECKS, ExplorationIncomplete, run_onpage_graph, validate_artifact_links
from seo_agents.exploration.repo_analysis.tools.helpers import REPORT_FILES
from seo_agents.sandbox.host import sync_sources


def generated_script(category):
    """A fixture agent's program; values and evidence come from the input each run."""
    return '''def extract(files, assets):
    pages = []
    for file, text in files.items():
        if not file.endswith(('.html', '.vue', '.svelte', '.astro', '.tsx')):
            continue
        row = {'url': '/', 'template': file, 'is_dynamic': False,
               'coverage': {'mode': 'static-source', 'complete': False,
                            'limitations': ['Runtime data and visibility are unverified.']}}
        blocks = []
        headings = []
        links = []
        media = []
        declarations = []
        for node in parse_html(text):
            tag = node['tag']
            attrs = node['attrs']
            ref = {'file': file, 'line': node['line'],
                   'source_id': file + ':' + str(node['line']) + ':' + str(node['column']) + ':' + tag}
            if tag in ['h1', 'p', 'a', 'button'] and node['text']:
                blocks.append(dict(ref, tag=tag, text=node['text'], section=node['section']))
            if tag in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                headings.append(dict(ref, level=int(tag[1]), text=node['text']))
            if tag == 'a' and 'href' in attrs:
                links.append(dict(ref, href=attrs['href'], anchor_text=node['text'],
                                  target_url=resolve_url('/', attrs['href']), kind='internal',
                                  rel=attrs.get('rel'), target=attrs.get('target')))
            if tag == 'img':
                media.append(dict(ref, kind='img', src=attrs.get('src'), size_review='unknown', alt=attrs.get('alt')))
            if tag == 'title':
                declarations.append(dict(ref, title=node['text']))
        category = CATEGORY
        if category == 'content':
            row.update(blocks=blocks, word_count=sum(len(b['text'].split()) for b in blocks),
                       link_refs=[{'source_id': l['source_id'], 'report': 'internal_links.json'} for l in links],
                       media_refs=[{'source_id': m['source_id'], 'report': 'media.json'} for m in media])
        elif category == 'page_structure':
            row.update(headings=headings, landmarks=[])
        elif category == 'internal_links':
            row.update(links=links)
        elif category == 'metadata':
            row.update(declarations=declarations, inheritance_note='No runtime merge evaluated.')
        elif category == 'media':
            row.update(items=media)
        elif category == 'urls':
            row.update(depth=0, query_links=[])
        elif category == 'eeat':
            row.update(signals=[], trust_links=[], citations=[], interpretation='Candidate evidence only.')
        pages.append(row)
    return {'schema_version': '2.0', 'category': CATEGORY,
            'extraction': {'mode': 'static-source'}, 'pages': pages}
'''.replace('CATEGORY', repr(category))


class ToolCallingModel(BaseChatModel):
    """A deterministic provider double that actually calls the inspection tool."""
    source: str
    _calls: int = PrivateAttr(default=0)
    _saw_source: bool = PrivateAttr(default=False)

    @property
    def _llm_type(self):
        return 'fixture-tool-calling-model'

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self._calls += 1
        if self._calls == 1:
            message = AIMessage(content='', tool_calls=[{'name': 'read_source', 'args': {'path': 'index.html'}, 'id': 'inspect'}])
        else:
            self._saw_source = any(isinstance(m, ToolMessage) and 'Observed heading' in m.content for m in messages)
            message = AIMessage(content='', tool_calls=[{'name': 'ExtractionPlan', 'args': {
                'framework': 'html', 'notes': 'Inspected index.html entry.', 'source': self.source}, 'id': 'plan'}])
        return ChatResult(generations=[ChatGeneration(message=message)])


class BrowsingModel(ToolCallingModel):
    """Keeps reading while a read tool is available; submits once it is removed."""
    _can_read: bool = PrivateAttr(default=False)

    def bind_tools(self, tools, **kwargs):
        self._can_read = any((tool.get('function', {}).get('name') if isinstance(tool, dict) else tool.name) == 'read_source' for tool in tools)
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self._calls += 1
        if self._can_read:
            message = AIMessage(content='', tool_calls=[{'name': 'read_source', 'args': {'path': 'index.html'}, 'id': 'inspect-' + str(self._calls)}])
        else:
            message = AIMessage(content='', tool_calls=[{'name': 'ExtractionPlan', 'args': {
                'framework': 'html', 'notes': 'Known evidence; remaining semantics unresolved.', 'source': self.source}, 'id': 'plan'}])
        return ChatResult(generations=[ChatGeneration(message=message)])


@patch.dict(os.environ, {'SEO_NO_LLM': '0'})
class ExplorationAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        (self.repo / 'index.html').write_text('<title>Example</title>\n<main><h1>Observed heading</h1><p>Full copy.</p><a href="/about">About</a><img src="/cover.png" alt="Cover"></main>')
        self.snapshot = snapshot_repository(self.repo)

    def test_real_agent_calls_source_tool_then_returns_verified_plan(self):
        model = ToolCallingModel(source=generated_script('content'))
        result = run_exploration_agent('content', snapshot=self.snapshot, model=model)
        self.assertTrue(model._saw_source)
        self.assertEqual(result['report']['pages'][0]['blocks'][0]['text'], 'Observed heading')
        self.assertEqual(result['run']['verification'], 'passed')
        self.assertEqual(result['report']['extraction']['snapshot_sha256'], self.snapshot['sha256'])

    def test_tool_progress_is_visible_before_the_next_model_call(self):
        terminal = io.StringIO()
        test = self

        class ObservingModel(ToolCallingModel):
            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                if self._calls == 1:
                    output = terminal.getvalue()
                    test.assertIn('completed read_source', output)
                    test.assertIn('index.html', output)
                    test.assertNotIn('verify and extract', output)
                return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

        with redirect_stdout(terminal):
            run_exploration_agent('content', snapshot=self.snapshot, model=ObservingModel(source=generated_script('content')))
        output = terminal.getvalue()
        self.assertIn('DONE agent content: model discovery 1/', output)
        self.assertIn('DONE agent content: verify and extract', output)
        self.assertNotIn('Observed heading', output)  # Source text is not a progress message.

    def test_graph_runs_agents_on_dependency_dag_after_their_prerequisites(self):
        from seo_agents.exploration.repo_analysis.graph import DEPENDENCIES
        import threading
        terminal = io.StringIO()
        called, lock = [], threading.Lock()
        reports = {key: execute_script(generated_script(key), self.snapshot) for key in CHECKS}

        def extract(category, **kwargs):
            with lock:
                for prerequisite in DEPENDENCIES[category]:
                    self.assertIn(prerequisite, called)
                    self.assertIn('report ready: ' + REPORT_FILES[prerequisite], terminal.getvalue())
                called.append(category)
            self.assertEqual(set(kwargs['handoff']), set(PRIOR_WORK[category]))
            for key, entry in kwargs['handoff'].items():
                self.assertIs(entry['report'], reports[key])
            self.assertIn('element_index', kwargs)
            if category == 'urls':
                self.assertEqual(kwargs['route_candidates'][0]['template'], 'index.html')
            if category == 'content':
                self.assertIn('internal_links', kwargs['handoff'])
                self.assertIn('media', kwargs['handoff'])
            return {'report': reports[category], 'run': {'verification': 'passed'}}

        with patch('seo_agents.exploration.repo_analysis.graph.run_exploration_agent', side_effect=extract), redirect_stdout(terminal):
            run_onpage_graph(self.repo, mode='agent')
        self.assertEqual(set(called), set(CHECKS))
        self.assertEqual((called[0], called[-2], called[-1]), ('urls', 'content', 'eeat'))
        output = terminal.getvalue()
        self.assertIn('on-page progress: 7/7', output)
        self.assertIn('agent content: page "/" completed', output)
        self.assertIn('DONE cross-report validation', output)

    def test_graph_reuses_a_precomputed_snapshot_instead_of_capturing_again(self):
        reports = {key: execute_script(generated_script(key), self.snapshot) for key in CHECKS}
        with patch('seo_agents.exploration.repo_analysis.graph.run_exploration_agent',
                   side_effect=lambda category, **kwargs: {'report': reports[category], 'run': {}}), patch(
                'seo_agents.exploration.repo_analysis.graph.snapshot_repository') as capture, redirect_stdout(io.StringIO()):
            state = run_onpage_graph(self.repo, mode='agent', snapshot=self.snapshot)
        capture.assert_not_called()
        self.assertIs(state['snapshot'], self.snapshot)

    def test_agent_can_consult_prior_evidence_then_independently_inspect_source(self):
        links = execute_script(generated_script('internal_links'), self.snapshot)
        handoff = build_handoff('content', {'internal_links': links}, {'internal_links': {
            'framework': 'html', 'notes': 'Entry is index.html.', 'inspected_files': ['index.html'],
            'script': 'DO NOT SHARE PRIOR CODE'}})

        class HandoffModel(ToolCallingModel):
            _saw_prior: bool = PrivateAttr(default=False)

            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                if self._calls < 2:
                    self._calls += 1
                    name, args = ('query_prior_report', {'category': 'internal_links', 'url': '/', 'field': 'links'}) if self._calls == 1 else ('read_source', {'path': 'index.html'})
                    message = AIMessage(content='', tool_calls=[{'name': name, 'args': args, 'id': 'inspect-' + str(self._calls)}])
                    return ChatResult(generations=[ChatGeneration(message=message)])
                self._saw_prior = any(isinstance(m, ToolMessage) and m.name == 'query_prior_report'
                                     and 'index.html:2:' in m.content for m in messages)
                return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

        model = HandoffModel(source=generated_script('content'))
        result = run_exploration_agent('content', snapshot=self.snapshot, model=model, handoff=handoff)
        self.assertTrue(model._saw_prior)
        self.assertTrue(model._saw_source)
        self.assertEqual(result['run']['verification'], 'passed')

    def test_discovery_loop_is_forced_to_submit_without_removing_structured_response_tool(self):
        model = BrowsingModel(source=generated_script('content'))
        result = run_exploration_agent('content', snapshot=self.snapshot, model=model, max_discovery_rounds=2)
        self.assertEqual(model._calls, 3)
        self.assertEqual(result['run']['metrics']['discovery_model_calls'], 2)
        self.assertEqual(result['run']['metrics']['finalization_model_calls'], 1)
        self.assertEqual(result['report']['extraction']['discovery_budget'], {'max_model_rounds': 2, 'reached': True})
        self.assertTrue(any('budget reached' in item for item in result['report']['pages'][0]['coverage']['limitations']))

    def test_content_reference_repair_can_query_prior_report_after_discovery_expires(self):
        valid = generated_script('content')
        invalid = valid.replace("link_refs=[{'source_id': l['source_id']", "link_refs=[{'source_id': 'index.html:2:19:a'")
        reports = {key: execute_script(generated_script(key), self.snapshot) for key in ('internal_links', 'media')}
        handoff = build_handoff('content', reports, {})
        test = self

        class ReferenceRepairModel(ToolCallingModel):
            _available: set = PrivateAttr(default_factory=set)
            _saw_prior: bool = PrivateAttr(default=False)

            def bind_tools(self, tools, **kwargs):
                self._available = {(tool.get('function', {}).get('name') or tool.get('name')) if isinstance(tool, dict) else tool.name for tool in tools}
                return self

            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                self._calls += 1
                if self._calls == 1:
                    name, args = 'read_source', {'path': 'index.html'}
                elif self._calls == 2:
                    name, args = 'ExtractionPlan', {'framework': 'html', 'notes': 'data-row reference', 'source': invalid}
                elif self._calls == 3:
                    # Repairs get a small fresh tool budget (read/grep/draft tools and prior reports).
                    test.assertIn('query_prior_report', self._available)
                    test.assertIn('read', self._available)
                    test.assertIn('edit_file', self._available)
                    test.assertIn('test_script', self._available)
                    test.assertTrue(any('/pages/0/link_refs/0' in str(m.content) for m in messages))
                    # Fresh compact repair turn: the discovery transcript is not resent.
                    test.assertFalse(any(isinstance(m, ToolMessage) and m.name == 'read_source' for m in messages))
                    name, args = 'query_prior_report', {'category': 'internal_links', 'url': '/', 'field': 'links'}
                else:
                    self._saw_prior = any(isinstance(m, ToolMessage) and m.name == 'query_prior_report' and 'source_id' in m.content for m in messages)
                    name, args = 'ExtractionPlan', {'framework': 'html', 'notes': 'corrected element identity', 'source': valid}
                message = AIMessage(content='', tool_calls=[{'name': name, 'args': args, 'id': 'step-' + str(self._calls)}])
                return ChatResult(generations=[ChatGeneration(message=message)])

        model = ReferenceRepairModel(source=invalid)
        result = run_exploration_agent('content', snapshot=self.snapshot, model=model, handoff=handoff, max_discovery_rounds=1)
        self.assertTrue(model._saw_prior)
        self.assertEqual(result['run']['attempts'], 2)
        self.assertEqual(result['run']['metrics']['repair_model_calls'], 2)
        self.assertEqual(result['report']['pages'][0]['link_refs'][0]['source_id'], reports['internal_links']['pages'][0]['links'][0]['source_id'])
        self.assertEqual(result['report']['pages'][0]['blocks'][0]['text'], 'Observed heading')

    def test_missing_target_occurrence_is_an_explicit_gap_while_content_is_preserved(self):
        invalid = generated_script('content')
        repaired = invalid.replace("link_refs=[{'source_id': l['source_id'], 'report': 'internal_links.json'} for l in links]", 'link_refs=[]')
        repaired = repaired.replace('        pages.append(row)',
            "        row['coverage']['limitations'].append('Link occurrences are observed here but absent from the earlier links report; references remain unresolved.')\n        pages.append(row)")
        links = execute_script(generated_script('internal_links'), self.snapshot)
        links['pages'][0]['links'] = []
        agent = Mock()
        agent.invoke.side_effect = [
            {'structured_response': ExtractionPlan('html', 'unmatched occurrence', invalid)},
            {'structured_response': ExtractionPlan('html', 'explicit predecessor coverage gap', repaired)}]

        def factory(**kwargs):
            next(tool for tool in kwargs['tools'] if tool.name == 'read_source').invoke({'path': 'index.html'})
            return agent

        result = run_exploration_agent('content', snapshot=self.snapshot, model=Mock(), agent_factory=factory,
            handoff=build_handoff('content', {'internal_links': links}, {}))
        page = result['report']['pages'][0]
        self.assertEqual(page['blocks'], execute_script(invalid, self.snapshot)['pages'][0]['blocks'])
        self.assertTrue(any('references remain unresolved' in limitation for limitation in page['coverage']['limitations']))
        self.assertEqual(result['run']['attempts'], 2)

    def test_persistent_content_mismatch_stops_before_eeat(self):
        called = []

        def factory(**kwargs):
            category = kwargs['name'].removeprefix('explore_')
            called.append(category)
            next(tool for tool in kwargs['tools'] if tool.name == 'read_source').invoke({'path': 'index.html'})
            source = generated_script(category)
            if category == 'content':
                source = source.replace("link_refs=[{'source_id': l['source_id']", "link_refs=[{'source_id': 'index.html:2:19:a'")
            agent = Mock()
            agent.invoke.return_value = {'structured_response': ExtractionPlan('html', 'entry', source)}
            return agent

        with patch('seo_agents.exploration.repo_analysis.agents.llm.extractor.create_agent', side_effect=factory), patch(
                'seo_agents.exploration.repo_analysis.agents.llm.extractor.get_chat_model', return_value=Mock()):
            with self.assertRaises(ExplorationIncomplete) as caught:
                run_onpage_graph(self.repo, mode='agent')
        self.assertEqual(called[0], 'urls')
        self.assertEqual(set(called), {'urls', 'page_structure', 'internal_links', 'media', 'metadata', 'content'})
        self.assertEqual(called[-1], 'content')  # eeat never starts.
        self.assertEqual(caught.exception.checkpoint['checkpoint']['completed'],
                         ['urls', 'page_structure', 'internal_links', 'media', 'metadata'])
        self.assertIn('Unmatched cross-report reference', caught.exception.checkpoint['agent_runs']['content']['previous_errors'][0])

    def test_invalid_discovery_budgets_are_rejected_before_model_use(self):
        for value in (0, -1, 101, '2'):
            with self.subTest(value=value), patch('seo_agents.exploration.repo_analysis.agents.llm.extractor.get_chat_model') as model:
                with self.assertRaises(ValueError):
                    run_exploration_agent('content', snapshot=self.snapshot, max_discovery_rounds=value)
                model.assert_not_called()

    def test_same_program_extracts_changed_source_across_framework_templates(self):
        for file in ['pages/index.vue', 'src/routes/+page.svelte', 'src/pages/index.astro', 'app/page.tsx']:
            with self.subTest(file=file):
                snapshot = dict(self.snapshot, files={file: '<main><h1>Different framework copy</h1></main>'})
                report = verify_and_extract('content', generated_script('content'), snapshot)
                self.assertEqual(report['pages'][0]['template'], file)
                self.assertEqual(report['pages'][0]['blocks'][0]['text'], 'Different framework copy')

    def test_rejected_script_is_repaired_before_production_extraction(self):
        fake = Mock()
        fake.invoke.side_effect = [
            {'structured_response': ExtractionPlan('html', 'first', 'import os\ndef extract(files, assets):\n    return {}')},
            {'structured_response': ExtractionPlan('html', 'repaired', generated_script('content'))},
        ]
        def factory(**kwargs):
            next(t for t in kwargs['tools'] if t.name == 'read_source').invoke({'path': 'index.html'})
            return fake
        with patch('seo_agents.exploration.repo_analysis.agents.llm.verification.subprocess.run', wraps=__import__('subprocess').run) as worker:
            result = run_exploration_agent('content', snapshot=self.snapshot, model=Mock(), agent_factory=factory)
        self.assertEqual(worker.call_count, 2)  # Only the accepted script's trial and extraction.
        self.assertEqual(result['run']['attempts'], 2)
        self.assertIn('Verification failed', fake.invoke.call_args_list[1].args[0]['messages'][-1]['content'])

    def test_three_failures_stop_without_returning_an_artifact(self):
        agent = Mock()
        agent.invoke.return_value = {'structured_response': ExtractionPlan('html', '', 'def extract(files, assets):\n    return {}')}
        with self.assertRaisesRegex(RuntimeError, 'after 3 attempts'):
            run_exploration_agent('content', snapshot=self.snapshot, model=Mock(), agent_factory=Mock(return_value=agent))
        self.assertEqual(agent.invoke.call_count, 3)

    def test_content_type_errors_identify_field_type_and_location_without_dumping_copy(self):
        original = execute_script(generated_script('content'), self.snapshot)
        for field, value, actual_type in [('section', None, 'NoneType'), ('section', {'id': 'main'}, 'dict'),
                                           ('tag', ['p'], 'list'), ('text', 42, 'int')]:
            with self.subTest(field=field, actual_type=actual_type):
                report = copy.deepcopy(original)
                report['pages'][0]['blocks'][0][field] = value
                with self.assertRaises(ObservationValidationError) as caught:
                    validate_observations('content', report, self.snapshot)
                issue = caught.exception.issues[0]
                self.assertEqual(issue['pointer'], '/pages/0/blocks/0/' + field)
                self.assertEqual(issue['actual_type'], actual_type)
                self.assertEqual(issue['file'], 'index.html')
                self.assertIn('hint', issue)
                self.assertNotIn('Observed heading', str(caught.exception))

    def test_null_section_is_repaired_with_precise_feedback_and_no_type_coercion(self):
        valid = generated_script('content')
        invalid = valid.replace("section=node['section']", 'section=None')
        agent = Mock()
        agent.invoke.side_effect = [
            {'structured_response': ExtractionPlan('html', 'bad context', invalid)},
            {'structured_response': ExtractionPlan('html', 'fixed context', valid)},
        ]
        def factory(**kwargs):
            next(t for t in kwargs['tools'] if t.name == 'read_source').invoke({'path': 'index.html'})
            return agent
        result = run_exploration_agent('content', snapshot=self.snapshot, model=Mock(), agent_factory=factory)
        self.assertEqual(result['run']['attempts'], 2)
        feedback = agent.invoke.call_args_list[1].args[0]['messages'][-1]['content']
        self.assertIn('/pages/0/blocks/0/section', feedback)
        self.assertIn('NoneType', feedback)
        self.assertEqual(result['report']['pages'][0]['blocks'][0]['section'], 'main')

    def test_missing_observation_fields_identify_records_across_categories(self):
        fields = {'content': 'blocks', 'page_structure': 'headings', 'internal_links': 'links',
                  'metadata': 'declarations', 'media': 'items', 'urls': 'query_links', 'eeat': 'trust_links'}
        for category, field in fields.items():
            with self.subTest(category=category):
                report = execute_script(generated_script(category), self.snapshot)
                report['pages'][0][field] = [{'file': 'index.html'}]
                with self.assertRaises(ObservationValidationError) as caught:
                    validate_observations(category, report, self.snapshot)
                issue = caught.exception.issues[0]
                self.assertEqual(issue['pointer'], '/pages/0/' + field + '/0')
                self.assertIn('line', issue['missing_fields'])
                self.assertNotIn('file', issue['missing_fields'])

    def test_eeat_evidence_errors_are_bounded_precise_and_do_not_echo_copy(self):
        original = execute_script(generated_script('eeat'), self.snapshot)
        for value, actual_type, missing in [('Private client copy', 'str', None),
                ({'file': 'index.html', 'line': 2}, 'dict', ['source_id', 'text']),
                (None, 'NoneType', None)]:
            with self.subTest(actual_type=actual_type):
                report = copy.deepcopy(original)
                report['pages'][0]['signals'] = [{'signal': 'authorship', 'status': 'candidate_evidence',
                                                 'evidence': [value] * 10}]
                with self.assertRaises(ObservationValidationError) as caught:
                    validate_observations('eeat', report, self.snapshot)
                self.assertEqual(len(caught.exception.issues), 4)
                issue = caught.exception.issues[0]
                self.assertEqual(issue['pointer'], '/pages/0/signals/0/evidence/0')
                self.assertEqual(issue['actual_type'], actual_type)
                self.assertEqual(issue.get('missing_fields'), missing)
                self.assertIn('evidence=[]', issue['hint'])
                self.assertNotIn('Private client copy', str(caught.exception))

    def test_eeat_evidence_shape_and_text_types_are_validated(self):
        original = execute_script(generated_script('eeat'), self.snapshot)
        ref = {'file': 'index.html', 'line': 2, 'source_id': 'index.html:2:0:main'}
        for evidence, suffix, expected in [('Private client copy', '', 'list of observation objects'),
                ([dict(ref, text=None)], '/0/text', 'string')]:
            with self.subTest(evidence=evidence):
                report = copy.deepcopy(original)
                report['pages'][0]['signals'] = [{'signal': 'authorship', 'status': 'candidate_evidence',
                                                 'evidence': evidence}]
                with self.assertRaises(ObservationValidationError) as caught:
                    validate_observations('eeat', report, self.snapshot)
                issue = caught.exception.issues[0]
                self.assertEqual(issue['pointer'], '/pages/0/signals/0/evidence' + suffix)
                self.assertEqual(issue['expected'], expected)
                self.assertNotIn('Private client copy', str(caught.exception))
        original['pages'][0]['signals'] = [{'signal': 'authorship',
            'status': 'not_observed_in_static_content', 'evidence': []}]
        self.assertIs(validate_observations('eeat', original, self.snapshot), original)

    def test_real_eeat_agent_repairs_evidence_after_discovery_budget_is_reached(self):
        valid = generated_script('eeat').replace('row.update(signals=[],',
            "row.update(signals=[{'signal': 'authorship', 'status': 'candidate_evidence', 'evidence': [blocks[0]]}],")
        invalid = valid.replace("'evidence': [blocks[0]]", "'evidence': [blocks[0]['text']]")

        class RepairingModel(BrowsingModel):
            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                if any('Verification failed:' in str(m.content) and '/pages/0/signals/0/evidence/0' in str(m.content)
                       for m in messages):
                    self.source = valid
                return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

        model = RepairingModel(source=invalid)
        result = run_exploration_agent('eeat', snapshot=self.snapshot, model=model, max_discovery_rounds=1)
        self.assertEqual(result['run']['attempts'], 2)
        self.assertEqual(result['run']['metrics']['finalization_model_calls'], 2)
        self.assertIn('/pages/0/signals/0/evidence/0', result['run']['previous_errors'][0])
        evidence = result['report']['pages'][0]['signals'][0]['evidence'][0]
        self.assertEqual(evidence['text'], 'Observed heading')
        self.assertEqual(evidence['file'], 'index.html')

    def test_plain_model_response_is_repaired_in_existing_conversation(self):
        message = AIMessage(content='I will now produce the script.', response_metadata={'finish_reason': 'stop'})
        agent = Mock()
        agent.invoke.side_effect = [
            {'messages': [message]},
            {'structured_response': ExtractionPlan('html', 'complete result', generated_script('content'))},
        ]
        def factory(**kwargs):
            next(t for t in kwargs['tools'] if t.name == 'read_source').invoke({'path': 'index.html'})
            return agent
        result = run_exploration_agent('content', snapshot=self.snapshot, model=Mock(), agent_factory=factory)
        self.assertEqual(result['run']['attempts'], 2)
        repair_messages = agent.invoke.call_args_list[1].args[0]['messages']
        self.assertIs(repair_messages[0], message)
        self.assertIn('ExtractionPlan response tool', repair_messages[-1]['content'])
        self.assertEqual(result['run']['metrics']['last_response_diagnostic']['finish_reason'], 'stop')

    def test_syntax_repair_receives_bounded_generated_source_context(self):
        invalid = 'def extract(files, assets):\n    values = [\n    return {}'
        agent = Mock()
        agent.invoke.side_effect = [
            {'structured_response': ExtractionPlan('html', 'incomplete program', invalid)},
            {'structured_response': ExtractionPlan('html', 'repaired', generated_script('content'))},
        ]
        def factory(**kwargs):
            next(t for t in kwargs['tools'] if t.name == 'read_source').invoke({'path': 'index.html'})
            return agent
        result = run_exploration_agent('content', snapshot=self.snapshot, model=Mock(), agent_factory=factory)
        feedback = agent.invoke.call_args_list[1].args[0]['messages'][-1]['content']
        self.assertIn('Generated-source context', feedback)
        self.assertIn('"line": 2', feedback)
        self.assertIn('values = [', feedback)
        self.assertEqual(result['run']['attempts'], 2)

    def test_missing_plans_exhaust_bounded_attempts_with_diagnostics(self):
        agent = Mock()
        agent.invoke.return_value = {'messages': [AIMessage(content='Incomplete', response_metadata={'finish_reason': 'length'})]}
        with self.assertRaises(ExtractionFailed) as caught:
            run_exploration_agent('content', snapshot=self.snapshot, model=Mock(), agent_factory=Mock(return_value=agent))
        self.assertEqual(agent.invoke.call_count, 3)
        self.assertIsNone(caught.exception.run['script'])
        self.assertEqual(caught.exception.run['metrics']['last_response_diagnostic']['finish_reason'], 'length')

    def test_identical_rejected_scripts_are_not_executed_repeatedly_and_remain_diagnosable(self):
        invalid = generated_script('content').replace("section=node['section']", 'section=None')
        agent = Mock()
        agent.invoke.return_value = {'structured_response': ExtractionPlan('html', 'bad context', invalid)}
        def factory(**kwargs):
            next(t for t in kwargs['tools'] if t.name == 'read_source').invoke({'path': 'index.html'})
            return agent
        with patch('seo_agents.exploration.repo_analysis.agents.llm.verification.subprocess.run', wraps=__import__('subprocess').run) as worker:
            with self.assertRaises(ExtractionFailed) as caught:
                run_exploration_agent('content', snapshot=self.snapshot, model=Mock(), agent_factory=factory)
        self.assertEqual(worker.call_count, 1)
        self.assertEqual(caught.exception.run['script'], invalid)
        self.assertEqual(caught.exception.run['verification'], 'failed')
        self.assertEqual(len(caught.exception.run['previous_errors']), 3)
        self.assertIn('Unchanged rejected script', caught.exception.run['previous_errors'][-1])

    def test_failed_agent_diagnostics_join_completed_work_in_partial_checkpoint(self):
        urls = verify_and_extract('urls', generated_script('urls'), self.snapshot)
        failure = ExtractionFailed('page_structure', {'category': 'page_structure', 'verification': 'failed',
            'script': 'rejected script', 'previous_errors': ['bad section']})
        checkpoints = []
        def extract(category, **kwargs):
            if category == 'urls':
                return {'report': urls, 'run': {'verification': 'passed'}}
            if category == 'page_structure':
                raise failure
            __import__('time').sleep(0.3)  # Parallel siblings finish after the failure is raised.
            return {'report': execute_script(generated_script(category), self.snapshot), 'run': {'verification': 'passed'}}

        with patch('seo_agents.exploration.repo_analysis.graph.run_exploration_agent', side_effect=extract):
            with self.assertRaises(ExplorationIncomplete) as caught:
                run_onpage_graph(self.repo, mode='agent', on_checkpoint=checkpoints.append)
        checkpoint = caught.exception.checkpoint
        self.assertEqual(checkpoint['checkpoint']['completed'], ['urls'])
        self.assertEqual(set(checkpoint['artifacts']), {'urls'})
        self.assertEqual(checkpoint['agent_runs']['page_structure']['script'], 'rejected script')
        self.assertEqual(checkpoints[-1]['checkpoint']['agent_runs']['page_structure']['verification'], 'failed')

    def test_no_silent_fallback_when_model_disabled(self):
        with patch.dict(os.environ, {'SEO_NO_LLM': '1'}):
            with self.assertRaisesRegex(RuntimeError, 'requires a model'):
                run_exploration_agent('content', snapshot=self.snapshot)

    def test_agent_must_inspect_source_before_script_is_accepted(self):
        agent = Mock()
        agent.invoke.return_value = {'structured_response': ExtractionPlan('html', '', generated_script('content'))}
        with self.assertRaisesRegex(RuntimeError, 'Read relevant repository source'):
            run_exploration_agent('content', snapshot=self.snapshot, model=Mock(), agent_factory=Mock(return_value=agent))

    def test_route_catalog_mismatch_is_returned_for_repair(self):
        agent = Mock()
        agent.invoke.return_value = {'structured_response': ExtractionPlan('html', '', generated_script('content'))}
        def factory(**kwargs):
            next(t for t in kwargs['tools'] if t.name == 'read_source').invoke({'path': 'index.html'})
            return agent
        with self.assertRaisesRegex(RuntimeError, 'verified_route_catalog'):
            run_exploration_agent('content', snapshot=self.snapshot, routes=[{'url': '/other', 'template': 'index.html'}],
                                  model=Mock(), agent_factory=factory)

    def test_policy_rejects_unsafe_capabilities_before_worker_runs(self):
        bodies = ['import os\n', 'open("/tmp/unwanted", "w")', 'files.__class__',
                  'eval("1")', 'getattr(files, "keys")', 'str.format',
                  'files.clear()', '(lambda: 1)()']
        for body in bodies:
            source = body + '\ndef extract(files, assets):\n    return {}' if body.startswith('import') else 'def extract(files, assets):\n    ' + body
            with self.subTest(body=body), patch('seo_agents.exploration.repo_analysis.agents.llm.verification.subprocess.run') as worker:
                with self.assertRaises(ValueError):
                    execute_script(source, self.snapshot)
                worker.assert_not_called()

    def test_policy_reports_multiple_violation_kinds_in_one_bounded_repair(self):
        source = 'VALUE = 1\ndef extract(files, assets):\n    try:\n        private = [_ for _ in []]\n        return open("private")\n    except ValueError:\n        return {}'
        with patch('seo_agents.exploration.repo_analysis.agents.llm.verification.subprocess.run') as worker:
            with self.assertRaises(ValueError) as caught:
                execute_script(source, self.snapshot)
        message = str(caught.exception)
        for problem in ('only functions', 'Private names', 'Unsupported Python syntax: Try', 'Unavailable function: open', 'line 3'):
            self.assertIn(problem, message)
        self.assertLess(len(message), 2000)
        worker.assert_not_called()

    def test_nested_pure_helpers_are_callable_and_still_subject_to_capability_checks(self):
        source = 'def extract(files, assets):\n    def title(text):\n        return parse_html(text)[0]["text"]\n    return {"title": title(files["index.html"])}'
        self.assertEqual(execute_script(source, self.snapshot)['title'], 'Example')
        unsafe = source.replace('return parse_html(text)[0]["text"]', 'return open(text)')
        with patch('seo_agents.exploration.repo_analysis.agents.llm.verification.subprocess.run') as worker:
            with self.assertRaisesRegex(ValueError, 'Unavailable function: open'):
                execute_script(unsafe, self.snapshot)
        worker.assert_not_called()

    def test_source_relationships_accept_file_and_json_pointer_without_markup_locations(self):
        snapshot = dict(self.snapshot, files={**self.snapshot['files'], 'data.json': '{"items":[{"a/b":"Tea"}]}'})
        report = execute_script(generated_script('content'), snapshot)
        page = report['pages'][0]
        page['content_sources'] = [{'file': 'index.html', 'pointer': '', 'kind': 'template'},
                                   {'file': 'data.json', 'pointer': '/items/0/a~1b', 'kind': 'local-json'}]
        validate_observations('content', report, snapshot)
        self.assertEqual(page['content_sources'][1]['pointer_validation'], 'resolved-json-pointer')
        for relationship in [{'file': 'missing.json', 'pointer': ''},
                             {'file': 'data.json', 'pointer': '/items/~2'},
                             {'file': 'data.json', 'pointer': None},
                             {'file': 'index.html', 'pointer': '', 'line': 9999}]:
            with self.subTest(relationship=relationship):
                page['content_sources'] = [relationship]
                with self.assertRaises(ValueError):
                    validate_observations('content', report, snapshot)
        for pointer in ('/items/8', '/items/-1', '/items/00'):
            page['content_sources'] = [{'file': 'data.json', 'pointer': pointer}]
            validate_observations('content', report, snapshot)
            self.assertEqual(page['content_sources'][0]['pointer_validation'], 'invalid-or-unresolvable-json-pointer')
        page['content_sources'] = [{'file': 'index.html', 'pointer': '/HomePage'}]
        validate_observations('content', report, snapshot)
        self.assertEqual(page['content_sources'][0]['pointer_validation'], 'unverified-source-selector')
        self.assertTrue(any('selectors are unverified or invalid' in text for text in page['coverage']['limitations']))
        page['content_sources'] = []
        del page['blocks'][0]['source_id']
        with self.assertRaises(ValueError):
            validate_observations('content', report, snapshot)

    def test_worker_exposes_safe_stack_operations_used_by_source_parsers(self):
        source = 'def extract(files, assets):\n    stack = [1, 2, 3]\n    last = stack.pop()\n    return {"remaining": list(reversed(stack)), "last": last}'
        self.assertEqual(execute_script(source, self.snapshot), {'remaining': [2, 1], 'last': 3})

    def test_worker_allows_padding_and_sequence_operations_without_host_capabilities(self):
        source = '''def extract(files, assets):
    values = ['c', 'a']
    values.insert(1, 'b')
    values.sort()
    values.reverse()
    return {'padded': str(len(files['index.html'].splitlines())).zfill(3),
            'left': 'a'.ljust(3), 'right': 'a'.rjust(3), 'center': 'a'.center(3),
            'folded': 'STRASSE'.casefold(), 'parts': 'a:b:c'.partition(':'),
            'last_parts': 'a:b:c'.rpartition(':'), 'values': values}
'''
        result = execute_script(source, self.snapshot)
        self.assertEqual(result['padded'], '002')
        self.assertEqual(result['values'], ['c', 'b', 'a'])
        self.assertEqual(result['left'], 'a  ')
        self.assertEqual(result['right'], '  a')
        self.assertEqual(result['center'], ' a ')
        self.assertEqual(result['folded'], 'strasse')
        self.assertEqual(result['parts'], ['a', ':', 'b:c'])
        self.assertEqual(result['last_parts'], ['a:b', ':', 'c'])

    def test_agent_receives_the_workers_method_allowlist(self):
        from seo_agents.exploration.repo_analysis.agents.llm.script_policy import METHODS
        captured = {}

        def factory(**kwargs):
            captured.update(kwargs)
            return create_agent(**kwargs)

        run_exploration_agent('content', snapshot=self.snapshot,
            model=ToolCallingModel(source=generated_script('content')), agent_factory=factory)
        self.assertIn('Allowed methods (from the worker policy): ' + ', '.join(sorted(METHODS)), captured['system_prompt'])
        for source in ['def extract(files, assets):\n    return "{0.__class__}".format(files)',
                       'def extract(files, assets):\n    return files.__class__']:
            with self.assertRaises(ValueError):
                execute_script(source, self.snapshot)

    def test_worker_stops_infinite_loop(self):
        with self.assertRaisesRegex(ValueError, 'time limit'):
            execute_script('def extract(files, assets):\n    while True:\n        pass', self.snapshot, timeout=0.15)

    def test_invalid_locations_empty_reports_and_word_counts_are_rejected(self):
        source = generated_script('content')
        for modified in [source.replace("'line': node['line']", "'line': 99999"),
                         source.replace("'file': file", "'file': '../secret'"),
                         source.replace("word_count=sum(len(b['text'].split()) for b in blocks)", 'word_count=999'),
                         'def extract(files, assets):\n    return {"schema_version":"2.0","category":"content","extraction":{},"pages":[]}']:
            with self.subTest(modified=modified[:80]), self.assertRaises(ValueError):
                verify_and_extract('content', modified, self.snapshot)

    def test_unrepeatable_output_is_rejected(self):
        valid = execute_script(generated_script('content'), self.snapshot)
        changed = copy.deepcopy(valid)
        changed['extraction']['changed'] = True
        with patch('seo_agents.exploration.repo_analysis.agents.llm.verification.execute_script', side_effect=[valid, changed]):
            with self.assertRaisesRegex(ValueError, 'changed between'):
                verify_and_extract('content', generated_script('content'), self.snapshot)

    def test_snapshot_and_tools_exclude_secrets_external_symlinks_and_report_files(self):
        (self.repo / '.env').write_text('SECRET=hidden')
        (self.repo / 'private.key').write_text('private')
        (self.repo / 'external.html').symlink_to('/etc/hosts')
        (self.repo / 'seo_reports').mkdir()
        (self.repo / 'seo_reports' / 'content.json').write_text('old inventory')
        (self.repo / 'component.vue').write_text('<h2>Vue source</h2>')
        snapshot = snapshot_repository(self.repo)
        self.assertEqual(set(snapshot['files']), {'index.html', 'component.vue'})
        tools = {t.name: t for t in repository_tools(snapshot)}
        self.assertIn('error', tools['read_source'].invoke({'path': '../.env'}))
        self.assertEqual(tools['search_source'].invoke({'needle': 'Vue'})['total'], 1)

    def test_graph_runs_seven_isolated_skills_and_retains_no_client_files(self):
        prompts = []
        def factory(**kwargs):
            category = kwargs['name'].removeprefix('explore_')
            prompts.append(kwargs['system_prompt'])
            next(t for t in kwargs['tools'] if t.name == 'read_source').invoke({'path': 'index.html'})
            agent = Mock()
            agent.invoke.return_value = {'structured_response': ExtractionPlan('html', 'entry verified', generated_script(category))}
            return agent
        before = sorted(p.relative_to(self.repo).as_posix() for p in self.repo.rglob('*'))
        with patch('seo_agents.exploration.repo_analysis.agents.llm.extractor.create_agent', side_effect=factory), patch(
                'seo_agents.exploration.repo_analysis.agents.llm.extractor.get_chat_model', return_value=Mock()), patch.object(
                Path, 'write_text', side_effect=AssertionError('analysis wrote source')), patch.object(
                Path, 'write_bytes', side_effect=AssertionError('analysis wrote bytes')):
            state = run_onpage_graph(str(self.repo), mode='agent')
        self.assertEqual(set(state['artifacts']), set(REPORT_FILES))
        self.assertEqual(set(state['agent_runs']), set(REPORT_FILES))
        self.assertEqual(len(set(prompts)), 7)
        self.assertEqual(before, sorted(p.relative_to(self.repo).as_posix() for p in self.repo.rglob('*')))
        state['artifacts']['internal_links']['pages'][0]['links'] = []
        with self.assertRaisesRegex(ValueError, 'Unmatched cross-report'):
            validate_artifact_links(state)

    def test_container_sync_includes_skills_and_worker(self):
        sandbox = Mock()
        sync_sources(sandbox)
        paths = [call.args[0] for call in sandbox.write.call_args_list]
        self.assertTrue(any(p.endswith('/skills/content/SKILL.md') for p in paths))
        self.assertTrue(any(p.endswith('/agents/llm/script_worker.py') for p in paths))
        self.assertTrue(any(p.endswith('/repo_analysis/skills/routes/SKILL.md') for p in paths))
        self.assertTrue(any(p.endswith('/pyproject.toml') for p in paths))
        self.assertFalse(any('__pycache__' in p for p in paths))


if __name__ == '__main__':
    unittest.main()
