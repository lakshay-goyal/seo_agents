"""Clone the client repo on the runner's machine."""

import shlex

from seo_agents.log import done, start
from seo_agents.sandbox.validate import is_valid_github_url


def repo_folder(github_url):
    name = github_url.rstrip("/").removesuffix(".git").split("/")[-1]
    if name:
        return "/tmp/" + name
    return "/tmp/repo"


def clone_command(github_url, target):
    q_target = shlex.quote(target)
    return ("test -d " + shlex.quote(target + "/.git") + " || git clone --depth 1 -- "
            + shlex.quote(github_url) + " " + q_target)


def clone_repo(runner, github_url, target_dir=None):
    if not is_valid_github_url(github_url):
        raise ValueError("Refusing to clone a non-GitHub or malformed URL: " + repr(github_url))
    target = target_dir or repo_folder(github_url)
    begun = start("clone " + github_url)
    result = runner.run(clone_command(github_url, target))
    if not result.success:
        raise RuntimeError("Clone failed:\n" + result.stdout + result.stderr)
    done("clone " + github_url, begun)
    return target
