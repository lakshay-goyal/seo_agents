"""Docker sandbox: one long-running container for the whole pipeline."""

import os
import subprocess
import tempfile

IMAGE = "python:3.14-slim"
CONTAINER = "seo-agents-sandbox"


class DockerResult:
    def __init__(self, stdout, stderr, exit_code):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code

    @property
    def success(self):
        return self.exit_code == 0


class DockerSandbox:
    def __init__(self, container):
        self.container = container

    def run(self, command, env=None):
        cmd = ["docker", "exec"]
        for key, value in (env or {}).items():
            cmd = cmd + ["-e", key + "=" + value]
        cmd = cmd + [self.container, "bash", "-c", command]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        return DockerResult(proc.stdout, proc.stderr, proc.returncode)

    def write(self, path, content):
        if isinstance(content, str):
            content = content.encode()
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.write(content)
        tmp.close()
        try:
            parent = path.rsplit("/", 1)[0]
            subprocess.run(
                ["docker", "exec", self.container, "mkdir", "-p", parent],
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["docker", "cp", tmp.name, self.container + ":" + path],
                capture_output=True,
                check=True,
            )
        finally:
            os.unlink(tmp.name)


def docker_stdout(args):
    proc = subprocess.run(args, capture_output=True, text=True)
    return proc


def create_sandbox():
    print("Creating local Docker sandbox...")
    running = docker_stdout(["docker", "ps", "-q", "-f", "name=" + CONTAINER]).stdout.strip()
    if not running:
        docker_stdout(["docker", "rm", "-f", CONTAINER])
        proc = docker_stdout(["docker", "run", "-d", "--name", CONTAINER, IMAGE, "sleep", "infinity"])
        if proc.returncode != 0:
            raise RuntimeError(
                "Could not start the container. Is Docker Desktop running?\n"
                + proc.stdout
                + proc.stderr
            )
    sandbox = DockerSandbox(CONTAINER)
    tools_ready = sandbox.run("command -v git >/dev/null && command -v gh >/dev/null")
    if not tools_ready.success:
        print("Installing git/gh inside the container (first run only)...")
        result = sandbox.run("apt-get update -qq && apt-get install -y -qq git gh curl ca-certificates")
        if not result.success:
            raise RuntimeError("Tool install failed:\n" + result.stdout + result.stderr)
    print("Sandbox is running.")
    return sandbox
