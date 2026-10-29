"""Open the on-page SEO reports PR on the client repo.

Runs on the runner's machine (inside the sandbox container):
fork the client repo, commit seo_reports/onpage/*.json plus a readme,
push to the fork, open the PR.
"""

import json
import os
import shlex

from seo_agents.exploration.checks.tools.helpers import REPORT_FILES
from seo_agents.exploration.checks.tools.contracts import validate_report
from seo_agents.log import done, log, start

PR_TITLE = "chore(seo-agents): add on-page SEO reports"
PR_BRANCH = "seo-agents/onpage-reports"

PR_BODY = """\
## On-page SEO inventories (schema 2.0)

Adds seven category-specific JSON files under `seo_reports/onpage/` with page
routes, source references and explicit extraction coverage:

- `content.json`: full observed literal text, UI labels and link/media references.
- `page_structure.json`: heading hierarchy and semantic landmarks.
- `internal_links.json`: per-page anchors and normalized destinations.
- `metadata.json`: metadata values and ancestor layout declarations.
- `media.json`: sources, alt, local bytes/dimensions and loading behavior.
- `urls.json`: route patterns, depths and query links.
- `eeat.json`: candidate trust evidence, policy links and possible citations.

`.seo_agents/readme.md` documents the contract and JSON-only analysis command.
These are static source observations. Runtime data, component props and dynamic
route instances remain unresolved; coverage fields explain the gaps. No production
code is changed. This schema replaces the earlier count/excerpt format.

In agent mode, each category uses its own skill and repository-specific extraction
script, accepted only after bounded execution, schema/source checks and a repeated
run. Extraction metadata records script, skill and source snapshot hashes.
"""

GIT_USER_NAME = "seo-agents-bot"
GIT_USER_EMAIL = "seo-agents-bot@users.noreply.github.com"

README_CONTENT = """\
# On-page SEO inventory (schema 2.0)

Generated under `seo_reports/onpage/`. Each file has a category, extraction
coverage and pages identified by route URL and source template. Evidence includes
source file, line and source_id. Relative hrefs remain intact; target_url resolves
relative internal destinations against their page route. No production domain is
invented. Absolute links remain unclassified without an authoritative site origin.

- content.json: full literal text fragments, UI labels, word counts and references
  to corresponding link/media records. Text is not limited to an excerpt.
- page_structure.json: heading text/levels and semantic landmarks.
- internal_links.json: anchors, original hrefs, normalized destinations, rel/target.
- metadata.json: exact HTML declarations and literal title/description exports,
  including ancestor layout declarations. Runtime merge semantics remain unknown.
- media.json: per-page image/video/audio/source/track/iframe declarations, alt,
  source, local bytes, PNG/GIF/JPEG/SVG dimensions when readable, declared size,
  responsive attributes, preload/priority and loading defaults.
- urls.json: route templates, dynamic patterns, depths and query links.
- eeat.json: visible candidate evidence for authorship, experience, expertise,
  trust, reviews and dates, plus policy links and possible external citations.
  Candidates do not verify credentials, authenticity or reputation.

## Coverage

This is a static inventory, not a rendered browser crawl. Local components and
framework routing are explored by category agents in the default mode. Their
generated scripts must pass bounded execution, schema/source checks and a repeated
run before report acceptance; extraction metadata records verification hashes.
Coverage and framework semantics depend on the generated extractor and are not
proof of complete runtime coverage. The following details describe legacy static
mode: local components and
ancestor Next.js layouts are followed. Literal constants and local asset imports
are resolved. Vite mount entries, same-file components, supported props/maps/pathname routing,
local public-file fetches and object-valued click states are resolved through syntax
trees. Unsupported expressions, aliases, nested routing and remote APIs/CMS still
need further resolution. Check each page's coverage before interpreting absence.
Source order per file is retained; composed rendered order is not established.
Images outside public/ or literal imports may be unresolved. Remote bytes and
optimized transfer bytes are unknown. The 200 KiB source-size budget is a review
heuristic; displayed dimensions and placement are needed to judge undersizing.
Empty alt may correctly identify a decorative image. E-E-A-T relevance depends
on the purpose of the page; missing candidate signals are not universal failures.

## Analyze saved JSON only

With seo-agents installed, run:

```sh
python -m seo_agents.exploration.checks.json_analysis seo_reports/onpage
```

This emits evidence-linked review findings without reading application source,
calling an LLM or accessing the network. No aggregate-only AI summary is embedded
in the inventory. The schema is incompatible with the old count/excerpt reports.
"""


