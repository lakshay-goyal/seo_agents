"""Provisioning must pass version constraints intact through the sandbox shell."""

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

from seo_agents.sandbox.host import PIP_DEPS, RUNTIME_MARKER, ensure_container_runtime
from seo_agents.sandbox.tools.local import LocalResult


class SandboxRuntimeTests(unittest.TestCase):
    def test_dependency_constraints_reach_pip_without_shell_redirections(self):
        sandbox = Mock()
        sandbox.run.side_effect = [LocalResult('', '', 1), LocalResult('', '', 0), LocalResult('', '', 0)]
        ensure_container_runtime(sandbox)
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


if __name__ == '__main__':
    unittest.main()
