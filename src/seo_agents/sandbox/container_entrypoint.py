"""Driver that runs INSIDE the sandbox container.

Started by the host in two separate phases so the GitHub token never coexists with repository code:

    python3 container_entrypoint.py analyze <github_url> [site_url]   # orchestrator.py pipeline; prints SEO_BUNDLE=<path>
    python3 container_entrypoint.py publish <github_url> <bundle>     # commit + PR;          prints SEO_PR_URL=<url>

`python3 container_entrypoint.py <github_url> [site_url]` still runs both phases in one process (local use).
"""

import json
import os
from pathlib import Path
import sys

sys.path.insert(0, "/opt/seo_agents/src")

from seo_agents.sandbox.report_bundle import BUNDLE_DIR, directory_files, site_model_files  # noqa: F401 - re-exported
from seo_agents.utils.step_logger import done, log, start


def analyze(github_url, site_url=None):
    """Clone, install, explore, keywords, on-page + technical, bundle: see seo_agents/orchestrator.py."""
    from seo_agents.orchestrator import run_pipeline

    state = run_pipeline(github_url, site_url)
    return state.bundle_path


def publish(github_url, bundle_path):
    from seo_agents.sandbox.runners.local_runner import LocalRunner
    from seo_agents.sandbox.git.pull_request import PullRequestPublisher

    bundle = json.loads(Path(bundle_path).read_text())
    if bundle.get("github_url") != github_url:
        raise SystemExit("Bundle was produced for a different repository")
    log("open PR (" + str(len(bundle["files"])) + " files committed, " + str(len(bundle.get("excluded") or {}))
        + " left out)")
    return PullRequestPublisher().create(LocalRunner(), github_url, reports=bundle.get("reports") or {}, extra_files=bundle["files"],
                                         base_sha=bundle.get("sha"), summary=bundle.get("summary"),
                                         excluded=bundle.get("excluded"))


def main(argv=None):
    from seo_agents.sandbox.github_url_validation import is_valid_github_url
    argv = list(sys.argv[1:] if argv is None else argv)
    phase = argv.pop(0) if argv and argv[0] in {"analyze", "publish"} else "all"
    if not argv:
        raise SystemExit("usage: container_entrypoint.py [analyze|publish] <github_url> [site_url|bundle]")
    github_url = argv[0]
    if not is_valid_github_url(github_url):
        raise SystemExit("Refusing malformed GitHub URL: " + repr(github_url))
    begun = start("in-container " + phase)
    if phase == "publish":
        if len(argv) < 2:
            raise SystemExit("publish needs the bundle path printed by analyze")
        print("SEO_PR_URL=" + publish(github_url, argv[1]), flush=True)
    else:
        site_url = (argv[1] if len(argv) > 1 else None) or os.environ.get("SEO_SITE_URL") or None
        bundle = analyze(github_url, site_url)
        print("SEO_BUNDLE=" + bundle, flush=True)
        if phase == "all":
            print("SEO_PR_URL=" + publish(github_url, bundle), flush=True)
    done("in-container " + phase, begun)


class ContainerEntrypoint:
    """Container side: `analyze` (the orchestrator pipeline, writes a bundle) and `publish` (PR from the bundle)."""

    def analyze(self, github_url, site_url=None):
        return analyze(github_url, site_url)

    def publish(self, github_url, bundle_path):
        return publish(github_url, bundle_path)


if __name__ == "__main__":
    main()
