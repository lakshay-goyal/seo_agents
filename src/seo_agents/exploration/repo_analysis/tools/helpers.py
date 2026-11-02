"""Small shared helpers for the on-page checks."""

from pathlib import Path


SKIP_DIRS = {
    "node_modules", ".git", ".next", "dist", "build", ".venv", "venv",
    "__pycache__", ".pytest_cache", ".ruff_cache", "coverage",
}

TEXT_EXTS = {
    ".tsx", ".jsx", ".ts", ".js", ".html", ".htm", ".md", ".mdx",
    ".vue", ".svelte", ".astro",
}

REPORT_FILES = {
    "content": "content.json",
    "page_structure": "page_structure.json",
    "internal_links": "internal_links.json",
    "metadata": "metadata.json",
    "media": "media.json",
    "urls": "urls.json",
    "eeat": "eeat.json",
}

GENERIC_ANCHORS = {
    "click here", "here", "read more", "learn more", "more", "link",
    "click", "see more", "details",
}


def resolve_repo_path(repo_path=None):
    if repo_path:
        return Path(repo_path).resolve()
    return Path.cwd().resolve()


def rel(repo, path):
    try:
        return str(path.relative_to(repo))
    except ValueError:
        return str(path)
