"""Exercise generated scripts and the real agent/tool loop without a paid model."""

import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from seo_agents.exploration.checks.agents.extractor import ExtractionPlan, repository_tools, run_exploration_agent
from seo_agents.exploration.checks.agents.repository import snapshot_repository
from seo_agents.exploration.checks.agents.verification import execute_script, verify_and_extract
from seo_agents.exploration.checks.graph import run_onpage_graph, validate_artifact_links
from seo_agents.exploration.checks.json_analysis import analyze_reports
from seo_agents.exploration.checks.tools.helpers import REPORT_FILES
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
        with patch('seo_agents.exploration.checks.agents.verification.subprocess.run', wraps=__import__('subprocess').run) as worker:
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
            with self.subTest(body=body), patch('seo_agents.exploration.checks.agents.verification.subprocess.run') as worker:
                with self.assertRaises(ValueError):
                    execute_script(source, self.snapshot)
                worker.assert_not_called()

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
        with patch('seo_agents.exploration.checks.agents.verification.execute_script', side_effect=[valid, changed]):
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
        with patch('seo_agents.exploration.checks.agents.extractor.create_agent', side_effect=factory), patch(
                'seo_agents.exploration.checks.agents.extractor.get_chat_model', return_value=Mock()), patch.object(
                Path, 'write_text', side_effect=AssertionError('analysis wrote source')), patch.object(
                Path, 'write_bytes', side_effect=AssertionError('analysis wrote bytes')):
            state = run_onpage_graph(str(self.repo), mode='agent')
        self.assertEqual(set(state['artifacts']), set(REPORT_FILES))
        self.assertEqual(set(state['agent_runs']), set(REPORT_FILES))
        self.assertEqual(len(set(prompts)), 7)
        self.assertEqual(before, sorted(p.relative_to(self.repo).as_posix() for p in self.repo.rglob('*')))
        self.assertIn('findings', analyze_reports(state['artifacts']))
        state['artifacts']['internal_links']['pages'][0]['links'] = []
        with self.assertRaisesRegex(ValueError, 'Unmatched cross-report'):
            validate_artifact_links(state)

    def test_container_sync_includes_skills_and_worker(self):
        sandbox = Mock()
        sync_sources(sandbox)
        paths = [call.args[0] for call in sandbox.write.call_args_list]
        self.assertTrue(any(p.endswith('/skills/content/SKILL.md') for p in paths))
        self.assertTrue(any(p.endswith('/agents/script_worker.py') for p in paths))


if __name__ == '__main__':
    unittest.main()
