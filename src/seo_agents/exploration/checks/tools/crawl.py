"""The 7 crawl tools, one per on-page check.

Each tool reads only the files it needs and returns plain dicts.
The @tool decorator lets LangChain call them; the check files call
them directly through .func().
"""

import re

from langchain_core.tools import tool

from seo_agents.exploration.checks.tools.helpers import (
    GENERIC_ANCHORS,
    MAX_MATCHES_PER_FILE,
    iter_source_files,
    read_capped,
    rel,
    resolve_repo_path,
    sha16,
)


APP_PAGE = re.compile(r"(?:^|/)app/(.+?)/page\.(tsx|jsx|ts|js|mdx?)")
PAGES_PAGE = re.compile(r"(?:^|/)pages/(.+?)\.(tsx|jsx|ts|js|mdx?)")
TEMPLATE_PAGE = re.compile(r"templates/(.+?)\.(html|htm)")

REACT_ROUTE = re.compile(
    r"(?:path\s*[:=]\s*[\"'`]([^\"'`]+)[\"'`]|<Route\b[^>]*\bpath=[\"'`]([^\"'`]+)[\"'`])"
)

GROUP_SEG = re.compile(r"/\([^/]+\)")
PARALLEL_SEG = re.compile(r"/@[^/]+")
DYNAMIC_SEG = re.compile(r"\[\[?\.{3}[^\]]+\]?\]+|\[[^\]]+\]")


def to_url(slug):
    url = "/" + slug.strip("/")
    url = GROUP_SEG.sub("", url)
    url = PARALLEL_SEG.sub("", url)
    url = DYNAMIC_SEG.sub(":param", url)
    url = re.sub(r"/+", "/", url)
    url = url.replace("/index", "/")
    url = re.sub(r"/+", "/", url)
    if url:
        return url
    return "/"


def clean_slug(slug):
    if slug in ("index", "page") or slug.endswith("/index"):
        if "/" in slug:
            slug = slug.rsplit("/", 1)[0]
        else:
            slug = ""
        if slug in ("index", "page"):
            slug = ""
    return slug


def file_route_url(route_file):
    for pattern in (APP_PAGE, PAGES_PAGE, TEMPLATE_PAGE):
        match = pattern.search(route_file)
        if match:
            return to_url(clean_slug(match.group(1)))
    return None


def code_routes(repo):
    routes = []
    js_exts = {".tsx", ".jsx", ".ts", ".js"}
    for path in iter_source_files(repo, exts=js_exts):
        text = read_capped(path)
        route_file = rel(repo, path)
        matches = list(REACT_ROUTE.finditer(text))[:MAX_MATCHES_PER_FILE]
        for match in matches:
            href = (match.group(1) or match.group(2) or "").strip()
            if href.startswith("/") and "*" not in href and len(href) < 200:
                routes.append({"url": href, "template": route_file, "source": "react-router"})
        if len(routes) >= 200:
            break
    return routes


def static_routes(repo):
    routes = []
    for path in iter_source_files(repo, exts={".html", ".htm"}):
        route_file = rel(repo, path)
        routes.append({"url": "/" + route_file, "template": route_file, "source": "static-html"})
    return routes


def unique_routes(routes):
    seen = set()
    unique = []
    for route in routes:
        if route["url"] not in seen:
            seen.add(route["url"])
            unique.append(route)
    return unique[:200]


def discover_routes(repo_path):
    repo = resolve_repo_path(repo_path)
    routes = []
    for path in iter_source_files(repo):
        route_file = rel(repo, path)
        url = file_route_url(route_file)
        if url:
            routes.append({"url": url, "template": route_file, "source": "file-based"})
    if not routes:
        routes = code_routes(repo)
    if not routes:
        routes = static_routes(repo)
    return unique_routes(routes)


def page_files(repo, routes):
    if routes:
        files = []
        for route in routes:
            files.append(repo / route["template"])
        return files
    return list(iter_source_files(repo))


def find_url(routes, template):
    for route in routes:
        if route["template"] == template:
            return route["url"]
    return "/" + template


