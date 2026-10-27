"""Small shared helpers for the on-page checks."""

import hashlib
import os
import re
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

MAX_FILES_PER_SCAN = 500
MAX_BYTES_PER_FILE = 100_000
MAX_MATCHES_PER_FILE = 100

GENERIC_ANCHORS = {
    "click here", "here", "read more", "learn more", "more", "link",
    "click", "see more", "details",
}


def resolve_repo_path(repo_path=None):
    if repo_path:
        return Path(repo_path).resolve()
    return Path.cwd().resolve()


def detect_frontend_framework(repo):
    package_file = repo / "package.json"
    package_text = ""
    if package_file.exists():
        package_text = package_file.read_text(encoding="utf-8", errors="ignore")
    uses_next = '"next"' in package_text
    uses_react = '"react"' in package_text
    has_next_config = (
        (repo / "next.config.js").exists()
        or (repo / "next.config.mjs").exists()
        or (repo / "next.config.ts").exists()
    )
    has_app_folder = (repo / "app").is_dir() or (repo / "src" / "app").is_dir()
    if uses_next and has_app_folder:
        return "next-app-router"
    if uses_next or has_next_config:
        return "next-pages-router"
    if uses_react:
        return "react"
    if (repo / "vite.config.ts").exists() or (repo / "vite.config.js").exists():
        return "vite"
    return "unknown"


def iter_source_files(repo, exts=None):
    if exts is None:
        exts = TEXT_EXTS
    count = 0
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if count >= MAX_FILES_PER_SCAN:
                return
            path = Path(root) / name
            if path.suffix.lower() in exts:
                count = count + 1
                yield path


def read_capped(path):
    try:
        data = path.read_bytes()[:MAX_BYTES_PER_FILE]
        return data.decode("utf-8", errors="ignore")
    except OSError:
        return ""


def rel(repo, path):
    try:
        return str(path.relative_to(repo))
    except ValueError:
        return str(path)


def sha16(text):
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]
