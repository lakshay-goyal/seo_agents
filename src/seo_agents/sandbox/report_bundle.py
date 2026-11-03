"""Collect everything the pipeline wrote under seo_reports/ and save it as one bundle JSON for the publish phase.

The analyze phase (no GitHub token) writes the bundle; the publish phase (token only) reads it and opens the PR.
"""

import json
import os
from pathlib import Path

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


def report_files(repo_path, *, site_model_dir=None, keywords_dir=None, onpage_dir=None, repo_context_path=None):
    """Every report file to commit: site model, exploration, keywords, on-page proposals and repo_context.json."""
    files = site_model_files(site_model_dir)
    files.update(directory_files(Path(repo_path) / "seo_reports" / "exploration", "seo_reports/exploration"))
    files.update(directory_files(keywords_dir, "seo_reports/keywords"))
    files.update(directory_files(onpage_dir, "seo_reports/onpage"))
    if repo_context_path:
        files["seo_reports/repo_context.json"] = Path(repo_context_path).read_text()
    return files


def write_bundle(github_url, sha, repo_path, files, legacy_reports=None, bundle_dir=BUNDLE_DIR):
    """Scrub local paths, choose what fits in the PR, write the bundle JSON (mode 600). Returns its path."""
    from seo_agents.sandbox.git.pull_request import PullRequestPublisher, scrub_paths
    from seo_agents.utils.step_logger import log

    local_paths = (str(repo_path), str(Path(repo_path).resolve()))
    files = {path: scrub_paths(text, local_paths) for path, text in files.items()}
    publisher = PullRequestPublisher()
    committed, excluded = publisher.commit_plan(files)
    if excluded:
        log("not committing " + str(len(excluded)) + " large files (set SEO_PR_COMMIT_FULL=1 to commit everything)")
    bundle = {"github_url": github_url, "sha": sha, "files": committed, "excluded": excluded,
              "summary": publisher.summary_markdown(files), "reports": legacy_reports or {}}
    Path(bundle_dir).mkdir(parents=True, exist_ok=True)
    path = Path(bundle_dir) / (Path(repo_path).name + "-" + (sha or "unknown")[:12] + ".json")
    path.write_text(json.dumps(bundle, ensure_ascii=False))
    os.chmod(path, 0o600)
    return str(path)