def split_github_url(github_url):
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


def check_reports(reports):
    if not isinstance(reports, dict):
        raise ValueError("PR publication requires the complete in-memory reports dictionary")
    missing = []
    for key in REPORT_FILES:
        if key not in reports:
            missing.append(key)
    if missing:
        raise RuntimeError("reports is missing checks: " + str(missing))
    for category in REPORT_FILES:
        validate_report(category, reports[category])


def run_or_fail(runner, env, command):
    result = runner.run(command, env=env)
    if not result.success:
        raise RuntimeError("FAILED: " + command + "\n" + result.stdout + result.stderr)
    return result.stdout.strip()


def create_pr(runner, github_url, reports):
    begun = start("PR on " + github_url)
    owner, repo, target = split_github_url(github_url)
    check_reports(reports)
    # Serialize every payload before any GitHub or filesystem mutation.
    payloads = {
        filename: json.dumps(reports[key], indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        for key, filename in REPORT_FILES.items()
    }
    env = login_env()
    log("target checkout: " + target)

    run_or_fail(runner, env, "git --version")
    run_or_fail(runner, env, "gh --version || (apt-get update -qq && apt-get install -y -qq gh)")
    run_or_fail(runner, env, "test -d " + target + "/.git || git clone --depth 1 " + github_url + " " + target)
    log("checkout ready: " + target)

    runner.run("gh repo fork " + owner + "/" + repo + " --clone=false", env=env)
    fork_owner = run_or_fail(runner, env, "gh api user -q .login")
    base = run_or_fail(runner, env, "gh api repos/" + owner + "/" + repo + " -q .default_branch")
    log("forked as " + fork_owner + ", base branch " + base)

    run_or_fail(runner, env, "cd " + target + " && git fetch origin -q && git checkout -B " + PR_BRANCH + " origin/" + base)
    run_or_fail(runner, env, "cd " + target + " && git config user.name '" + GIT_USER_NAME + "' && git config user.email '" + GIT_USER_EMAIL + "'")
    runner.write(target + "/.seo_agents/readme.md", README_CONTENT)
    log("branch ready: " + PR_BRANCH)

    added = [".seo_agents/readme.md"]
    for filename, content in payloads.items():
        runner.write(target + "/seo_reports/onpage/" + filename, content)
        added.append("seo_reports/onpage/" + filename)
    log("staged " + str(len(REPORT_FILES)) + " report files")
    run_or_fail(runner, env, "cd " + target + " && git add " + " ".join(added))
    if run_or_fail(runner, env, "cd " + target + " && git status --porcelain"):
        run_or_fail(runner, env, "cd " + target + " && git commit -q -m '" + PR_TITLE + "'")
        log("committed " + str(len(added)) + " files")

    push_cmd = (
        "cd " + target + ' && git push --force "https://x-access-token:${GITHUB_TOKEN}@github.com/'
        + fork_owner + "/" + repo + '.git" ' + PR_BRANCH
    )
    run_or_fail(runner, env, push_cmd)
    log("pushed branch " + PR_BRANCH)

    if fork_owner == owner:
        head = PR_BRANCH
    else:
        head = fork_owner + ":" + PR_BRANCH
    already = run_or_fail(
        runner, env,
        "gh pr list --repo " + owner + "/" + repo + " --head " + head + ' --json url -q ".[0].url"',
    )
    if already and already != "null":
        log("PR already exists: " + already)
        done("PR on " + github_url, begun)
        return already
    pr_url = run_or_fail(
        runner, env,
        "gh pr create --repo " + owner + "/" + repo + " --base " + base
        + " --head " + head + " --title " + shlex.quote(PR_TITLE) + " --body " + shlex.quote(PR_BODY),
    )
    log("opened PR: " + pr_url)
    done("PR on " + github_url, begun)
    return pr_url
