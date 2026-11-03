"""URL check: accept only plain github.com repository links (owner/repo)."""

import re

GITHUB_REPO_URL = re.compile(r'^https://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9._-]+?(?:\.git)?/?$')


def is_valid_github_url(url):
    """True only for https://github.com/<owner>/<repo>[.git][/]; no query, fragment or shell metacharacters."""
    return isinstance(url, str) and GITHUB_REPO_URL.fullmatch(url) is not None


class GitHubUrlValidator:
    """Only plain https://github.com/<owner>/<repo> URLs are accepted."""

    def is_valid(self, url):
        return is_valid_github_url(url)
