import os

from seo_agents.sandbox import create_sandbox, is_valid_github_url, run_pipeline_in_sandbox


def forward_env():
    env = {"GITHUB_TOKEN": os.environ["GITHUB_TOKEN"]}
    for key in ("OPENROUTER_API_KEY", "OPENROUTER_MODEL", "OPENROUTER_BASE_URL", "SEO_NO_LLM"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def main():
    github_url = "https://github.com/lakshay-goyal/my-portfolio"

    if not is_valid_github_url(github_url):
        print("Skipping: not a valid GitHub URL: " + github_url)
        return

    if not os.environ.get("GITHUB_TOKEN"):
        raise RuntimeError("Set GITHUB_TOKEN in .env first (classic PAT with the `repo` scope).")

    sandbox = create_sandbox()
    pr_url = run_pipeline_in_sandbox(sandbox, github_url, env=forward_env())
    print("Done. PR: " + pr_url)


if __name__ == "__main__":
    main()