WS = re.compile(r"\s+")
TEXT_TAGS = re.compile(
    r"<(p|li|blockquote|h1|h2|h3|h4|td|th|span|div)[^>]*>(.*?)</\1>",
    re.IGNORECASE | re.DOTALL,
)
STRIP_TAGS = re.compile(r"<[^>]+>")


def clean_text(html):
    text = STRIP_TAGS.sub(" ", html)
    return WS.sub(" ", text).strip()


@tool
def scrape_content_blocks(repo_path=""):
    """Scrape visible text blocks per route template (words, thin/duplicate flags)."""
    repo = resolve_repo_path(repo_path or None)
    routes = discover_routes(str(repo))
    pages = []
    counts = {}
    for path in page_files(repo, routes)[:200]:
        text = read_capped(path)
        blocks = []
        matches = list(TEXT_TAGS.finditer(text))[:MAX_MATCHES_PER_FILE]
        for match in matches:
            raw = clean_text(match.group(2))
            if len(raw) >= 20:
                blocks.append(raw[:2000])
        body = " ".join(blocks)
        if not body:
            plain = clean_text(text)
            body = plain[:2000]
            words = len(plain.split())
        else:
            words = len(body.split())
        if body.strip():
            key = sha16(WS.sub(" ", body.lower())[:5000])
        else:
            key = "empty"
        counts[key] = counts.get(key, 0) + 1
        pages.append({
            "template": rel(repo, path),
            "url": find_url(routes, rel(repo, path)),
            "word_count": words,
            "block_count": len(blocks),
            "content_hash": key,
            "is_thin": words < 200,
            "excerpt": body[:500],
        })
    for page in pages:
        page["is_duplicate_boilerplate"] = (
            page["content_hash"] != "empty"
            and counts[page["content_hash"]] > 1
            and page["word_count"] < 800
        )
    return {"page_count": len(pages), "pages": pages}


