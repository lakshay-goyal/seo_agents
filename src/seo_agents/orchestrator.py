"""SEO pipeline orchestrator: the whole analysis run, one step after another. Read this file top to bottom.

Runs INSIDE the sandbox container (started by sandbox/container_entrypoint.py, `analyze` phase, no GitHub token).
The PR is opened afterwards by the separate `publish` phase, which only reads the bundle written by step 6.

    START
      -> clone        1. clone the GitHub repository into the sandbox
      -> install      2. install the repository's own dependencies (only with --build and no --site-url)
      -> explore      3. explore the repository -> seo_reports/*.json (incl. Lighthouse / Web Vitals measurements)
      -> keywords     4. keyword research for every page; the Jev judge accepts or rejects each keyword
      -> onpage       5. on-page content + technical SEO proposals (performance, security, crawlability, ...)
      -> bundle       6. collect every report into one bundle JSON for the publish phase
    END

Every step is a plain function: it receives the PipelineState, calls ONE workflow or tool, and returns only the
fields it changed. Steps 1-3 and 6 must succeed (an exception stops the run). Steps 4-5 are allowed to fail: the
error is recorded in `errors` and the exploration reports are still published.

Why on-page and technical SEO are one step: both are checks of the same OnpageAnalysisWorkflow, whose final QA
check removes duplicates across ALL checks and writes one seo_reports/onpage/ folder. Keyword research runs first
because the on-page `keyword_plan` check reads seo_reports/keywords/keyword_map.json.
"""

from dataclasses import dataclass, field
import os
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from seo_agents.utils.step_logger import done, log, start


# ---------------------------------------------------------------- state shared by every step
@dataclass
class PipelineState:
    # inputs
    github_url: str
    site_url: str | None = None          # deployed site to measure instead of building the repository
    build: bool = False                  # install + build + serve the repository inside the sandbox
    render: bool = False                 # render pages with Playwright and run Lighthouse
    # step 1: clone
    repo_path: str | None = None
    commit_sha: str | None = None
    # step 3: explore
    reports_dir: str | None = None       # <repo>/seo_reports
    site_model_dir: str | None = None
    repo_context_path: str | None = None
    legacy_reports: dict = field(default_factory=dict)
    # step 4: keywords
    keywords_dir: str | None = None
    # step 5: on-page + technical
    onpage_dir: str | None = None
    # step 6: bundle
    bundle_path: str | None = None
    # failures of the optional steps (4, 5)
    errors: list = field(default_factory=list)


# ---------------------------------------------------------------- steps
def clone_repository(state):
    """Step 1: fresh clone of the repository; remember the analysed commit."""
    from seo_agents.sandbox.git.repository_clone import RepositoryCloner
    from seo_agents.sandbox.runners.local_runner import LocalRunner

    cloner = RepositoryCloner()
    runner = LocalRunner()
    repo_path = cloner.clone(runner, state.github_url)
    commit_sha = cloner.head_sha(runner, repo_path)
    log("analysed commit: " + str(commit_sha))
    return {"repo_path": repo_path, "commit_sha": commit_sha}


def install_dependencies(state):
    """Step 2: install the repository's dependencies (npm/pnpm/yarn/bun) as the unprivileged build user.

    Only needed when step 3 builds the site itself. A failed install does not stop the run: step 3 then
    analyses the source only and records why.
    """
    if state.site_url:
        log("skipped: measuring the deployed site " + state.site_url + ", nothing to install")
        return {}
    if not state.build:
        log("skipped: building is off (pass --build to install and build the repository)")
        return {}

    from seo_agents.repo_analysis.measurement.site_server import SiteServer
    from seo_agents.repo_analysis.tools.repository.framework_detection import FrameworkDetector
    from seo_agents.repo_analysis.tools.repository.repository_snapshot import RepositorySnapshotTool

    snapshot = RepositorySnapshotTool().capture(state.repo_path)
    fingerprint = FrameworkDetector().detect(snapshot)
    ok, install_log, limitations = SiteServer().install(state.repo_path, fingerprint)
    for line in install_log + limitations:
        log("install: " + line)
    log("dependencies installed" if ok else "dependency install failed; exploration will be source-only")
    return {}


