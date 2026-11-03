"""Read-only access to the exploration output (seo_reports/). The on-page stage never reads client source.

Accepted roots: the `seo_reports/` folder (with `site_model/` inside) or a `site_model/` folder itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re

SKIP_KEYS = {'el', 'file', 'line', 'col', 'via', 'method', 'confidence', 'edit', 'anchor_edit', 'alt_edit', 'kind',
             'inputs_sha256', 'snapshot_sha256', 'pointer', 'template', 'composition', 'discovered_by', 'add_target',
             'section', 'path', 'id', 'selector', 'sha256', 'content_sha256'}


@dataclass
class Reports:
    root: Path
    site_model_dir: Path
    index: dict
    site: dict
    pages: dict  # page id -> page record
    repo_context: dict = field(default_factory=dict)
    exploration: dict = field(default_factory=dict)  # optional sharded output (seo_reports/exploration/)

    def page_rows(self):
        return self.index.get('pages') or []


def load_reports(root) -> Reports:
    root = Path(root)
    model_dir = root / 'site_model' if (root / 'site_model' / 'index.json').exists() else root
    if not (model_dir / 'index.json').exists():
        raise FileNotFoundError('No site model index.json under ' + str(root))
    index = json.loads((model_dir / 'index.json').read_text())
    site = json.loads((model_dir / 'site.json').read_text()) if (model_dir / 'site.json').exists() else {}
    pages = {}
    for row in index.get('pages') or []:
        path = model_dir / (row.get('file') or ('pages/' + row['id'] + '.json'))
        if path.exists():
            pages[row['id']] = json.loads(path.read_text())
    base = model_dir.parent if model_dir.name == 'site_model' else model_dir
    context_path = base / 'repo_context.json'
    if not context_path.exists():
        context_path = base / 'exploration' / 'evidence' / 'repo_context.json'
    repo_context = json.loads(context_path.read_text()) if context_path.exists() else {}
    exploration = load_exploration(base / 'exploration')
    return Reports(root=base, site_model_dir=model_dir, index=index, site=site, pages=pages, repo_context=repo_context,
                   exploration=exploration)


def _read(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def load_exploration(directory):
    """Optional sharded exploration output: only the small planner/site/route shards (evidence stays lazy)."""
    if not directory.is_dir():
        return {}
    out = {}
    for name in ('manifest', 'summary', 'site', 'routes', 'entity', 'images', 'links', 'metadata'):
        data = _read(directory / (name + '.json'))
        if data is not None:
            out[name] = data
    return out


def exploration_site(reports):
    """Site facts merged from the site model and the optional exploration shards (shards win when present)."""
    site = dict(reports.site or {})
    shard = (reports.exploration or {}).get('site') or {}
    if isinstance(shard, dict):
        for key, item in shard.items():
            if item not in (None, {}, []):
                site[key] = item
    return site


def route_record(reports, page):
    """routes.json row for a page (head_scope, render mode, template), if the sharded output exists."""
    routes = (reports.exploration or {}).get('routes')
    rows = routes.get('routes') if isinstance(routes, dict) else routes
    for row in rows or []:
        if isinstance(row, dict) and (row.get('url') == page.get('url') or row.get('route_id') == page.get('id')):
            return row
    return {}


# ------------------------------------------------------------------ field helpers
def value(field_):
    if isinstance(field_, dict):
        return field_.get('value')
    return field_


def reference(field_, fallback=None):
    """Edit reference for a FIELD (where the change must be made), or the add target when absent."""
    if isinstance(field_, dict) and field_.get('edit'):
        ref = {'edit': field_['edit'], 'method': field_.get('method'), 'confidence': field_.get('confidence')}
        if field_.get('lens_agreement') is not None:
            ref['lens_agreement'] = field_['lens_agreement']
        return ref
    if fallback:
        return {'edit': fallback, 'method': 'add', 'confidence': None}
    return None


def clean(text):
    if not isinstance(text, str):
        return None
    text = re.sub(r'\s+', ' ', text).strip()
    return text or None


def head(page, key):
    return (page.get('head') or {}).get(key)


def page_title(page):
    return clean(value(head(page, 'title')))


def page_description(page):
    return clean(value(head(page, 'description')))


def headings(page):
    return [h for h in page.get('headings') or [] if isinstance(h, dict)]


def heading_texts(page):
    return [{'level': h.get('level'), 'text': clean(h.get('text'))} for h in headings(page) if clean(h.get('text'))]


def blocks(page):
    return [b for b in (page.get('content') or {}).get('blocks') or [] if isinstance(b, dict)]


TEXT_TAGS = {'p', 'li', 'span', 'div', 'td', 'th', 'dd', 'dt', 'blockquote', 'figcaption', 'label', 'small', 'strong', 'em', 'a', 'button', 'summary'}


def copy_text(page, limit=1800):
    """Visible copy in document order (headings + text blocks), capped by words."""
    parts, total = [], 0
    for item in document_order(page):
        text = clean(item.get('text'))
        if not text:
            continue
        words = len(text.split())
        if total + words > limit:
            break
        parts.append(text)
        total += words
    return parts


def position(item):
    edit = item.get('edit') or {}
    return (str(edit.get('file') or ''), int(edit.get('line') or 0), int(edit.get('col') or 0))


HEADING_TAGS = {'h1': 1, 'h2': 2, 'h3': 3, 'h4': 4, 'h5': 5, 'h6': 6}


def document_order(page):
    """Headings + blocks in reading order. Rendered block lists already interleave headings."""
    content = blocks(page)
    if any(b.get('tag') in HEADING_TAGS for b in content):
        known = {(h.get('level'), clean(h.get('text'))): h for h in headings(page)}
        ordered = []
        for b in content:
            if b.get('tag') in HEADING_TAGS:
                level = HEADING_TAGS[b['tag']]
                source = known.get((level, clean(b.get('text')))) or {}
                ordered.append({'type': 'heading', 'level': level, 'text': b.get('text'),
                                'edit': source.get('edit') or b.get('edit')})
            else:
                ordered.append({'type': 'block', **b})
        return ordered
    items = [{'type': 'heading', **h} for h in headings(page)] + [{'type': 'block', **b} for b in content]
    if all(not (i.get('edit') or {}).get('line') for i in items):
        return items
    return sorted(items, key=position)


def current_sections(page):
    """Group current blocks under the heading that precedes them (approximate document order)."""
    sections, current = [], {'heading': None, 'level': None, 'edit': None, 'blocks': []}
    for item in document_order(page):
        if item['type'] == 'heading':
            if current['heading'] or current['blocks']:
                sections.append(current)
            current = {'heading': clean(item.get('text')), 'level': item.get('level'), 'edit': item.get('edit'), 'blocks': []}
        elif clean(item.get('text')):
            current['blocks'].append({'tag': item.get('tag'), 'text': clean(item.get('text')), 'edit': item.get('edit')})
    if current['heading'] or current['blocks']:
        sections.append(current)
    return sections


def images(page):
    return [i for i in page.get('images') or [] if isinstance(i, dict)]


def links_out(page):
    return [l for l in (page.get('links') or {}).get('out') or [] if isinstance(l, dict)]


def flatten_strings(obj, out=None, depth=0):
    """Every human string in a JSON value (skips locators/metadata keys)."""
    out = [] if out is None else out
    if depth > 12:
        return out
    if isinstance(obj, dict):
        for key, item in obj.items():
            if key in SKIP_KEYS:
                continue
            flatten_strings(item, out, depth + 1)
    elif isinstance(obj, list):
        for item in obj:
            flatten_strings(item, out, depth + 1)
    elif isinstance(obj, str):
        text = clean(obj)
        if text and len(text) > 1:
            out.append(text)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out.append(str(obj))
    return out


def visible_strings(page):
    """Grounding corpus for one page: served/visible text and data values only (audit §10: no JSX snippets, byte
    counts, HTTP codes or file paths)."""
    out = []
    head_ = page.get('head') or {}
    for key in ('title', 'description'):
        text = clean(value(head_.get(key)))
        if text:
            out.append(text)
    for group in ('og', 'twitter'):
        for item in (head_.get(group) or {}).values():
            text = clean(value(item))
            if text and not text.startswith('http'):
                out.append(text)
    for h in headings(page):
        if clean(h.get('text')):
            out.append(clean(h['text']))
    for b in blocks(page):
        if clean(b.get('text')):
            out.append(clean(b['text']))
    for link in links_out(page):
        if link.get('visible') is False:
            continue
        for key in ('anchor', 'aria_label', 'img_alt', 'title'):
            if clean(link.get(key)):
                out.append(clean(link[key]))
    for image in images(page):
        if clean(image.get('alt')):
            out.append(clean(image['alt']))
        if clean(image.get('caption')):
            out.append(clean(image['caption']))
    for item in page.get('data_values') or []:  # optional: data JSON values backing the page (exploration shards)
        if isinstance(item, str) and clean(item):
            out.append(clean(item))
    for row in page.get('structured_data') or []:
        if isinstance(row, dict):
            for key in ('name', 'headline', 'description', 'jobTitle'):
                if isinstance(row.get(key), str):
                    out.append(clean(row[key]))
    return [t for t in out if t]


IMAGE_EXT = re.compile(r'\.(?:png|jpe?g|webp|avif|gif|svg)$', re.I)


def site_assets(reports, page=None):
    """Images the writer may use: this page's images first, then public/ image files from the inventory."""
    assets, seen = [], set()
    for image in images(page or {}):
        src = image.get('src')
        if src and src not in seen:
            seen.add(src)
            assets.append({'src': src, 'alt': clean(image.get('alt')), 'on_page': True,
                           'natural': image.get('natural')})
    for entry in reports.repo_context.get('file_inventory') or []:
        path = (entry or {}).get('path') or ''
        if path.startswith('public/') and IMAGE_EXT.search(path):
            src = path[len('public'):]
            if src not in seen:
                seen.add(src)
                assets.append({'src': src, 'alt': None, 'on_page': False, 'natural': None})
    return assets


def site_urls(reports):
    """Internal URLs that exist: manifest pages plus internal link targets that resolved (< 400)."""
    urls = {}
    for row in reports.page_rows():
        urls[row['url']] = {'url': row['url'], 'title': clean(row.get('title')), 'in_model': True}
    for page in reports.pages.values():
        for link in links_out(page):
            href = link.get('href')
            if link.get('kind') == 'internal' and isinstance(href, str) and href.startswith('/') \
                    and not IMAGE_EXT.search(href) and not href.lower().endswith('.pdf') \
                    and (link.get('status') is None or link.get('status') < 400) and href not in urls:
                urls[href] = {'url': href, 'title': clean(link.get('anchor')), 'in_model': False}
    return list(urls.values())


def site_origin(reports, override=None):
    if override:
        return override.rstrip('/')
    serve = reports.site.get('serve') or {}
    base = serve.get('base_url')
    for page in reports.pages.values():
        final = ((page.get('http') or {}).get('final_url'))
        if isinstance(final, str) and final.startswith('http'):
            match = re.match(r'(https?://[^/]+)', final)
            if match:
                return match.group(1)
    return base.rstrip('/') if isinstance(base, str) else None
