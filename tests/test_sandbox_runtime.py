"""Provisioning must pass version constraints intact through the sandbox shell."""

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

from seo_agents.sandbox.host import (PIP_DEPS, RENDER_MARKER, RUNTIME_MARKER, ensure_container_runtime,
                                     ensure_render_runtime, project_dependencies, render_runtime_requested)
from seo_agents.sandbox.tools.local import LocalResult


class SandboxRuntimeTests(unittest.TestCase):
    def test_dependency_constraints_reach_pip_without_shell_redirections(self):
        sandbox = Mock()
        sandbox.run.side_effect = [LocalResult('', '', 1), LocalResult('', '', 0), LocalResult('', '', 0), LocalResult('', '', 0)]
        ensure_container_runtime(sandbox)
        self.assertIn('useradd', sandbox.run.call_args_list[2].args[0])
        install = sandbox.run.call_args_list[1].args[0]
        # Exercise bash, as DockerSandbox does, replacing Python only to avoid
        # installing packages. Any accidental redirects still execute in bash.
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                ['bash', '-c', 'python3() { printf "%s\\0" "$@"; }\n' + install],
                cwd=directory, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertEqual(result.stdout.decode().split('\0')[:-1],
                         ['-m', 'pip', 'install', '--quiet', '--break-system-packages', *PIP_DEPS])
        self.assertIn(RUNTIME_MARKER, sandbox.run.call_args_list[-1].args[0])

    def test_failed_install_does_not_mark_runtime_ready(self):
        sandbox = Mock()
        sandbox.run.side_effect = [LocalResult('', '', 1), LocalResult('', 'install failed', 1)]
        with self.assertRaisesRegex(RuntimeError, 'install failed'):
            ensure_container_runtime(sandbox)
        self.assertEqual(sandbox.run.call_count, 2)

    def test_container_dependencies_come_from_pyproject(self):
        self.assertEqual(PIP_DEPS, project_dependencies())
        self.assertTrue(any(dep.startswith('defusedxml') for dep in PIP_DEPS))
        self.assertFalse(any(dep.startswith(('beautifulsoup4', 'lxml')) for dep in PIP_DEPS))

    def test_render_runtime_is_opt_in_and_never_fails_the_run(self):
        self.assertFalse(render_runtime_requested({}))
        self.assertTrue(render_runtime_requested({'SEO_RENDER': '1'}))
        self.assertTrue(render_runtime_requested({'SEO_BUILD': 'true'}))
        sandbox = Mock()
        sandbox.run.return_value = LocalResult('', 'offline', 1)
        self.assertFalse(ensure_render_runtime(sandbox))
        self.assertFalse(any(RENDER_MARKER in call.args[0] and 'touch' in call.args[0]
                             for call in sandbox.run.call_args_list))


if __name__ == '__main__':
    unittest.main()
