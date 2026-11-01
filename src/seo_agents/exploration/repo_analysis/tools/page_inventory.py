"""Structural page inventory over any template language (no framework assumptions).

Templates of every ecosystem still contain tags (`<h1>`, `<a>`, `<img>`, `<meta>`)
or Markdown. Each record keeps the exact markup in `code` so an agent can edit it.
"""

import html
from pathlib import PurePosixPath
import re
import tomllib

from seo_agents.exploration.repo_analysis.tools.repository_snapshot import file_roles
from seo_agents.exploration.repo_analysis.tools.source_index import language_of, strip_quotes
from seo_agents.exploration.repo_analysis.tools.skill_runner import evaluate_gaps, records_of, run_skill

MAX_TEXT_BLOCKS_PER_FILE = 300
ATTR = re.compile(r'''([:@\w.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|\{\s*(`[^`]*`|"[^"]*"|'[^']*'|[^{}]*(?:\{[^{}]*\}[^{}]*)*)\s*\})''', re.S)
TITLE = re.compile(r'<title\b[^>]*>(.*?)</title>', re.S | re.I)
META = re.compile(r'<meta\b[^>]*?/?>', re.S | re.I)
LINK_TAG = re.compile(r'<link\b[^>]*?/?>', re.S | re.I)
HTML_TAG = re.compile(r'<html\b[^>]*>', re.I)
HEADING = re.compile(r'<h([1-6])\b[^>]*>(.*?)</h\1\s*>', re.S | re.I)
MD_HEADING = re.compile(r'^(#{1,6})\s+(.+?)\s*#*\s*$', re.M)
TEXT_NODE = re.compile(r'>([^<>{}]*[^\W\d_][^<>{}]*)<', re.S)
ANCHOR = re.compile(r'<(a|Link|NuxtLink|RouterLink|router-link|NavLink|A)\b([^>]*?)(?:/>|>(.*?)</\1\s*>)', re.S)
MD_LINK = re.compile(r'(?<!!)\[([^\]\n]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)')
MEDIA = re.compile(r'<(img|Image|NuxtImg|NuxtPicture|StaticImage|GatsbyImage|enhanced:img|picture|source|video|audio|iframe)\b([^>]*?)/?>', re.S)
MD_IMAGE = re.compile(r'!\[([^\]\n]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)')
FRONT_MATTER = re.compile(r'\A(?:---|\+\+\+)\s*\n(.*?)\n(?:---|\+\+\+)\s*(?:\n|\Z)', re.S)
DECLARED_FIELD = re.compile(r'''\b(title|description|keywords|siteName|applicationName|canonical)\s*:\s*(['"`])((?:(?!\2).){1,400})\2''', re.S)
CONTENT_LANGUAGES = {'markdown', 'mdx'}
SKIP_TEXT = re.compile(r'^[\s\W\d]*$|^\s*(?:import|export|const|let|var|function|return)\b|=>|&&|\|\||;\s*$')


def attributes(raw):
    found = {}
    for match in ATTR.finditer(raw):
        value = next(g for g in match.groups()[1:] if g is not None)
        dynamic = match.group(4) is not None and not re.fullmatch(r'''\s*(["'`])[^"'`]*\1\s*''', match.group(4))
        found[match.group(1)] = {'value': html.unescape(strip_quotes(value)), 'dynamic': dynamic}
    for flag in re.findall(r'(?:^|\s)(priority|async|defer|download|hidden)(?=\s|$|/)', raw):
        found.setdefault(flag, {'value': True, 'dynamic': False})
    return found


def plain(fragment):
    text = re.sub(r'<[^>]+>', ' ', fragment)
    text = re.sub(r'\{[^{}]*\}', ' ', text)
    return ' '.join(html.unescape(text).split())


def front_matter(index, path):
    match = FRONT_MATTER.match(index.files[path])
    if not match:
        return None
    body = match.group(1)
    data = {}
    if index.files[path].startswith('+++'):
        try:
            data = tomllib.loads(body)
        except tomllib.TOMLDecodeError:
            pass
    else:
        key = None
        for line in body.splitlines():
            item = re.match(r'^([\w-]+)\s*:\s*(.*)$', line)
            if item:
                key, value = item.group(1), item.group(2).strip()
                if value.startswith('[') and value.endswith(']'):
                    data[key] = [strip_quotes(v) for v in value[1:-1].split(',') if v.strip()]
                else:
                    data[key] = strip_quotes(value) if value else []
            elif key and re.match(r'^\s+-\s+', line) and isinstance(data.get(key), list):
                data[key].append(strip_quotes(re.sub(r'^\s+-\s+', '', line)))
    return index.evidence('front-matter', path, match.start(), match.end(), fields=data)


