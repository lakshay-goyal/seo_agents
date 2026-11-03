"""Driver that runs INSIDE the sandbox container.

Started by the host in two separate phases so the GitHub token never coexists with repository code:

    python3 in_container.py analyze <github_url> [site_url]   # clone + exploration; prints SEO_BUNDLE=<path>
    python3 in_container.py publish <github_url> <bundle>     # commit + PR;          prints SEO_PR_URL=<url>

`python3 in_container.py <github_url> [site_url]` still runs both phases in one process (local use).
"""

import json
import os
from pathlib import Path
import sys

sys.path.insert(0, "/opt/seo_agents/src")

from seo_agents.log import done, log, start

BUNDLE_DIR = "/opt/seo_agents/out"


def directory_files(directory, prefix):
    """{<prefix>/<relative>: text} for every text file under directory."""
    if not directory:
        return {}
    root = Path(directory)
    if not root.is_dir():
        return {}
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            try:
                files[prefix + "/" + path.relative_to(root).as_posix()] = path.read_text()
            except UnicodeDecodeError:
                continue
    return files


def site_model_files(directory):
    """{seo_reports/site_model/<relative>: text} for every file the site model wrote."""
    return directory_files(directory, "seo_reports/site_model")


def output_files(state, target):
    """Everything the exploration wrote (site model, sharded exploration output, on-page proposals, repo context)."""
    files = site_model_files(state.get("site_model_dir"))
    for key, prefix in (("exploration_dir", "seo_reports/exploration"), ("onpage_dir", "seo_reports/onpage")):
        files.update(directory_files(state.get(key), prefix))
    exploration = Path(target) / "seo_reports" / "exploration"
    if not state.get("exploration_dir") and exploration.is_dir():
        files.update(directory_files(exploration, "seo_reports/exploration"))
    if state.get("repo_context_path"):
        with open(state["repo_context_path"]) as stream:
            files["seo_reports/repo_context.json"] = stream.read()
    return files


def analyze(github_url, site_url=None):
    from seo_agents.sandbox.clone import clone_repo, head_sha, repo_folder
    from seo_agents.sandbox.tools.local import LocalRunner
    from seo_agents.sandbox.pr import commit_plan, scrub_paths, summary_markdown
    from seo_agents.exploration.repo_analysis import repo_analysis

    runner = LocalRunner()
    log("stage 1/2: clone (fresh)")
    target = clone_repo(runner, github_url)
    sha = head_sha(runner, target)
    log("analysed commit: " + str(sha))
    log("stage 2/2: analyze" + (" (site_url=" + site_url + ")" if site_url else ""))
    state = repo_analysis(github_url, repo_path=target, site_url=site_url)
    files = {path: scrub_paths(text, (target, str(Path(target).resolve())))
             for path, text in output_files(state, target).items()}
    committed, excluded = commit_plan(files)
    if excluded:
        log("not committing " + str(len(excluded)) + " large files (set SEO_PR_COMMIT_FULL=1 to commit everything)")
    bundle = {"github_url": github_url, "sha": sha, "files": committed, "excluded": excluded,
              "summary": summary_markdown({**files}), "reports": state.get("artifacts") or {}}
    Path(BUNDLE_DIR).mkdir(parents=True, exist_ok=True)
    path = Path(BUNDLE_DIR) / (Path(repo_folder(github_url)).name + "-" + (sha or "unknown")[:12] + ".json")
    path.write_text(json.dumps(bundle, ensure_ascii=False))
    os.chmod(path, 0o600)
    return str(path)


def publish(github_url, bundle_path):
    from seo_agents.sandbox.tools.local import LocalRunner
    from seo_agents.sandbox.pr import create_pr

    bundle = json.loads(Path(bundle_path).read_text())
    if bundle.get("github_url") != github_url:
        raise SystemExit("Bundle was produced for a different repository")
    log("open PR (" + str(len(bundle["files"])) + " files committed, " + str(len(bundle.get("excluded") or {}))
        + " left out)")
    return create_pr(LocalRunner(), github_url, reports=bundle.get("reports") or {}, extra_files=bundle["files"],
                     base_sha=bundle.get("sha"), summary=bundle.get("summary"), excluded=bundle.get("excluded"))


def main(argv=None):
    from seo_agents.sandbox.validate import is_valid_github_url
    argv = list(sys.argv[1:] if argv is None else argv)
    phase = argv.pop(0) if argv and argv[0] in {"analyze", "publish"} else "all"
    if not argv:
        raise SystemExit("usage: in_container.py [analyze|publish] <github_url> [site_url|bundle]")
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


if __name__ == "__main__":
    main()
