def clone_repo(sandbox, github_url: str, target_dir: str | None = None) -> str:
    """Clone the repo inside an already-running sandbox."""
    target = target_dir or f"/tmp/{github_url.rstrip('/').removesuffix('.git').split('/')[-1] or 'repo'}"
    print(f"Cloning {github_url} into sandbox:{target}")
    sandbox.run(f"git clone {github_url} {target}")
    print(f"Cloned into sandbox:{target}")
    return target
