"""Host side: prepare the sandbox and run the pipeline inside it.

The host never clones the client repo. It only provisions the container, ships this package
into it, and starts the driver in two separate `docker exec` phases:

1. `analyze` (clone + exploration): no GITHUB_TOKEN. Repository code that runs during an
   optional build runs as the unprivileged `seo-build` user with an allowlisted environment.
2. `publish` (commit + PR): only GITHUB_TOKEN; no repository code runs in this phase.
"""

import os
import re
import shlex
import tomllib
from pathlib import Path

from seo_agents.log import done, log, start

CONTAINER_SRC = "/opt/seo_agents/src"
RUNTIME_MARKER = "/opt/seo_agents/.runtime_ready_agents_v3"
RENDER_MARKER = "/opt/seo_agents/.runtime_ready_render_v1"
SYNC_SUFFIXES = {'.py', '.md', '.json'}

PR_URL_RE = re.compile(r"SEO_PR_URL=(\S+)")


def host_src_dir():
    return Path(__file__).resolve().parents[2]


def project_dependencies(pyproject=None):
    """`[project].dependencies` from pyproject.toml: the single source of truth for the container."""
    path = Path(pyproject) if pyproject else host_src_dir().parent / "pyproject.toml"
    with path.open("rb") as stream:
        return list(tomllib.load(stream)["project"]["dependencies"])


PIP_DEPS = project_dependencies()

# Best-effort optional runtime for building, rendering and Lighthouse. Each step may fail
# (e.g. no network for apt/npm); the pipeline then falls back to source-only lenses.
BROWSERS_PATH = "/opt/ms-playwright"
BUILD_USER = "seo-build"
RENDER_STEPS = [
    ("system packages (node, npm)", "apt-get update -qq && apt-get install -y -qq nodejs npm"),
    ("playwright python package", shlex.join(["python3", "-m", "pip", "install", "--quiet", "--break-system-packages", "playwright>=1.48"])),
    ("chromium for playwright", "PLAYWRIGHT_BROWSERS_PATH=" + BROWSERS_PATH + " python3 -m playwright install --with-deps chromium"),
    ("lighthouse CLI", "npm install -g --silent lighthouse"),
]


def ensure_container_runtime(sandbox):
    if sandbox.run("test -f " + RUNTIME_MARKER).success:
        log("container runtime already provisioned.")
        return
    begun = start("provisioning container runtime (pip install)")
    result = sandbox.run(shlex.join([
        "python3", "-m", "pip", "install", "--quiet", "--break-system-packages", *PIP_DEPS,
    ]))
    if not result.success:
        raise RuntimeError("pip install failed:\n" + result.stdout + result.stderr)
    # Unprivileged identity for install/build/preview of untrusted repository code.
    sandbox.run("id -u " + BUILD_USER + " >/dev/null 2>&1 || useradd -m -s /bin/bash " + BUILD_USER)
    sandbox.run("mkdir -p /opt/seo_agents && touch " + RUNTIME_MARKER)
    done("provisioning container runtime (pip install)", begun)


def render_runtime_requested(env=None):
    env = env if env is not None else os.environ
    return any(str(env.get(key, '')).lower() in {'1', 'true', 'yes'} for key in ('SEO_RENDER', 'SEO_BUILD'))


def ensure_render_runtime(sandbox):
    """Install node/npm, Playwright + Chromium and Lighthouse. Never fails the run."""
    if sandbox.run("test -f " + RENDER_MARKER).success:
        log("render runtime already provisioned.")
        return True
    begun = start("provisioning render runtime (node, playwright, lighthouse)")
    ok = True
    for label, command in RENDER_STEPS:
        result = sandbox.run(command)
        if not result.success:
            ok = False
            log("WARNING: render runtime step failed (" + label + "); continuing with reduced lenses: "
                + (result.stderr or result.stdout or '')[-400:].strip())
    if ok:
        sandbox.run("mkdir -p /opt/seo_agents && touch " + RENDER_MARKER)
    done("provisioning render runtime", begun)
    return ok


def sync_sources(sandbox):
    begun = start("syncing sources into container")
    src = host_src_dir()
    count = 0
    for path in sorted(src.rglob("*")):
        if not path.is_file() or path.suffix not in SYNC_SUFFIXES or "__pycache__" in path.parts:
            continue
        dest = CONTAINER_SRC + "/" + str(path.relative_to(src))
        sandbox.write(dest, path.read_bytes())
        count = count + 1
    pyproject = src.parent / "pyproject.toml"
    if pyproject.is_file():
        sandbox.write("/opt/seo_agents/pyproject.toml", pyproject.read_bytes())
        count += 1
    done("syncing " + str(count) + " source files into container", begun)
    return count


SECRET_FOR_PUBLISH = ("GITHUB_TOKEN", "GH_TOKEN")
PUBLISH_KEYS = ("SEO_PR_COMMIT_FULL", "SEO_PR_COMMIT_BUDGET", "SEO_PR_BRANCH")


def phase_envs(env):
    """(analyze_env, publish_env): the token reaches only the publish phase."""
    analyze = {k: v for k, v in env.items() if k not in SECRET_FOR_PUBLISH}
    analyze.setdefault("PLAYWRIGHT_BROWSERS_PATH", BROWSERS_PATH)
    analyze.setdefault("SEO_BUILD_USER", BUILD_USER)
    publish = {k: env[k] for k in SECRET_FOR_PUBLISH + PUBLISH_KEYS if env.get(k)}
    return analyze, publish


def marker(output, name):
    match = re.search(name + r"=(\S+)", output or "")
    return match.group(1) if match else None


def run_pipeline_in_sandbox(sandbox, github_url, env=None, site_url=None):
    begun = start("full pipeline in sandbox")
    env = dict(env or {})
    ensure_container_runtime(sandbox)
    if render_runtime_requested(env):
        ensure_render_runtime(sandbox)
    sync_sources(sandbox)
    driver = CONTAINER_SRC + "/seo_agents/sandbox/in_container.py"
    analyze_env, publish_env = phase_envs(env)
    log("running analysis inside the sandbox for " + github_url + " (no GitHub token in this phase)")
    command = ["python3", "-u", driver, "analyze", github_url] + ([site_url] if site_url else [])
    result = sandbox.run(shlex.join(command), env=analyze_env, stream=True)
    if not result.success:
        raise RuntimeError("Container analysis failed (exit " + str(result.exit_code) +
                           "); see the streamed output above.")
    bundle = marker(result.stdout, "SEO_BUNDLE")
    if not bundle:
        raise RuntimeError("Analysis finished but no SEO_BUNDLE found; see the streamed output above.")
    log("publishing the PR (token-only phase)")
    result = sandbox.run(shlex.join(["python3", "-u", driver, "publish", github_url, bundle]), env=publish_env, stream=True)
    if not result.success:
        raise RuntimeError("Container publish failed (exit " + str(result.exit_code) +
                           "); see the streamed output above.")
    pr_url = marker(result.stdout, "SEO_PR_URL")
    if not pr_url:
        raise RuntimeError("Pipeline finished but no SEO_PR_URL found; see the streamed output above.")
    done("full pipeline in sandbox", begun)
    return pr_url
