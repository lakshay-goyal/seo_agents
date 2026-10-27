"""Driver that runs INSIDE the sandbox container.

Started by the host as:
    python3 /opt/seo_agents/src/seo_agents/sandbox/in_container.py <github_url>

Clone -> analyze (in memory) -> PR, all on the container filesystem.
Prints SEO_PR_URL=<url> at the end for the host to read.
"""

import sys


def main():
    sys.path.insert(0, "/opt/seo_agents/src")

    github_url = sys.argv[1]

    from seo_agents.sandbox.clone import clone_repo
    from seo_agents.sandbox.tools.local import LocalRunner
    from seo_agents.sandbox.pr import create_pr
    from seo_agents.exploration.repo_analysis import repo_analysis

    runner = LocalRunner()
    target = clone_repo(runner, github_url)

    state = repo_analysis(github_url, repo_path=target)
    artifacts = state.get("artifacts") or {}
    print("On-page reports in memory: " + str(sorted(artifacts)), flush=True)

    pr_url = create_pr(runner, github_url, reports=artifacts)
    print("SEO_PR_URL=" + pr_url, flush=True)


if __name__ == "__main__":
    main()
