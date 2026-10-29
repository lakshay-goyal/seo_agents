"""Analysis is read-only; publication consumes memory and writes only in the sandbox."""

import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from seo_agents.exploration.checks.graph import run_onpage_graph
from seo_agents.exploration.checks.tools.helpers import REPORT_FILES
from seo_agents.sandbox.pr import create_pr
from seo_agents.sandbox.tools.docker import DockerSandbox
from seo_agents.sandbox.tools.local import LocalResult


def reports_in_memory():
    return {category: {'schema_version': '2.0', 'category': category,
                       'extraction': {'mode': 'static-source'}, 'pages': []}
            for category in REPORT_FILES}


class MemoryWorkflowTests(unittest.TestCase):
    def test_analysis_returns_reports_without_writing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / 'index.html').write_text('<main><h1>Example page</h1><p>Example content.</p></main>')
            before = sorted(str(p.relative_to(repo)) for p in repo.rglob('*'))
            with patch.object(Path, 'write_text', side_effect=AssertionError('analysis wrote text')), patch.object(
                    Path, 'write_bytes', side_effect=AssertionError('analysis wrote bytes')), patch(
                    'builtins.open', side_effect=AssertionError('unexpected file open')):
                state = run_onpage_graph(str(repo), mode='static')
            self.assertEqual(set(state['artifacts']), set(REPORT_FILES))
            self.assertEqual(state['artifacts']['content']['pages'][0]['blocks'][0]['text'], 'Example page')
            self.assertEqual(sorted(str(p.relative_to(repo)) for p in repo.rglob('*')), before)

    def test_pr_writes_exact_memory_payloads_only_in_target_checkout(self):
        reports = reports_in_memory()
        original = copy.deepcopy(reports)
        runner = Mock()
        def run(command, env=None):
            if command == 'gh api user -q .login':
                output = 'example-owner'
            elif command.endswith('-q .default_branch'):
                output = 'main'
            elif command.startswith('gh pr create '):
                output = 'https://github.com/example-owner/example-site/pull/1'
            elif 'git status --porcelain' in command:
                output = 'M seo_reports/onpage/content.json'
            else:
                output = ''
            return LocalResult(output, '', 0)
        runner.run.side_effect = run
        with patch('seo_agents.sandbox.pr.login_env', return_value={}):
            result = create_pr(runner, 'https://github.com/example-owner/example-site', reports)
        self.assertTrue(result.endswith('/pull/1'))
        writes = {args[0]: args[1] for args, kwargs in runner.write.call_args_list}
        self.assertEqual(len(writes), 8)  # Seven reports and the PR documentation.
        self.assertTrue(all(path.startswith('/tmp/example-site/') for path in writes))
        for category, filename in REPORT_FILES.items():
            self.assertEqual(json.loads(writes['/tmp/example-site/seo_reports/onpage/' + filename]), reports[category])
        self.assertEqual(reports, original)
        create_command = next(args[0] for args, kwargs in runner.run.call_args_list if args[0].startswith('gh pr create '))
        self.assertIn(' --body ', create_command)
        self.assertNotIn('--body-file', create_command)

    def test_publication_requires_memory_reports_before_any_side_effect(self):
        for reports in (None, {}):
            with self.subTest(reports=reports):
                runner = Mock()
                with patch('seo_agents.sandbox.pr.login_env') as login:
                    with self.assertRaises((ValueError, RuntimeError)):
                        create_pr(runner, 'https://github.com/example-owner/example-site', reports)
                login.assert_not_called()
                runner.run.assert_not_called()
                runner.write.assert_not_called()

    def test_all_reports_serialize_before_any_pr_changes(self):
        reports = reports_in_memory()
        reports['media']['extraction']['invalid_number'] = float('nan')
        runner = Mock()
        with patch('seo_agents.sandbox.pr.login_env') as login:
            with self.assertRaises(ValueError):
                create_pr(runner, 'https://github.com/example-owner/example-site', reports)
        login.assert_not_called()
        runner.run.assert_not_called()
        runner.write.assert_not_called()

    def test_docker_transfer_streams_bytes_without_a_host_tempfile(self):
        sandbox = DockerSandbox('example-sandbox')
        with patch('seo_agents.sandbox.tools.docker.subprocess.run') as run, patch(
                'tempfile.NamedTemporaryFile', side_effect=AssertionError('host tempfile created')):
            sandbox.write('/tmp/example-site/seo_reports/onpage/content.json', '{"text":"Example"}')
        args, kwargs = run.call_args
        self.assertEqual(args[0][:4], ['docker', 'exec', '-i', 'example-sandbox'])
        self.assertEqual(kwargs['input'], b'{"text":"Example"}')
        self.assertTrue(kwargs['check'])


if __name__ == '__main__':
    unittest.main()
