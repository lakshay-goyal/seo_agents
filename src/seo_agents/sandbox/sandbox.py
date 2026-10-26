"""Local Docker sandbox — free, persistent replacement for LangSmith sandbox.

Runs a long-lived container (`sleep infinity`) on your machine, so it stays
up as long as you need: multiple scripts, whole-codebase exploration, many
agents. No credits, no timeouts, no account needed.

Same tiny interface the rest of the code uses:
  sandbox.run(command, env=None) -> result with .stdout/.stderr/.exit_code/.success
  sandbox.write(path, content)
"""

import os
import subprocess
import tempfile

IMAGE = "python:3.14-slim"
CONTAINER = "seo-agents-sandbox"


class DockerResult:
    """Mimics LangSmith's ExecutionResult so callers don't change."""

    def __init__(self, stdout, stderr, exit_code):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code

    @property
    def success(self):
        return self.exit_code == 0


class DockerSandbox:
    """A running container. `run` = `docker exec`, `write` = `docker cp`."""

    def __init__(self, container):
        self.container = container

    def run(self, command, env=None):
        cmd = ["docker", "exec"]
        for key, value in (env or {}).items():
            cmd += ["-e", f"{key}={value}"]
        cmd += [self.container, "bash", "-c", command]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        return DockerResult(proc.stdout, proc.stderr, proc.returncode)

    def write(self, path, content):
        data = content.encode() if isinstance(content, str) else content
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            parent = os.path.dirname(path)
            subprocess.run(
                ["docker", "exec", self.container, "mkdir", "-p", parent],
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["docker", "cp", tmp_path, f"{self.container}:{path}"],
                capture_output=True,
                check=True,
            )
        finally:
            os.unlink(tmp_path)


def _sh(args):
    """Run a local docker command, return stripped stdout."""
    proc = subprocess.run(args, capture_output=True, text=True)
    return proc


def create_sandbox():
    """Start (or reuse) the persistent container and return it."""
    print("Creating local Docker sandbox...")

    running = _sh(["docker", "ps", "-q", "-f", f"name={CONTAINER}"]).stdout.strip()
    if not running:
        _sh(["docker", "rm", "-f", CONTAINER])  # clear any stopped leftover
        proc = _sh(["docker", "run", "-d", "--name", CONTAINER, IMAGE, "sleep", "infinity"])
        if proc.returncode != 0:
            raise RuntimeError(
                "Could not start the container. Is Docker Desktop running?\n"
                f"{proc.stdout}\n{proc.stderr}"
            )

    sandbox = DockerSandbox(CONTAINER)

    # One-time tools inside the container (skipped on reuse if already there).
    if not sandbox.run("command -v git >/dev/null && command -v gh >/dev/null").success:
        print("Installing git/gh inside the container (first run only)...")
        result = sandbox.run(
            "apt-get update -qq && apt-get install -y -qq git gh curl ca-certificates"
        )
        if not result.success:
            raise RuntimeError(f"Tool install failed:\n{result.stdout}\n{result.stderr}")

    print("Sandbox is running.")
    return sandbox
