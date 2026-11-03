"""Clone the client repo on the runner's machine.

Every run clones fresh into a directory keyed by owner AND repo, so two owners' repositories with
the same name never share a checkout, and a stale earlier clone is never analysed. The analysed
commit SHA is recorded so the PR is based on exactly what was measured.
"""

import re
import shlex

from seo_agents.log import done, start
from seo_agents.sandbox.validate import is_valid_github_url

CLONE_ROOT = "/tmp/seo-agents"


def owner_repo(github_url):
    clean = github_url.strip().rstrip("/").removesuffix(".git")
    owner, repo = clean.split("/")[-2:]
    return owner, repo


def repo_folder(github_url):
    owner, repo = owner_repo(github_url)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", owner + "__" + repo)
    return CLONE_ROOT + "/" + (safe or "repo")


def clone_command(github_url, target):
    q_target = shlex.quote(target)
    return ("rm -rf -- " + q_target + " && mkdir -p -- " + shlex.quote(target.rsplit("/", 1)[0] or "/")
            + " && git clone --depth 1 -- " + shlex.quote(github_url) + " " + q_target)


def head_sha(runner, target):
    result = runner.run("git -C " + shlex.quote(target) + " rev-parse HEAD")
    sha = (result.stdout or "").strip() if result.success else ""
    return sha if re.fullmatch(r"[0-9a-f]{40}", sha) else None


def clone_repo(runner, github_url, target_dir=None):
    """Fresh clone; returns the checkout path. Use `head_sha(runner, path)` for the analysed commit."""
    if not is_valid_github_url(github_url):
        raise ValueError("Refusing to clone a non-GitHub or malformed URL: " + repr(github_url))
    target = target_dir or repo_folder(github_url)
    begun = start("clone " + github_url)
    result = runner.run(clone_command(github_url, target))
    if not result.success:
        raise RuntimeError("Clone failed:\n" + result.stdout + result.stderr)
    done("clone " + github_url, begun)
    return target
