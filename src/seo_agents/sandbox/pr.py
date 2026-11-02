"""Open the SEO exploration PR on the client repo.

Runs on the runner's machine (inside the sandbox container):
fork the client repo, commit seo_reports/site_model/**, repo_context.json and any
optional seo_reports/onpage/*.json reports plus a readme, push to the fork, open the PR.
"""

import json
import os
import re
import shlex

from seo_agents.exploration.repo_analysis.tools.helpers import REPORT_FILES
from seo_agents.exploration.repo_analysis.tools.contracts import validate_report
from seo_agents.log import done, log, start
from seo_agents.sandbox.validate import is_valid_github_url

PR_TITLE = "chore(seo-agents): add SEO site model and reports"
PR_BRANCH = "seo-agents/onpage-reports"

PR_BODY = """\
## SEO site model (exploration output)

Adds a page-centric, edit-ready model of this website under `seo_reports/site_model/`:

- `index.json`: compact planning view (framework fingerprint, coverage, one row per URL).
- `site.json`: site-level facts (robots.txt, sitemaps, redirects, headers, i18n config,
  structured data, link graph, duplicates, coverage ledger).
- `pages/<id>.json`: one complete URL each. Every field carries its `value`, the
  `method` that observed it (rendered DOM, raw HTML or static source) and an `edit`
  location (`literal | json | yaml | markdown | i18n | prop | computed | cms | add`
  with file, line and column), so changes can be made from JSON alone.

Rendered and raw-HTML lenses are used when a build/preview or `--site-url` is
available; otherwise values come from static source and say so. Every URL x
category has an explicit coverage state (`measured | source-only | gap | failed`).

`seo_reports/repo_context.json` holds repository-wide deterministic signals with
file/line references. When the optional LLM category agents run, their seven
schema-2.0 reports are also published under `seo_reports/onpage/` (legacy output).
No production code is changed.
"""

GIT_USER_NAME = "seo-agents-bot"
GIT_USER_EMAIL = "seo-agents-bot@users.noreply.github.com"

README_CONTENT = """\
# SEO exploration output

## `seo_reports/site_model/` (primary)

- `index.json`: planning view; load this first (one row per URL, coverage counts).
- `site.json`: site-wide facts (robots, sitemaps, redirects, headers, i18n, link graph).
- `pages/<id>.json`: everything known about one URL. Each value has
  `{value, method, edit, confidence}`; `edit.kind` is one of
  `literal | json | yaml | markdown | i18n | prop | computed | cms | add`.
  `computed`/`cms` mean a source edit alone may not change the value.

Methods: `rendered` (browser DOM), `raw-html` (HTTP response without JS) or
`source-*` (static analysis). Absence of a value is only meaningful when the
page's coverage state for that field is `measured`.

## `seo_reports/repo_context.json`

Deterministic repository signals (routes, rendering hints, crawl control, security
headers, structured data, i18n, performance causes) with file/line references.

## `seo_reports/onpage/` (optional, legacy)

Seven schema-2.0 category reports produced by the LLM extraction agents when they
run. Analyze them offline with:

```sh
python -m seo_agents.exploration.repo_analysis.json_analysis seo_reports/onpage
```
"""


def split_github_url(github_url):
    if not is_valid_github_url(github_url):
        raise ValueError("Not a valid GitHub repository URL: " + repr(github_url))
    clean = github_url.strip().removesuffix("/").removesuffix(".git")
    parts = clean.split("/")
    owner = parts[-2]
    repo = parts[-1]
    return owner, repo, "/tmp/" + repo


def login_env():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("Set GITHUB_TOKEN in .env first (classic PAT with the `repo` scope).")
    return {"GITHUB_TOKEN": token, "GH_TOKEN": token}


def check_reports(reports, *, require_any=True):
    """Validate whichever category reports exist; none are mandatory."""
    if reports is None:
        reports = {}
    if not isinstance(reports, dict):
        raise ValueError("PR publication requires an in-memory reports dictionary")
    unknown = sorted(set(reports) - set(REPORT_FILES))
    if unknown:
        raise ValueError("Unknown report categories: " + str(unknown))
    for category, report in reports.items():
        validate_report(category, report)
    if require_any and not reports:
        raise RuntimeError("Nothing to publish: no reports and no extra files")
    return reports


