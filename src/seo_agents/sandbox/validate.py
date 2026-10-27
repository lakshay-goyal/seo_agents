"""URL check: accept only github.com repo links."""


def is_valid_github_url(url):
    if not url.startswith("https://github.com/"):
        return False
    return len(url.split("/")) >= 5