def explore_repository(state):
    """Step 3: run every exploration agent and the site model; writes JSON files under <repo>/seo_reports/."""
    from seo_agents.repo_analysis.repo_analysis import RepoAnalysisWorkflow

    workflow = RepoAnalysisWorkflow(state.github_url, repo_path=state.repo_path, site_url=state.site_url,
                                    build=state.build, render=state.render)
    result = workflow.run()
    site_model_dir = result.get("site_model_dir")
    reports_dir = Path(site_model_dir).parent if site_model_dir else Path(state.repo_path) / "seo_reports"
    return {"reports_dir": str(reports_dir),
            "site_model_dir": site_model_dir,
            "repo_context_path": result.get("repo_context_path"),
            "legacy_reports": result.get("artifacts") or {}}


def research_keywords(state):
    """Step 4: generate, measure and judge (Jev) keywords for every page -> seo_reports/keywords/."""
    from seo_agents.keyword_research.keyword_research import KeywordResearchWorkflow

    try:
        result = KeywordResearchWorkflow().run(state.reports_dir)
    except Exception as error:  # noqa: BLE001 - optional step: the exploration PR still opens
        return {"errors": state.errors + [failure("keywords", error)]}
    keywords_dir = (result.get("written") or {}).get("out_dir") or result.get("out_dir")
    return {"keywords_dir": keywords_dir}


def improve_onpage_and_technical_seo(state):
    """Step 5: on-page content and technical SEO checks for every page -> seo_reports/onpage/."""
    from seo_agents.onpage.onpage_analysis import OnpageAnalysisWorkflow

    try:
        result = OnpageAnalysisWorkflow().run(state.reports_dir, site_url=state.site_url)
    except Exception as error:  # noqa: BLE001 - optional step: the exploration PR still opens
        return {"errors": state.errors + [failure("onpage", error)]}
    return {"onpage_dir": result.get("out_dir")}


def write_bundle(state):
    """Step 6: collect every report file into one bundle JSON that the publish phase turns into a PR."""
    from seo_agents.sandbox.report_bundle import report_files, write_bundle as save_bundle

    files = report_files(state.repo_path, site_model_dir=state.site_model_dir, keywords_dir=state.keywords_dir,
                         onpage_dir=state.onpage_dir, repo_context_path=state.repo_context_path)
    bundle_path = save_bundle(state.github_url, state.commit_sha, state.repo_path, files, state.legacy_reports)
    return {"bundle_path": bundle_path}


# ---------------------------------------------------------------- graph: one straight line
def build_graph():
    graph = StateGraph(PipelineState)

    graph.add_node("clone", logged("1/6 clone", clone_repository))
    graph.add_node("install", logged("2/6 install dependencies", install_dependencies))
    graph.add_node("explore", logged("3/6 explore repository", explore_repository))
    graph.add_node("keywords", logged("4/6 keyword research", research_keywords))
    graph.add_node("onpage", logged("5/6 on-page + technical SEO", improve_onpage_and_technical_seo))
    graph.add_node("bundle", logged("6/6 write bundle", write_bundle))

    graph.add_edge(START, "clone")
    graph.add_edge("clone", "install")
    graph.add_edge("install", "explore")
    graph.add_edge("explore", "keywords")
    graph.add_edge("keywords", "onpage")
    graph.add_edge("onpage", "bundle")
    graph.add_edge("bundle", END)

    return graph.compile()


def run_pipeline(github_url, site_url=None, *, build=None, render=None):
    """Run steps 1-6 and return the final PipelineState (its `bundle_path` goes to the publish phase)."""
    if build is None:
        build = env_flag("SEO_BUILD")
    if render is None:
        render = env_flag("SEO_RENDER")
    first = PipelineState(github_url=github_url, site_url=site_url, build=build, render=render)
    final = build_graph().invoke(first)
    state = PipelineState(**final)
    for error in state.errors:
        log("step failed (run continued): " + error)
    return state


# ---------------------------------------------------------------- small helpers
def logged(label, step):
    """Wrap a step so the run log shows when it starts and how long it took."""
    def run(state):
        begun = start("step " + label)
        changes = step(state)
        done("step " + label, begun)
        return changes
    run.__name__ = step.__name__
    return run


def failure(step_name, error):
    message = step_name + ": " + type(error).__name__ + ": " + str(error)[:300]
    log(message)
    return message


def env_flag(name):
    return os.environ.get(name, "").lower() in {"1", "true", "yes"}