def local_asset(index, src):
    if not isinstance(src, str) or re.match(r'^(?:https?:)?//|^data:', src):
        return None
    clean = src.split('?')[0].lstrip('./')
    for candidate in (clean, 'public/' + clean, 'static/' + clean, 'src/' + clean, 'assets/' + clean):
        if candidate in index.assets:
            return {'path': candidate, **index.assets[candidate]}
    name = PurePosixPath(clean).name
    hits = [p for p in index.assets if p.endswith('/' + name) or p == name]
    return {'path': hits[0], 'match': 'by-file-name', **index.assets[hits[0]]} if len(hits) == 1 else None


def analyze_page_inventory(index):
    metadata_skill = run_skill(index, 'metadata')
    title_tags, meta_tags, link_tags, html_tags, declared = [], [], [], [], []
    headings, text_blocks, links, media, front = [], [], [], [], []
    for path in index.templates():
        text, language = index.files[path], language_of(path)
        for match in TITLE.finditer(text):
            title_tags.append(index.evidence('title-tag', path, match.start(), match.end(), value=plain(match.group(1)),
                                             dynamic=bool(re.search(r'\{|<%|\{\{', match.group(1)))))
        for match in META.finditer(text):
            attrs = attributes(match.group(0))
            name = (attrs.get('name') or attrs.get('property') or attrs.get('http-equiv') or attrs.get('itemprop') or {}).get('value')
            if name or 'charset' in attrs:
                meta_tags.append(index.evidence('meta-tag', path, match.start(), match.end(), name=name or 'charset',
                                                value=(attrs.get('content') or attrs.get('charset') or {}).get('value'),
                                                dynamic=(attrs.get('content') or {}).get('dynamic', False)))
        for match in LINK_TAG.finditer(text):
            attrs = attributes(match.group(0))
            rel = (attrs.get('rel') or {}).get('value')
            if rel:
                link_tags.append(index.evidence('link-tag', path, match.start(), match.end(), rel=rel,
                                                href=(attrs.get('href') or {}).get('value'),
                                                hreflang=(attrs.get('hreflang') or attrs.get('hrefLang') or {}).get('value')))
        for match in HTML_TAG.finditer(text):
            attrs = attributes(match.group(0))
            html_tags.append(index.evidence('html-tag', path, match.start(), match.end(),
                                            lang=(attrs.get('lang') or {}).get('value')))
        for match in HEADING.finditer(text):
            headings.append(index.evidence('heading', path, match.start(), match.end(), level=int(match.group(1)),
                                           text=plain(match.group(2)), dynamic=bool(re.search(r'\{', match.group(2)))))
        if language in CONTENT_LANGUAGES:
            fm = front_matter(index, path)
            if fm:
                front.append(fm)
            body_start = FRONT_MATTER.match(text).end() if FRONT_MATTER.match(text) else 0
            in_fence, offset = False, 0
            for line in text.splitlines(keepends=True):
                offset += len(line)
                start = offset - len(line)
                if start < body_start:
                    continue
                if line.lstrip().startswith(('```', '~~~')):
                    in_fence = not in_fence
                    continue
                if in_fence:
                    continue
                heading = MD_HEADING.match(line)
                if heading:
                    headings.append(index.evidence('heading', path, start, start + len(line), level=len(heading.group(1)),
                                                   text=plain(heading.group(2)), dynamic=False, syntax='markdown'))
                elif len(line.split()) >= 3 and not line.lstrip().startswith(('<', 'import ', 'export ', '|', '![')):
                    if sum(1 for b in text_blocks if b['location']['file'] == path) < MAX_TEXT_BLOCKS_PER_FILE:
                        text_blocks.append(index.evidence('text', path, start, start + len(line),
                                                          text=plain(re.sub(r'[*_`>#-]+', ' ', MD_LINK.sub(r'\1', line))),
                                                          syntax='markdown'))
        else:
            count = 0
            for match in TEXT_NODE.finditer(text):
                value = ' '.join(html.unescape(match.group(1)).split())
                if len(value.split()) < 2 or SKIP_TEXT.search(value) or count >= MAX_TEXT_BLOCKS_PER_FILE:
                    continue
                count += 1
                text_blocks.append(index.evidence('text', path, match.start(1), match.end(1), text=value, syntax='markup'))
        for match in ANCHOR.finditer(text):
            attrs = attributes(match.group(2))
            target = attrs.get('href') or attrs.get('to')
            if not target:
                continue
            href = target['value']
            links.append(index.evidence('link', path, match.start(), match.end(), href=href, dynamic=target['dynamic'],
                                        anchor_text=plain(match.group(3) or ''), component=match.group(1),
                                        rel=(attrs.get('rel') or {}).get('value'), target=(attrs.get('target') or {}).get('value'),
                                        link_type=link_kind(href)))
        for match in MD_LINK.finditer(text):
            links.append(index.evidence('link', path, match.start(), match.end(), href=match.group(2), dynamic=False,
                                        anchor_text=match.group(1), component='markdown', link_type=link_kind(match.group(2))))
        for match in MEDIA.finditer(text):
            attrs = attributes(match.group(2))
            tag = match.group(1)
            src = (attrs.get('src') or attrs.get('srcSet') or attrs.get('srcset') or attrs.get('ngSrc') or {}).get('value')
            record = {'tag': tag, 'src': src, 'alt': (attrs['alt']['value'] if 'alt' in attrs else None),
                      'has_alt': 'alt' in attrs, 'width': (attrs.get('width') or {}).get('value'),
                      'height': (attrs.get('height') or {}).get('value'),
                      'loading': (attrs.get('loading') or {}).get('value'),
                      'priority': 'priority' in attrs or (attrs.get('fetchpriority') or {}).get('value') == 'high',
                      'responsive': any(k in attrs for k in ('srcset', 'srcSet', 'sizes')),
                      'fill': 'fill' in attrs, 'local_asset': local_asset(index, src)}
            if tag in {'img', 'Image', 'NuxtImg', 'StaticImage', 'enhanced:img'}:
                record['issues'] = image_issues(record)
            media.append(index.evidence('media', path, match.start(), match.end(), **record))
        for match in MD_IMAGE.finditer(text):
            record = {'tag': 'markdown-image', 'src': match.group(2), 'alt': match.group(1), 'has_alt': bool(match.group(1)),
                      'width': None, 'height': None, 'loading': None, 'priority': False, 'responsive': False,
                      'fill': False, 'local_asset': local_asset(index, match.group(2))}
            record['issues'] = image_issues(record)
            media.append(index.evidence('media', path, match.start(), match.end(), **record))
    for record in records_of(metadata_skill, *metadata_skill['signals']):
        for match in DECLARED_FIELD.finditer(record.get('code', '')):
            declared.append({'field': match.group(1), 'value': ' '.join(match.group(3).split()),
                             'declared_in': record['id'], 'location': record['location']})
    description_found = any(m['name'] and m['name'].lower() in {'description', 'og:description'} for m in meta_tags) \
        or any(d['field'] == 'description' for d in declared)
    if description_found:
        evaluate_gaps(metadata_skill, {'@literal:description'})
    content_skill = run_skill(index, 'content', extra_found={'@heading:1'} if any(h['level'] == 1 for h in headings) else ())
    return {
        'metadata': {'title_tags': title_tags, 'meta_tags': meta_tags, 'link_tags': link_tags, 'html_tags': html_tags,
                     'declared_values': declared, 'apis': metadata_skill},
        'headings': headings, 'text_blocks': text_blocks, 'front_matter': front,
        'links': links, 'media': media, 'content_sources': content_skill,
        'data_content': data_content(index),
    }


