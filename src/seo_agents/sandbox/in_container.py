"""Driver that runs INSIDE the sandbox container.

Started by the host as:
    python3 /opt/seo_agents/src/seo_agents/sandbox/in_container.py <github_url> [site_url]

Clone -> analyze -> PR, all on the container filesystem.
Prints SEO_PR_URL=<url> at the end for the host to read.
"""

import os
from pathlib import Path
import sys

sys.path.insert(0, "/opt/seo_agents/src")

from seo_agents.log import done, log, start


def site_model_files(directory):
    """{seo_reports/site_model/<relative>: text} for every file the site model wrote."""
    if not directory:
        return {}
    root = Path(directory)
    if not root.is_dir():
        return {}
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            files["seo_reports/site_model/" + path.relative_to(root).as_posix()] = path.read_text()
    return files


def main():
    github_url = sys.argv[1]
    site_url = (sys.argv[2] if len(sys.argv) > 2 else None) or os.environ.get("SEO_SITE_URL") or None

    from seo_agents.sandbox.clone import clone_repo
    from seo_agents.sandbox.tools.local import LocalRunner
    from seo_agents.sandbox.pr import create_pr
    from seo_agents.sandbox.validate import is_valid_github_url
    from seo_agents.exploration.repo_analysis import repo_analysis

    if not is_valid_github_url(github_url):
        raise SystemExit("Refusing malformed GitHub URL: " + repr(github_url))

    begun = start("in-container pipeline")
    runner = LocalRunner()

    log("stage 1/3: clone")
    target = clone_repo(runner, github_url)

    log("stage 2/3: analyze" + (" (site_url=" + site_url + ")" if site_url else ""))
    state = repo_analysis(github_url, repo_path=target, site_url=site_url)
    artifacts = state.get("artifacts") or {}
    extra_files = site_model_files(state.get("site_model_dir"))
    if state.get("repo_context_path"):
        with open(state["repo_context_path"]) as stream:
            extra_files["seo_reports/repo_context.json"] = stream.read()

    log("stage 3/3: open PR (" + str(len(artifacts)) + " reports, " + str(len(extra_files)) + " files)")
    pr_url = create_pr(runner, github_url, reports=artifacts, extra_files=extra_files)
    done("in-container pipeline", begun)
    print("SEO_PR_URL=" + pr_url, flush=True)


if __name__ == "__main__":
    main()
