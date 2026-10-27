"""Docker sandbox: one long-running container for the whole pipeline."""

import subprocess

from seo_agents.log import done, log, start

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


def docker_stdout(args):
    proc = subprocess.run(args, capture_output=True, text=True)
    return proc


def create_sandbox():
    begun = start("sandbox boot")
    log("creating local Docker sandbox...")
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
        install_begun = start("installing git/gh in container")
        result = sandbox.run("apt-get update -qq && apt-get install -y -qq git gh curl ca-certificates")
        if not result.success:
            raise RuntimeError(
                "Tool install failed (exit " + str(result.exit_code) + ").\n"
                + result.stdout + result.stderr
                + '\nRe-run by hand to see the full error:\n'
                + 'docker exec ' + CONTAINER + ' bash -c "apt-get update && apt-get install -y git gh curl ca-certificates"'
            )
        done("installing git/gh in container", install_begun)
    done("sandbox boot", begun)
    return sandbox
