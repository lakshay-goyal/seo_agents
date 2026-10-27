"""Driver that runs INSIDE the sandbox container.

Started by the host as:
    python3 /opt/seo_agents/src/seo_agents/sandbox/in_container.py <github_url>

Clone -> analyze -> PR, all on the container filesystem.
Prints SEO_PR_URL=<url> at the end for the host to read.
"""

import sys

sys.path.insert(0, "/opt/seo_agents/src")

from seo_agents.log import done, log, start


def main():
    github_url = sys.argv[1]

    from seo_agents.sandbox.clone import clone_repo
    from seo_agents.sandbox.tools.local import LocalRunner
    from seo_agents.sandbox.pr import create_pr
    from seo_agents.exploration.repo_analysis import repo_analysis

    begun = start("in-container pipeline")
    runner = LocalRunner()

    log("stage 1/3: clone")
    target = clone_repo(runner, github_url)

    log("stage 2/3: analyze")
    state = repo_analysis(github_url, repo_path=target)
    artifacts = state.get("artifacts") or {}

    log("stage 3/3: open PR")
    pr_url = create_pr(runner, github_url, reports=artifacts)
    done("in-container pipeline", begun)
    print("SEO_PR_URL=" + pr_url, flush=True)


if __name__ == "__main__":
    main()
