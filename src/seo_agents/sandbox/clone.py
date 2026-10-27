"""Clone the client repo on the runner's machine."""


def repo_folder(github_url):
    name = github_url.rstrip("/").removesuffix(".git").split("/")[-1]
    if name:
        return "/tmp/" + name
    return "/tmp/repo"


def clone_repo(runner, github_url, target_dir=None):
    if target_dir:
        target = target_dir
    else:
        target = repo_folder(github_url)
    print("Cloning " + github_url + " into " + target, flush=True)
    result = runner.run("test -d " + target + "/.git || git clone --depth 1 " + github_url + " " + target)
    if not result.success:
        raise RuntimeError("Clone failed:\n" + result.stdout + result.stderr)
    print("Cloned into " + target, flush=True)
    return target