def link_kind(href):
    if not isinstance(href, str):
        return 'dynamic'
    if href.startswith(('mailto:', 'tel:', 'sms:', 'javascript:')):
        return 'action'
    if href.startswith('#'):
        return 'fragment'
    if re.match(r'^(?:https?:)?//', href):
        return 'absolute'
    return 'internal'


def image_issues(record):
    issues = []
    if not record['has_alt']:
        issues.append('missing-alt')
    if not record['fill'] and not (record['width'] and record['height']) and record['tag'] != 'StaticImage':
        issues.append('missing-dimensions (CLS)')
    if not record['loading'] and not record['priority'] and record['tag'] in {'img', 'markdown-image'}:
        issues.append('no-loading-attribute')
    asset = record.get('local_asset') or {}
    if asset.get('size_bytes', 0) > 200_000:
        issues.append('large-local-file (' + str(asset['size_bytes'] // 1024) + ' KiB, LCP)')
    if isinstance(record['src'], str) and re.search(r'\.(?:png|jpe?g|gif|bmp)(?:$|\?)', record['src'], re.I):
        issues.append('legacy-format (consider webp/avif)')
    return issues


def data_content(index, limit=400):
    """Prose-like string values in JSON/YAML data files (candidate page copy)."""
    records = []
    pattern = re.compile(r'''^\s*["']?([\w-]+)["']?\s*:\s*(["'])((?:(?!\2).){20,})\2''', re.M)
    for path in index.paths(languages={'json', 'yaml'}):
        if 'content-data' not in file_roles(path) or path.endswith(('package.json', 'tsconfig.json')) or 'locale' in path:
            continue
        for match in pattern.finditer(index.files[path]):
            if len(match.group(3).split()) >= 4 and len(records) < limit:
                records.append(index.evidence('data-text', path, match.start(), match.end(),
                                              key=match.group(1), text=match.group(3)))
    return records
