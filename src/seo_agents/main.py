import os

from seo_agents.log import done, log, start
from seo_agents.sandbox import create_sandbox, is_valid_github_url, run_pipeline_in_sandbox


def forward_env():
    env = {"GITHUB_TOKEN": os.environ["GITHUB_TOKEN"]}
    for key in ("OPENROUTER_API_KEY", "OPENROUTER_MODEL", "OPENROUTER_BASE_URL",
                "OPENROUTER_MAX_TOKENS", "OPENROUTER_REASONING_EFFORT", "SEO_MAX_DISCOVERY_ROUNDS", "SEO_NO_LLM", "SEO_EXPLORATION_MODE"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def main():
    begun = start("seo-agents run")
    github_url = "https://github.com/lakshay-goyal/my-portfolio"

    if not is_valid_github_url(github_url):
        log("skipping: not a valid GitHub URL: " + github_url)
        return

    if not os.environ.get("GITHUB_TOKEN"):
        raise RuntimeError("Set GITHUB_TOKEN in .env first (classic PAT with the `repo` scope).")

    sandbox = create_sandbox()
    pr_url = run_pipeline_in_sandbox(sandbox, github_url, env=forward_env())
    log("result: " + pr_url)
    done("seo-agents run", begun)


if __name__ == "__main__":
    main()
