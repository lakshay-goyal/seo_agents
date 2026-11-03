"""Sandbox isolation and PR hygiene: env-file secrets, per-run containers, phase split, compact commits."""

import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from seo_agents import main as cli
from seo_agents.sandbox.host_pipeline import phase_envs
from seo_agents.sandbox.git.pull_request import commit_plan, run_branch, scrub_paths, summary_markdown
from seo_agents.sandbox.runners import docker_sandbox as docker


class DockerSecretsTests(unittest.TestCase):
    def test_env_travels_by_env_file_never_on_the_command_line(self):
        seen = {}

        def fake_run(cmd, capture_output=True, text=True):
            seen['cmd'] = cmd
            path = cmd[cmd.index('--env-file') + 1]
            seen['path'] = path
            seen['mode'] = oct(os.stat(path).st_mode & 0o777)
            seen['text'] = Path(path).read_text()

            class Proc:
                stdout, stderr, returncode = 'ok', '', 0
            return Proc()
        with patch.object(docker.subprocess, 'run', side_effect=fake_run):
            result = docker.DockerSandbox('c1').run('echo hi', env={'GITHUB_TOKEN': 'ghp_secret'})
        self.assertTrue(result.success)
        self.assertNotIn('-e', seen['cmd'])
        self.assertFalse(any('ghp_secret' in part for part in seen['cmd']))
        self.assertEqual(seen['mode'], '0o600')
        self.assertIn('GITHUB_TOKEN=ghp_secret', seen['text'])
        self.assertFalse(os.path.exists(seen['path']))

    def test_newline_values_are_refused(self):
        with self.assertRaises(ValueError):
            docker.write_env_file({'A': 'x\nB=y'})

    def test_per_run_container_is_removed_and_limited(self):
        command = docker.run_command('seo-agents-abc', env={})
        self.assertIn('--rm', command)
        for flag in ('--cpus', '--memory', '--pids-limit'):
            self.assertIn(flag, command)
        self.assertIn('no-new-privileges', command)
        self.assertNotIn('--rm', docker.run_command('seo-agents-sandbox', reuse=True, env={}))


class PhaseSplitTests(unittest.TestCase):
    def test_token_only_reaches_the_publish_phase(self):
        analyze, publish = phase_envs({'GITHUB_TOKEN': 't', 'GH_TOKEN': 't', 'OPENROUTER_API_KEY': 'k', 'SEO_RENDER': '1'})
        self.assertNotIn('GITHUB_TOKEN', analyze)
        self.assertNotIn('GH_TOKEN', analyze)
        self.assertEqual(analyze['OPENROUTER_API_KEY'], 'k')
        self.assertEqual(analyze['SEO_BUILD_USER'], 'seo-build')
        self.assertEqual(publish, {'GITHUB_TOKEN': 't', 'GH_TOKEN': 't'})


class CommitHygieneTests(unittest.TestCase):
    def test_only_summary_and_small_files_are_committed(self):
        files = {'seo_reports/exploration/summary.json': '{"issues": []}',
                 'seo_reports/exploration/manifest.json': '{}',
                 'seo_reports/onpage/pages/p_1.json': 'x' * 1000,
                 'seo_reports/repo_context.json': 'x' * 400_000,
                 'seo_reports/exploration/pages/r_1.json': 'x' * 30_000}
        committed, excluded = commit_plan(files, budget=50_000, full=False)
        self.assertIn('seo_reports/exploration/summary.json', committed)
        self.assertIn('seo_reports/onpage/pages/p_1.json', committed)
        self.assertIn('seo_reports/repo_context.json', excluded)
        self.assertEqual(commit_plan(files, full=True)[1], {})

    def test_tmp_paths_are_scrubbed_and_branch_is_per_run(self):
        text = '{"file": "/tmp/seo-agents/a__b/src/App.jsx", "root": "/tmp/my-portfolio", "x": "/private/tmp/q/w"}'
        clean = scrub_paths(text, ('/tmp/seo-agents/a__b',))
        self.assertNotIn('/tmp/', clean)
        self.assertIn('"src/App.jsx"', clean)
        from datetime import datetime, timezone
        branch = run_branch('0123456789abcdef', datetime(2026, 11, 2, 9, 5, tzinfo=timezone.utc))
        self.assertEqual(branch, 'seo-agents/20261102-0905-0123456')

    def test_summary_markdown_lists_issues_and_proposals(self):
        files = {'seo_reports/exploration/summary.json': json.dumps({'issues': [
                     {'id': 'iss_soft404', 'severity': 'high', 'scope': 'site', 'fix': {'where': 'vercel.json'}}]}),
                 'seo_reports/onpage/pages/p_1.json': '{}'}
        text = summary_markdown(files)
        self.assertIn('iss_soft404', text)
        self.assertIn('vercel.json', text)
        self.assertIn('On-page proposals', text)


class CliTests(unittest.TestCase):
    def test_invalid_url_exits_nonzero(self):
        self.assertEqual(cli.main(['https://example.com/not-github']), 2)

    def test_tracing_and_onpage_settings_are_forwarded(self):
        with patch.dict(os.environ, {'GITHUB_TOKEN': 't', 'LANGSMITH_TRACING': 'true', 'SEO_ONPAGE_TIER': 'small',
                                     'SEO_MEASURE_PREBUILT': '0', 'UNRELATED_SECRET': 'x'}, clear=True):
            env = cli.forward_env()
        self.assertEqual(env['LANGSMITH_TRACING'], 'true')
        self.assertEqual(env['SEO_ONPAGE_TIER'], 'small')
        self.assertEqual(env['SEO_MEASURE_PREBUILT'], '0')
        self.assertNotIn('UNRELATED_SECRET', env)


if __name__ == '__main__':
    unittest.main()
