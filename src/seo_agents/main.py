import argparse
import os

from seo_agents.log import done, log, start
from seo_agents.sandbox import create_sandbox, is_valid_github_url, run_pipeline_in_sandbox

DEFAULT_REPO = "https://github.com/lakshay-goyal/my-portfolio"
FORWARDED_ENV = (
    "OPENROUTER_API_KEY", "OPENROUTER_MODEL", "OPENROUTER_BASE_URL", "OPENROUTER_MAX_TOKENS",
    "OPENROUTER_REASONING_EFFORT", "OPENROUTER_SMALL_MODEL", "OPENROUTER_MID_MODEL",
    "SEO_MAX_DISCOVERY_ROUNDS", "SEO_NO_LLM", "SEO_EXPLORATION_MODE",
    "SEO_SITE_URL", "SEO_RENDER", "SEO_BUILD", "SEO_RUN_LLM_AGENTS", "SEO_LIGHTHOUSE",
    "PAGESPEED_API_KEY", "SEO_TRACE",
    # site model orchestrator, gap workers and the legacy LLM harness
    "SEO_MAX_PAGES", "SEO_MEASURE_WORKERS", "SEO_CHECK_LINKS", "SEO_EXTERNAL_LINK_SAMPLE",
    "SEO_LIGHTHOUSE_RUNS", "SEO_LIGHTHOUSE_MAX_PAGES", "SEO_GAP_WORKERS", "SEO_GAP_CONCURRENCY",
    "SEO_GAP_MAX_TASKS", "SEO_TOPIC_MAX_PAGES", "SEO_EEAT_MAX_PAGES", "SEO_MODEL_PRICES",
    "SEO_REPAIR_ROUNDS", "SEO_MAX_TOOL_CALLS", "SEO_AGENT_CONCURRENCY",
)


def forward_env(overrides=None):
    env = {"GITHUB_TOKEN": os.environ["GITHUB_TOKEN"]}
    for key in FORWARDED_ENV:
        value = os.environ.get(key)
        if value:
            env[key] = value
    for key, value in (overrides or {}).items():
        if value is not None:
            env[key] = value
    return env


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="seo-agents",
                                     description="Explore a GitHub website repository for SEO and open a reports PR.")
    parser.add_argument("github_url", nargs="?", help="https://github.com/<owner>/<repo>")
    parser.add_argument("--site-url", help="Deployed/preview origin to measure (rendered DOM, Lighthouse, links)")
    parser.add_argument("--render", action="store_true", help="Provision Playwright/Lighthouse and render pages")
    parser.add_argument("--build", action="store_true", help="Install, build and serve the repository in the sandbox")
    parser.add_argument("--llm-agents", action="store_true",
                        help="Also run the 7 legacy LLM category extractors (off by default; the site model covers them)")
    parser.add_argument("--no-llm-agents", action="store_true", help=argparse.SUPPRESS)  # kept for old invocations
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    begun = start("seo-agents run")
    github_url = args.github_url
    if not github_url:
        github_url = DEFAULT_REPO
        log("notice: no repository given; using default " + DEFAULT_REPO)

    if not is_valid_github_url(github_url):
        log("skipping: not a valid GitHub URL: " + github_url)
        return

    if not os.environ.get("GITHUB_TOKEN"):
        raise RuntimeError("Set GITHUB_TOKEN in .env first (classic PAT with the `repo` scope).")

    overrides = {"SEO_SITE_URL": args.site_url,
                 "SEO_RENDER": "1" if args.render else None,
                 "SEO_BUILD": "1" if args.build else None,
                 "SEO_RUN_LLM_AGENTS": "1" if args.llm_agents else "0" if args.no_llm_agents else None}
    sandbox = create_sandbox()
    pr_url = run_pipeline_in_sandbox(sandbox, github_url, env=forward_env(overrides), site_url=args.site_url)
    log("result: " + pr_url)
    done("seo-agents run", begun)


if __name__ == "__main__":
    main()
