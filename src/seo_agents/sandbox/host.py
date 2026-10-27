"""Host side: prepare the sandbox and run the pipeline inside it.

The host never clones the client repo. It only provisions the
container, ships this package into it, and starts the driver.
"""

import re
from pathlib import Path

from seo_agents.log import done, log, start

CONTAINER_SRC = "/opt/seo_agents/src"
RUNTIME_MARKER = "/opt/seo_agents/.runtime_ready_ast_v1"
PIP_DEPS = ["langchain-core", "langgraph", "langchain-openai", "python-dotenv", "tree-sitter==0.25.2", "tree-sitter-javascript==0.25.0", "tree-sitter-typescript==0.23.2"]

PR_URL_RE = re.compile(r"SEO_PR_URL=(\S+)")


def host_src_dir():
    return Path(__file__).resolve().parents[2]


def ensure_container_runtime(sandbox):
    if sandbox.run("test -f " + RUNTIME_MARKER).success:
        log("container runtime already provisioned.")
        return
    begun = start("provisioning container runtime (pip install)")
    result = sandbox.run("python3 -m pip install --quiet --break-system-packages " + " ".join(PIP_DEPS))
    if not result.success:
        raise RuntimeError("pip install failed:\n" + result.stdout + result.stderr)
    sandbox.run("mkdir -p /opt/seo_agents && touch " + RUNTIME_MARKER)
    done("provisioning container runtime (pip install)", begun)


def sync_sources(sandbox):
    begun = start("syncing sources into container")
    src = host_src_dir()
    count = 0
    for path in sorted(src.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        dest = CONTAINER_SRC + "/" + str(path.relative_to(src))
        sandbox.write(dest, path.read_bytes())
        count = count + 1
    done("syncing " + str(count) + " source files into container", begun)
    return count


def run_pipeline_in_sandbox(sandbox, github_url, env=None):
    begun = start("full pipeline in sandbox")
    ensure_container_runtime(sandbox)
    sync_sources(sandbox)
    driver = CONTAINER_SRC + "/seo_agents/sandbox/in_container.py"
    log("running driver inside the sandbox for " + github_url)
    if env is None:
        env = {}
    result = sandbox.run("python3 " + driver + " " + github_url, env=env)
    print(result.stdout)
    if not result.success:
        raise RuntimeError("Container pipeline failed:\n" + result.stdout + result.stderr)
    match = PR_URL_RE.search(result.stdout)
    if not match:
        raise RuntimeError("Pipeline finished but no SEO_PR_URL found:\n" + result.stdout + result.stderr)
    done("full pipeline in sandbox", begun)
    return match.group(1)
