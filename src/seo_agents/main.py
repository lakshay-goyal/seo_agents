from seo_agents.exploration.repo_analysis import repo_analysis
from seo_agents.sandbox import clone_repo, create_sandbox, is_valid_github_url


def main() -> None:
    github_url = "https://github.com/lakshay-goyal/my-portfolio"

    if not is_valid_github_url(github_url):
        print(f"Skipping: not a valid GitHub URL: {github_url}")
        return

    sandbox = create_sandbox()
    clone_repo(sandbox, github_url)
    repo_analysis(github_url)


if __name__ == "__main__":
    main()