HEADING = re.compile(r"<(h[1-6])[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
SEMANTIC = re.compile(r"<(main|article|section|nav|header|footer|aside)\b", re.IGNORECASE)


@tool
def scrape_headings(repo_path=""):
    """Scrape H1-H6 hierarchy and semantic landmarks per template."""
    repo = resolve_repo_path(repo_path or None)
    routes = discover_routes(str(repo))
    pages = []
    for path in page_files(repo, routes)[:200]:
        text = read_capped(path)
        headings = []
        matches = list(HEADING.finditer(text))[:MAX_MATCHES_PER_FILE]
        for match in matches:
            headings.append({
                "level": match.group(1).lower(),
                "text": clean_text(match.group(2))[:200],
            })
        landmarks = {}
        for match in SEMANTIC.finditer(text):
            name = match.group(1).lower()
            landmarks[name] = landmarks.get(name, 0) + 1
        h1s = []
        for heading in headings:
            if heading["level"] == "h1":
                h1s.append(heading)
        levels = []
        for heading in headings:
            levels.append(int(heading["level"][1]))
        pages.append({
            "template": rel(repo, path),
            "url": find_url(routes, rel(repo, path)),
            "headings": headings,
            "h1_count": len(h1s),
            "h1_missing": len(h1s) == 0,
            "multiple_h1": len(h1s) > 1,
            "heading_order_ok": levels == sorted(levels),
            "landmarks": landmarks,
            "has_main": landmarks.get("main", 0) > 0,
        })
    return {"page_count": len(pages), "pages": pages}


A_HREF = re.compile(r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
NEXT_LINK = re.compile(r"<Link\s[^>]*href=\{?[\"'`]([^\"'`}]+)[\"'`}]?\}?", re.IGNORECASE)


@tool
def scrape_internal_links(repo_path=""):
    """Scrape the internal anchor graph (href, anchor text, generic anchors)."""
    repo = resolve_repo_path(repo_path or None)
    edge_counts = {}
    target_counts = {}
    generic = 0
    linking_files = 0
    for path in iter_source_files(repo):
        text = read_capped(path)
        src = rel(repo, path)
        found = False
        a_matches = list(A_HREF.finditer(text))[:MAX_MATCHES_PER_FILE]
        for match in a_matches:
            href = match.group(1).strip()
            anchor = clean_text(match.group(2))[:200]
            if href.startswith("/") and not href.startswith("//"):
                found = True
                edge_counts[(src, href)] = edge_counts.get((src, href), 0) + 1
                target_counts[href] = target_counts.get(href, 0) + 1
                if anchor.lower() in GENERIC_ANCHORS or len(anchor) < 3:
                    generic = generic + 1
        link_matches = list(NEXT_LINK.finditer(text))[:MAX_MATCHES_PER_FILE]
        for match in link_matches:
            href = match.group(1).strip()
            if href.startswith("/"):
                found = True
                edge_counts[(src, href)] = edge_counts.get((src, href), 0) + 1
                target_counts[href] = target_counts.get(href, 0) + 1
        if found:
            linking_files = linking_files + 1
    route_urls = []
    for route in discover_routes(str(repo)):
        route_urls.append(route["url"])
    orphans = []
    for url in sorted(route_urls):
        if target_counts.get(url, 0) == 0:
            orphans.append(url)
    top_targets = sorted(target_counts.items(), key=lambda item: item[1], reverse=True)[:50]
    top = []
    for url, inbound in top_targets:
        top.append({"url": url, "inbound": inbound})
    edges = []
    for (src, target), count in list(edge_counts.items())[:500]:
        edges.append({"source": src, "target": target, "count": count})
    total_edges = 0
    for count in edge_counts.values():
        total_edges = total_edges + count
    return {
        "linking_files": linking_files,
        "edge_count": total_edges,
        "unique_edges": len(edge_counts),
        "generic_anchor_count": generic,
        "orphan_routes": orphans,
        "top_targets": top,
        "edges": edges,
    }


TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
META_DESC = re.compile(r'<meta\s[^>]*name=["\']description["\'][^>]*>', re.IGNORECASE)
CANONICAL = re.compile(r'<link\s[^>]*rel=["\']canonical["\'][^>]*>', re.IGNORECASE)
ROBOTS_META = re.compile(r'<meta\s[^>]*name=["\']robots["\'][^>]*>', re.IGNORECASE)
OG_TAG = re.compile(r'<meta\s[^>]*property=["\']og:[^"\']+["\'][^>]*>', re.IGNORECASE)
TWITTER_TAG = re.compile(r'<meta\s[^>]*name=["\']twitter:[^"\']+["\'][^>]*>', re.IGNORECASE)
METADATA_EXPORT = re.compile(r"export\s+(?:const\s+metadata|async\s+function\s+generateMetadata)", re.IGNORECASE)
HEAD_COMPONENT = re.compile(r"<Head[\s>]", re.IGNORECASE)
ATTR_VALUE = re.compile(r'(content|href)=["\']([^"\']+)["\']', re.IGNORECASE)
EXPORT_TITLE = re.compile(r"title\s*:\s*[\"'`]([^\"'`]{1,300})[\"'`]", re.IGNORECASE)
EXPORT_DESC = re.compile(r"description\s*:\s*[\"'`]([^\"'`]{1,1000})[\"'`]", re.IGNORECASE)


@tool
def scrape_metadata(repo_path=""):
    """Scrape title, meta description, canonical, robots, OG/Twitter per template."""
    repo = resolve_repo_path(repo_path or None)
    routes = discover_routes(str(repo))
    pages = []
    seen_titles = {}
    for path in page_files(repo, routes)[:200]:
        text = read_capped(path)
        title = ""
        title_source = ""
        title_match = TITLE_TAG.search(text)
        if title_match:
            title = clean_text(title_match.group(1))[:300]
            title_source = "title-tag"
        uses_metadata_api = bool(METADATA_EXPORT.search(text))
        if not title and uses_metadata_api:
            export_match = EXPORT_TITLE.search(text)
            if export_match:
                title = WS.sub(" ", export_match.group(1)).strip()[:300]
                title_source = "metadata-export"
        if title:
            seen_titles[title] = seen_titles.get(title, 0) + 1
        desc_tag = META_DESC.search(text)
        export_desc = ""
        if uses_metadata_api:
            desc_match = EXPORT_DESC.search(text)
            if desc_match:
                export_desc = WS.sub(" ", desc_match.group(1)).strip()[:1000]
        if desc_tag:
            attr_match = ATTR_VALUE.search(desc_tag.group(0))
            if attr_match:
                desc_len = len(attr_match.group(2))
            else:
                desc_len = 0
            desc_source = "meta-tag"
        elif export_desc:
            desc_len = len(export_desc)
            desc_source = "metadata-export"
        else:
            desc_len = 0
            desc_source = ""
        pages.append({
            "template": rel(repo, path),
            "url": find_url(routes, rel(repo, path)),
            "title": title,
            "title_len": len(title),
            "title_source": title_source,
            "has_meta_description": bool(desc_tag) or bool(export_desc),
            "meta_description_len": desc_len,
            "meta_description_source": desc_source,
            "has_canonical": bool(CANONICAL.search(text)),
            "has_robots_meta": bool(ROBOTS_META.search(text)),
            "og_tag_count": len(OG_TAG.findall(text)),
            "twitter_tag_count": len(TWITTER_TAG.findall(text)),
            "uses_metadata_api": uses_metadata_api,
            "uses_head_component": bool(HEAD_COMPONENT.search(text)),
        })
    duplicates = []
    for title, count in seen_titles.items():
        if count > 1 and title:
            duplicates.append(title)
    return {
        "page_count": len(pages),
        "duplicate_title_count": len(duplicates),
        "duplicate_titles": duplicates[:50],
        "pages": pages,
    }


IMG_TAG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
NEXT_IMAGE = re.compile(r"<Image\b[^>]*>", re.IGNORECASE)
VIDEO_TAG = re.compile(r"<video\b[^>]*>", re.IGNORECASE)
SOURCE_TAG = re.compile(r"<source\b[^>]*>", re.IGNORECASE)
PICTURE_TAG = re.compile(r"<picture\b", re.IGNORECASE)
ALT_ATTR = re.compile(r'alt=["\']([^"\']*)["\']', re.IGNORECASE)
SRC_ATTR = re.compile(r'src=["\']([^"\']+)["\']', re.IGNORECASE)
LOADING_ATTR = re.compile(r'loading=["\']([^"\']+)["\']', re.IGNORECASE)


def read_attr(tag, pattern):
    match = pattern.search(tag)
    if match:
        return match.group(1)
    return ""


@tool
def scrape_media(repo_path=""):
    """Scrape images and video tags (alt text, filename, loading, format)."""
    repo = resolve_repo_path(repo_path or None)
    items = []
    counts = {}
    for path in iter_source_files(repo):
        text = read_capped(path)
        src = rel(repo, path)
        tag_kinds = [(IMG_TAG, "img"), (NEXT_IMAGE, "next-image"), (VIDEO_TAG, "video"), (SOURCE_TAG, "source")]
        for pattern, kind in tag_kinds:
            matches = list(pattern.finditer(text))[:MAX_MATCHES_PER_FILE]
            for match in matches:
                tag = match.group(0)
                alt_text = read_attr(tag, ALT_ATTR)
                has_alt = bool(ALT_ATTR.search(tag))
                src_val = read_attr(tag, SRC_ATTR)
                loading_match = LOADING_ATTR.search(tag)
                if loading_match:
                    loading = loading_match.group(1).lower()
                elif kind == "next-image" and "priority" in tag.lower():
                    loading = "eager?"
                else:
                    loading = "unspecified"
                clean_src = src_val.split("?")[0]
                if "." in src_val:
                    media_format = clean_src.rsplit(".", 1)[-1].lower()[:10]
                else:
                    media_format = ""
                counts[kind] = counts.get(kind, 0) + 1
                items.append({
                    "file": src,
                    "kind": kind,
                    "src": src_val[:300],
                    "filename": clean_src.rsplit("/", 1)[-1][:120],
                    "format": media_format,
                    "has_alt": has_alt,
                    "alt_empty": has_alt and alt_text.strip() == "",
                    "alt_text": alt_text[:300],
                    "alt_len": len(alt_text),
                    "loading": loading,
                    "uses_picture": bool(PICTURE_TAG.search(text[:2000])),
                })
                if len(items) >= 1000:
                    break
    missing = 0
    for item in items:
        if item["kind"] in ("img", "next-image") and not item["has_alt"]:
            missing = missing + 1
    return {
        "media_count": len(items),
        "by_kind": counts,
        "missing_alt_count": missing,
        "items": items[:1000],
    }


DYNAMIC_CHAR = re.compile(r"[\[\]:*]")
FILTER_HREF = re.compile(r'href=["\']([^"\']*\?[^"\']*)["\']')


@tool
def scrape_urls(repo_path=""):
    """Scrape file-based URLs (slug shape, depth, dynamic and filter segments)."""
    repo = resolve_repo_path(repo_path or None)
    routes = discover_routes(str(repo))
    filter_links = []
    for path in iter_source_files(repo):
        text = read_capped(path)
        matches = list(FILTER_HREF.finditer(text))[:20]
        for match in matches:
            filter_links.append({"file": rel(repo, path), "href": match.group(1)[:300]})
            if len(filter_links) >= 200:
                break
        if len(filter_links) >= 200:
            break
    urls = []
    for route in routes:
        url = route["url"]
        stripped = url.strip("/")
        if stripped:
            slug = stripped.split("/")[-1]
            depth = stripped.count("/") + 1
        else:
            slug = ""
            depth = 0
        urls.append({
            "url": url,
            "template": route["template"],
            "depth": depth,
            "slug": slug,
            "slug_len": len(slug),
            "is_dynamic": bool(DYNAMIC_CHAR.search(route["template"])),
            "has_underscore": "_" in slug,
            "has_uppercase": slug != slug.lower(),
            "overlong_slug": len(slug) > 60,
        })
    deep = []
    for entry in urls:
        if entry["depth"] >= 4:
            deep.append(entry)
    breadcrumbs = 0
    code_exts = {".tsx", ".jsx", ".ts", ".js", ".html"}
    for path in iter_source_files(repo, exts=code_exts):
        if "breadcrumb" in read_capped(path).lower():
            breadcrumbs = breadcrumbs + 1
    return {
        "url_count": len(urls),
        "deep_url_count": len(deep),
        "files_with_breadcrumbs": breadcrumbs,
        "filter_query_links": filter_links[:200],
        "urls": urls,
    }


EEAT_PATTERNS = {
    "author_byline": re.compile(r"(author|byline|rel=[\"']author[\"']|Written by|Reviewed by)", re.IGNORECASE),
    "about": re.compile(r"(about[\s_-]?(us|me|page)?|/about)", re.IGNORECASE),
    "contact": re.compile(r"(/contact|Contact Us|mailto:)", re.IGNORECASE),
    "privacy": re.compile(r"(/privacy|Privacy Policy)", re.IGNORECASE),
    "terms": re.compile(r"(/terms|Terms of (Service|Use))", re.IGNORECASE),
    "testimonial": re.compile(r"(testimonial|review-card|Trustpilot|G2 Crowd|Capterra)", re.IGNORECASE),
    "faq": re.compile(r"(FAQ|Frequently Asked Questions|Question--?Answer|FAQPage)", re.IGNORECASE),
    "toc": re.compile(r"(table of contents|toc\b|On this page)", re.IGNORECASE),
}


@tool
def scrape_eeat(repo_path=""):
    """Scrape trust signals (bylines, policy pages, reviews, FAQ, TOC)."""
    repo = resolve_repo_path(repo_path or None)
    hits = {}
    for key in EEAT_PATTERNS:
        hits[key] = []
    for path in iter_source_files(repo):
        text = read_capped(path)
        route_file = rel(repo, path)
        for key, pattern in EEAT_PATTERNS.items():
            if pattern.search(text) or pattern.search(route_file):
                if len(hits[key]) < 100:
                    hits[key].append(route_file)
    summary = {}
    for key, files in hits.items():
        summary[key] = len(files)
    return {
        "signals": summary,
        "has_about": summary["about"] > 0,
        "has_contact": summary["contact"] > 0,
        "has_privacy": summary["privacy"] > 0,
        "files": hits,
    }
