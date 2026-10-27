"""Agent tools returning versioned, category-specific source inventories."""

from langchain_core.tools import tool

from seo_agents.exploration.checks.tools.helpers import resolve_repo_path
from seo_agents.exploration.checks.tools.inventory import inventory, routes


def discover_routes(repo_path):
    return routes(resolve_repo_path(repo_path))


@tool
def scrape_content_blocks(repo_path: str = "") -> dict:
    """Extract content per page, with source evidence and unresolved coverage gaps.

    Returns schema version 2.0 JSON without generated SEO claims.
    Runtime data and unresolved components are explicitly marked unknown.
    """
    return inventory(resolve_repo_path(repo_path or None), "content")


@tool
def scrape_headings(repo_path: str = "") -> dict:
    """Extract page_structure per page, with source evidence and unresolved coverage gaps.

    Returns schema version 2.0 JSON without generated SEO claims.
    Runtime data and unresolved components are explicitly marked unknown.
    """
    return inventory(resolve_repo_path(repo_path or None), "page_structure")


@tool
def scrape_internal_links(repo_path: str = "") -> dict:
    """Extract internal_links per page, with source evidence and unresolved coverage gaps.

    Returns schema version 2.0 JSON without generated SEO claims.
    Runtime data and unresolved components are explicitly marked unknown.
    """
    return inventory(resolve_repo_path(repo_path or None), "internal_links")


@tool
def scrape_metadata(repo_path: str = "") -> dict:
    """Extract metadata per page, with source evidence and unresolved coverage gaps.

    Returns schema version 2.0 JSON without generated SEO claims.
    Runtime data and unresolved components are explicitly marked unknown.
    """
    return inventory(resolve_repo_path(repo_path or None), "metadata")


@tool
def scrape_media(repo_path: str = "") -> dict:
    """Extract media per page, with source evidence and unresolved coverage gaps.

    Returns schema version 2.0 JSON without generated SEO claims.
    Runtime data and unresolved components are explicitly marked unknown.
    """
    return inventory(resolve_repo_path(repo_path or None), "media")


@tool
def scrape_urls(repo_path: str = "") -> dict:
    """Extract urls per page, with source evidence and unresolved coverage gaps.

    Returns schema version 2.0 JSON without generated SEO claims.
    Runtime data and unresolved components are explicitly marked unknown.
    """
    return inventory(resolve_repo_path(repo_path or None), "urls")


@tool
def scrape_eeat(repo_path: str = "") -> dict:
    """Extract eeat per page, with source evidence and unresolved coverage gaps.

    Returns schema version 2.0 JSON without generated SEO claims.
    Runtime data and unresolved components are explicitly marked unknown.
    """
    return inventory(resolve_repo_path(repo_path or None), "eeat")