def run_or_fail(runner, env, command):
    result = runner.run(command, env=env)
    if not result.success:
        raise RuntimeError("FAILED: " + command + "\n" + result.stdout + result.stderr)
    return result.stdout.strip()


def safe_relative(relative):
    if not relative or relative.startswith("/") or ".." in relative.split("/") or "\0" in relative:
        raise ValueError("extra file path must stay inside the repository: " + repr(relative))
    return relative


def create_pr(runner, github_url, reports=None, extra_files=None):
    """reports: optional {category: report}; extra_files: {repo-relative path: text} (e.g. site model)."""
    begun = start("PR on " + github_url)
    owner, repo, target = split_github_url(github_url)
    extra_files = {safe_relative(path): content for path, content in (extra_files or {}).items()}
    reports = check_reports(reports, require_any=not extra_files)
    # Serialize every payload before any GitHub or filesystem mutation.
    payloads = {
        "seo_reports/onpage/" + REPORT_FILES[key]: json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        for key, report in reports.items()
    }
    env = login_env()
    q = shlex.quote
    q_target = q(target)
    slug = q(owner + "/" + repo)
    log("target checkout: " + target)

    run_or_fail(runner, env, "git --version")
    run_or_fail(runner, env, "gh --version || (apt-get update -qq && apt-get install -y -qq gh)")
    run_or_fail(runner, env, "test -d " + q(target + "/.git") + " || git clone --depth 1 -- " + q(github_url) + " " + q_target)
    log("checkout ready: " + target)

    runner.run("gh repo fork " + slug + " --clone=false", env=env)
    fork_owner = run_or_fail(runner, env, "gh api user -q .login")
    base = run_or_fail(runner, env, "gh api " + q("repos/" + owner + "/" + repo) + " -q .default_branch")
    log("forked as " + fork_owner + ", base branch " + base)

    run_or_fail(runner, env, "cd " + q_target + " && git fetch origin -q && git checkout -B " + q(PR_BRANCH) + " " + q("origin/" + base))
    run_or_fail(runner, env, "cd " + q_target + " && git config user.name " + q(GIT_USER_NAME) + " && git config user.email " + q(GIT_USER_EMAIL))
    runner.write(target + "/.seo_agents/readme.md", README_CONTENT)
    log("branch ready: " + PR_BRANCH)

    added = [".seo_agents/readme.md"]
    for relative, content in {**payloads, **extra_files}.items():
        runner.write(target + "/" + relative, content)
        added.append(relative)
    log("staged " + str(len(added) - 1) + " report files")
    run_or_fail(runner, env, "cd " + q_target + " && git add -- " + " ".join(q(path) for path in added))
    if run_or_fail(runner, env, "cd " + q_target + " && git status --porcelain"):
        run_or_fail(runner, env, "cd " + q_target + " && git commit -q -m " + q(PR_TITLE))
        log("committed " + str(len(added)) + " files")

    push_url = "https://x-access-token:${GITHUB_TOKEN}@github.com/" + fork_owner + "/" + repo + ".git"
    # Double quotes so bash expands only ${GITHUB_TOKEN}; owner/repo are validated identifiers.
    if not re.fullmatch(r"[A-Za-z0-9-]+", fork_owner):
        raise RuntimeError("Unexpected GitHub login: " + repr(fork_owner))
    run_or_fail(runner, env, "cd " + q_target + ' && git push --force "' + push_url + '" ' + q(PR_BRANCH))
    log("pushed branch " + PR_BRANCH)

    head = PR_BRANCH if fork_owner == owner else fork_owner + ":" + PR_BRANCH
    already = run_or_fail(
        runner, env,
        "gh pr list --repo " + slug + " --head " + q(head) + " --json url -q " + q(".[0].url"),
    )
    if already and already != "null":
        log("PR already exists: " + already)
        done("PR on " + github_url, begun)
        return already
    pr_url = run_or_fail(
        runner, env,
        "gh pr create --repo " + slug + " --base " + q(base)
        + " --head " + q(head) + " --title " + q(PR_TITLE) + " --body " + q(PR_BODY),
    )
    log("opened PR: " + pr_url)
    done("PR on " + github_url, begun)
    return pr_url
