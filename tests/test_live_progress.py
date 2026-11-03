"""Progress must reach the terminal before the sandbox process completes."""

from contextlib import redirect_stdout
import io
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import Mock, patch

from seo_agents.utils.step_logger import progress
from seo_agents.sandbox.host_pipeline import run_pipeline_in_sandbox
from seo_agents.sandbox.runners.docker_sandbox import DockerResult, DockerSandbox


class LiveProgressTests(unittest.TestCase):
    def test_docker_output_is_visible_before_child_exit_and_kept_for_pr_detection(self):
        real_popen = subprocess.Popen
        processes = []
        observed_while_running = []
        with tempfile.TemporaryDirectory() as directory:
            gate = Path(directory) / 'continue'
            source = (
                'import pathlib, sys, time\n'
                'print("first stdout", flush=True)\n'
                'print("first stderr", file=sys.stderr, flush=True)\n'
                f'gate = pathlib.Path({str(gate)!r})\n'
                'deadline = time.monotonic() + 3\n'
                'while not gate.exists() and time.monotonic() < deadline:\n'
                '    time.sleep(0.01)\n'
                'if not gate.exists():\n'
                '    sys.exit(9)\n'
                'print("SEO_PR_URL=https://github.com/example/site/pull/1", flush=True)\n'
            )

            class Terminal(io.StringIO):
                def flush(self):
                    if 'first stdout\n' in self.getvalue() and not gate.exists():
                        observed_while_running.append(processes[0].poll() is None)
                        gate.write_text('continue')

            def launch(command, **kwargs):
                self.assertEqual(command[:3], ['docker', 'exec', '--env-file'])
                self.assertNotIn('-e', command)  # secrets never on the command line
                self.assertIn('TEST_OPTION=enabled', Path(command[3]).read_text())
                process = real_popen([sys.executable, '-u', '-c', source], **kwargs)
                processes.append(process)
                return process

            terminal = Terminal()
            with patch('seo_agents.sandbox.runners.docker_sandbox.subprocess.Popen', side_effect=launch), redirect_stdout(terminal):
                result = DockerSandbox('test-sandbox').run('python3 -u driver.py',
                    env={'TEST_OPTION': 'enabled'}, stream=True)
            self.assertTrue(result.success, result.stdout)
            self.assertEqual(observed_while_running, [True])
            self.assertEqual(terminal.getvalue(), result.stdout)
            self.assertEqual(result.stdout.count('first stdout'), 1)
            self.assertIn('first stderr', result.stdout)
            self.assertIn('SEO_PR_URL=', result.stdout)

    def test_streaming_preserves_failure_status_and_error_output(self):
        real_popen = subprocess.Popen
        source = 'import sys; print("extractor failed", file=sys.stderr, flush=True); sys.exit(7)'
        with patch('seo_agents.sandbox.runners.docker_sandbox.subprocess.Popen',
                   side_effect=lambda command, **kwargs: real_popen([sys.executable, '-u', '-c', source], **kwargs)), redirect_stdout(io.StringIO()) as terminal:
            result = DockerSandbox('test-sandbox').run('driver', stream=True)
        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, 7)
        self.assertEqual(terminal.getvalue(), result.stdout)
        self.assertIn('extractor failed', result.stdout)

    def test_pipeline_streams_unbuffered_and_does_not_replay_output(self):
        for exit_code in (0, 1):
            with self.subTest(exit_code=exit_code):
                sandbox = Mock(spec=DockerSandbox)

                def run(command, env=None, *, stream=False):
                    self.assertTrue(command.startswith('python3 -u '))
                    self.assertTrue(stream)
                    print('live child output', flush=True)
                    marker = 'SEO_BUNDLE=/opt/b.json' if ' analyze ' in command else 'SEO_PR_URL=https://github.com/example/site/pull/1'
                    return DockerResult('live child output\n' + marker + '\n', '', exit_code)

                sandbox.run.side_effect = run
                with patch('seo_agents.sandbox.host_pipeline.ensure_container_runtime'), patch(
                        'seo_agents.sandbox.host_pipeline.sync_sources'), redirect_stdout(io.StringIO()) as terminal:
                    if exit_code:
                        with self.assertRaisesRegex(RuntimeError, 'exit 1') as caught:
                            run_pipeline_in_sandbox(sandbox, 'https://github.com/example/site')
                        self.assertNotIn('live child output', str(caught.exception))
                    else:
                        self.assertEqual(run_pipeline_in_sandbox(sandbox, 'https://github.com/example/site'),
                                         'https://github.com/example/site/pull/1')
                # analyze + publish phases each print once; nothing is replayed
                self.assertEqual(terminal.getvalue().count('live child output'), 1 if exit_code else 2)

    def test_waiting_updates_stop_when_work_completes_or_fails(self):
        for fails in (False, True):
            with self.subTest(fails=fails):
                waiting = Event()
                messages = []

                def collect(message):
                    messages.append(message)
                    if 'waiting (' in message:
                        waiting.set()

                with patch('seo_agents.utils.step_logger.log', side_effect=collect):
                    try:
                        with progress('model call', interval=0.005):
                            self.assertTrue(waiting.wait(1))
                            if fails:
                                raise ValueError('model unavailable')
                    except ValueError:
                        self.assertTrue(fails)
                self.assertTrue(messages[0].startswith('START model call'))
                self.assertTrue(messages[-1].startswith(('FAILED ' if fails else 'DONE ') + 'model call'))


if __name__ == '__main__':
    unittest.main()
