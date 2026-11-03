"""Docker sandbox: one fresh, resource-limited container per run.

- Unique name per run (`seo-agents-<id>`), started with `--rm`, CPU/memory/PID limits and
  `no-new-privileges`, so concurrent runs never share state and nothing leaks between repos.
  `SEO_SANDBOX_REUSE=1` keeps one named container across runs (faster local iteration).
- Secrets never appear on a command line: `docker exec -e KEY=VALUE` is visible in the host
  process list, so environment values travel through a 0600 `--env-file` deleted right after.
"""

import os
import subprocess
import sys
import tempfile
import uuid

from seo_agents.utils.step_logger import done, log, start

IMAGE = "python:3.14-slim"
CONTAINER = "seo-agents-sandbox"  # only used with SEO_SANDBOX_REUSE=1
LIMITS = {"cpus": "SEO_SANDBOX_CPUS", "memory": "SEO_SANDBOX_MEMORY", "pids-limit": "SEO_SANDBOX_PIDS"}
DEFAULT_LIMITS = {"cpus": "4", "memory": "8g", "pids-limit": "4096"}


class DockerResult:
    def __init__(self, stdout, stderr, exit_code):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code

    @property
    def success(self):
        return self.exit_code == 0


def write_env_file(env):
    """0600 temp file with KEY=VALUE lines; caller deletes it. Newlines in values are refused."""
    handle, path = tempfile.mkstemp(prefix="seo-agents-env-", suffix=".env")
    try:
        os.fchmod(handle, 0o600)
        lines = []
        for key, value in env.items():
            value = "" if value is None else str(value)
            if "\n" in value or "\r" in value or "=" in key or not key:
                raise ValueError("environment entry cannot be passed through --env-file: " + repr(key))
            lines.append(key + "=" + value)
        os.write(handle, ("\n".join(lines) + "\n").encode())
    except BaseException:
        os.close(handle)
        os.unlink(path)
        raise
    os.close(handle)
    return path


class DockerSandbox:
    def __init__(self, container, *, ephemeral=False):
        self.container = container
        self.ephemeral = ephemeral

    def exec_command(self, command, env_file=None, user=None, interactive=False):
        cmd = ["docker", "exec"]
        if interactive:
            cmd.append("-i")
        if env_file:
            cmd += ["--env-file", env_file]
        if user:
            cmd += ["-u", user]
        return cmd + [self.container, "bash", "-c", command]

    def run(self, command, env=None, *, stream=False, user=None):
        env_file = write_env_file(env) if env else None
        try:
            cmd = self.exec_command(command, env_file=env_file, user=user)
            if stream:
                # One pipe preserves the order of progress and error messages and
                # avoids a full stderr pipe blocking the child while stdout is read.
                with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, bufsize=1) as proc:
                    output = []
                    try:
                        for line in proc.stdout:
                            sys.stdout.write(line)
                            sys.stdout.flush()
                            output.append(line)
                        return DockerResult(''.join(output), '', proc.wait())
                    except BaseException:
                        proc.terminate()
                        proc.wait()
                        raise
            proc = subprocess.run(cmd, capture_output=True, text=True)
            return DockerResult(proc.stdout, proc.stderr, proc.returncode)
        finally:
            if env_file:
                try:
                    os.unlink(env_file)
                except OSError:
                    pass

    def write(self, path, content):
        if isinstance(content, str):
            content = content.encode()
        # Stream bytes into the container; never stage source/report data on the host.
        writer = (
            "import pathlib, sys; dest = pathlib.Path(sys.argv[1]); "
            "dest.parent.mkdir(parents=True, exist_ok=True); "
            "dest.write_bytes(sys.stdin.buffer.read())"
        )
        subprocess.run(
            ["docker", "exec", "-i", self.container, "python3", "-c", writer, path],
            input=content,
            capture_output=True,
            check=True,
        )

    def close(self):
        """Remove a per-run container (no-op for the reusable named one)."""
        if self.ephemeral:
            docker_stdout(["docker", "rm", "-f", self.container])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def docker_stdout(args):
    proc = subprocess.run(args, capture_output=True, text=True)
    return proc


def limit_flags(env=None):
    env = os.environ if env is None else env
    flags = []
    for flag, variable in LIMITS.items():
        value = env.get(variable) or DEFAULT_LIMITS[flag]
        if value and value != "0":
            flags += ["--" + flag, value]
    return flags + ["--security-opt", "no-new-privileges"]


def run_command(name, *, reuse=False, env=None):
    command = ["docker", "run", "-d"]
    if not reuse:
        command.append("--rm")
    return command + ["--name", name, *limit_flags(env), IMAGE, "sleep", "infinity"]


def create_sandbox():
    begun = start("sandbox boot")
    reuse = os.environ.get("SEO_SANDBOX_REUSE", "0") == "1"
    name = CONTAINER if reuse else "seo-agents-" + uuid.uuid4().hex[:12]
    log("creating local Docker sandbox " + name + ("" if reuse else " (fresh, removed after the run)") + "...")
    running = docker_stdout(["docker", "ps", "-q", "-f", "name=^" + name + "$"]).stdout.strip() if reuse else ""
    if not running:
        if reuse:
            docker_stdout(["docker", "rm", "-f", name])
        proc = docker_stdout(run_command(name, reuse=reuse))
        if proc.returncode != 0:
            raise RuntimeError(
                "Could not start the container. Is Docker Desktop running?\n"
                + proc.stdout
                + proc.stderr
            )
    sandbox = DockerSandbox(name, ephemeral=not reuse)
    tools_ready = sandbox.run("command -v git >/dev/null && command -v gh >/dev/null")
    if not tools_ready.success:
        install_begun = start("installing git/gh in container")
        result = sandbox.run("apt-get update -qq && apt-get install -y -qq git gh curl ca-certificates")
        if not result.success:
            sandbox.close()
            raise RuntimeError(
                "Tool install failed (exit " + str(result.exit_code) + ").\n"
                + result.stdout + result.stderr
                + '\nRe-run by hand to see the full error:\n'
                + 'docker exec ' + name + ' bash -c "apt-get update && apt-get install -y git gh curl ca-certificates"'
            )
        done("installing git/gh in container", install_begun)
    done("sandbox boot", begun)
    return sandbox
