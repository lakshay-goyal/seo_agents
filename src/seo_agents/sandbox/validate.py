def is_valid_github_url(url: str) -> bool:
    """Lightweight check run before creating the sandbox."""
    return url.startswith("https://github.com/") and len(url.split("/")) >= 5
