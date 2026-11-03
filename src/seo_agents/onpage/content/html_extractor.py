"""Turn one fetched competitor HTML page into a compact, analysable record (stdlib html.parser, no network).

Kept: title, meta description, dates, JSON-LD types, H1-H4 outline, main-text blocks, counts of lists / tables /
images / videos / code / quotes, image alt coverage, internal vs external links and the URL slug. Text inside
<script>, <style>, <noscript>, <svg>, <template> is dropped; text inside <nav>, <header>, <footer>, <aside> is
counted as chrome, not as main content.
"""

from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin, urlparse

SKIP = {'script', 'style', 'noscript', 'svg', 'template', 'iframe', 'canvas'}
CHROME = {'nav', 'header', 'footer', 'aside', 'form', 'button', 'select'}
TEXT_BLOCKS = {'p', 'li', 'td', 'th', 'blockquote', 'dd', 'dt', 'figcaption', 'pre'}
HEADINGS = {'h1': 1, 'h2': 2, 'h3': 3, 'h4': 4}
COUNTED = ('ul', 'ol', 'table', 'img', 'figure', 'video', 'pre', 'code', 'blockquote', 'iframe')
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr'}
DATE_META = {'article:published_time': 'published', 'article:modified_time': 'modified', 'og:updated_time': 'modified',
             'date': 'published', 'publish-date': 'published', 'pubdate': 'published', 'last-modified': 'modified',
             'dc.date': 'published', 'dc.date.issued': 'published', 'dcterms.modified': 'modified'}
ISO_DATE = re.compile(r'(20\d{2}|19\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])')


def clean(text):
    return re.sub(r'\s+', ' ', text or '').strip()


def iso_date(value):
    """'2026-05-01T10:00:00Z' / '2026-05-01' -> '2026-05-01' (None when no ISO date is inside)."""
    match = ISO_DATE.search(str(value or ''))
    return match.group(0) if match else None


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.skip_depth = 0
        self.chrome_depth = 0
        self.title = ''
        self.in_title = False
        self.meta = {}
        self.canonical = None
        self.json_ld = []
        self.in_json_ld = False
        self.json_buffer = []
        self.headings = []
        self.heading_level = None
        self.heading_buffer = []
        self.block_tag = None
        self.block_buffer = []
        self.blocks = []
        self.chrome_words = 0
        self.counts = {k: 0 for k in COUNTED}
        self.images = []
        self.links = []
        self.times = []
        self.list_items = 0

    # ---------------------------------------------------------------- tags
    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or '') for k, v in attrs}
        if tag in COUNTED:
            self.counts[tag] += 1
        if tag == 'script' and 'ld+json' in a.get('type', '').lower():
            self.in_json_ld, self.json_buffer = True, []
        if tag in SKIP:
            self.skip_depth += 1
        if tag in CHROME:
            self.chrome_depth += 1
        if tag == 'title' and not self.title:
            self.in_title = True
        elif tag == 'meta':
            key = (a.get('name') or a.get('property') or a.get('itemprop') or a.get('http-equiv') or '').lower()
            if key and a.get('content'):
                self.meta.setdefault(key, a['content'])
        elif tag == 'link' and 'canonical' in a.get('rel', '').lower().split():
            self.canonical = a.get('href') or None
        elif tag == 'img':
            self.images.append({'src': a.get('src') or a.get('data-src') or '', 'alt': clean(a.get('alt')),
                                'has_alt': 'alt' in a, 'in_chrome': self.chrome_depth > 0})
        elif tag == 'a' and a.get('href'):
            self.links.append({'href': a['href'], 'in_chrome': self.chrome_depth > 0})
        elif tag == 'time' and a.get('datetime'):
            self.times.append(a['datetime'])
        if tag in HEADINGS and not self.skip_depth:
            self.heading_level, self.heading_buffer = HEADINGS[tag], []
        elif tag in TEXT_BLOCKS and not self.skip_depth and self.block_tag is None:
            self.block_tag, self.block_buffer = tag, []
        if tag == 'li' and not self.skip_depth and not self.chrome_depth:
            self.list_items += 1
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag == 'script' and self.in_json_ld:
            self.in_json_ld = False
            self.json_ld.append(''.join(self.json_buffer))
        if tag == 'title':
            self.in_title = False
        if tag in HEADINGS and self.heading_level == HEADINGS[tag]:
            text = clean(''.join(self.heading_buffer))
            if text:
                self.headings.append({'level': self.heading_level, 'text': text[:200], 'in_chrome': self.chrome_depth > 0})
            self.heading_level = None
        if tag == self.block_tag:
            text = clean(''.join(self.block_buffer))
            if text:
                if self.chrome_depth:
                    self.chrome_words += len(text.split())
                else:
                    self.blocks.append({'tag': tag, 'text': text})
            self.block_tag = None
        if tag in SKIP and self.skip_depth:
            self.skip_depth -= 1
        if tag in CHROME and self.chrome_depth:
            self.chrome_depth -= 1
        if tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass

    def handle_data(self, data):
        if self.in_json_ld:
            self.json_buffer.append(data)
            return
        if self.in_title:
            self.title += data
            return
        if self.skip_depth:
            return
        if self.heading_level is not None:
            self.heading_buffer.append(data)
        if self.block_tag is not None:
            self.block_buffer.append(data)


def _json_ld_items(raw_blocks):
    items = []
    for raw in raw_blocks:
        try:
            data = json.loads(raw.strip())
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                items.append(node)
                stack.extend(v for k, v in node.items() if k == '@graph' or isinstance(v, (dict, list)))
    return items


def _types(items):
    out = []
    for item in items:
        kind = item.get('@type')
        for k in (kind if isinstance(kind, list) else [kind]):
            if isinstance(k, str) and k not in out:
                out.append(k)
    return out


def slug_words(url):
    path = urlparse(url or '').path.strip('/')
    last = path.split('/')[-1] if path else ''
    last = re.sub(r'\.(html?|php|aspx?)$', '', last)
    return [w for w in re.split(r'[-_]+', last.lower()) if w]


def extract(html, url, *, last_modified=None):
    """HTML text -> competitor page record (see module docstring). Never raises on malformed HTML."""
    parser = _Parser()
    try:
        parser.feed(html or '')
        parser.close()
    except Exception:  # noqa: BLE001 - best effort on broken markup
        pass
    host = urlparse(url or '').netloc.lower()
    items = _json_ld_items(parser.json_ld)
    dates = {'published': None, 'modified': None}
    for key, slot in DATE_META.items():
        if parser.meta.get(key) and not dates[slot]:
            dates[slot] = iso_date(parser.meta[key])
    for item in items:
        dates['published'] = dates['published'] or iso_date(item.get('datePublished'))
        dates['modified'] = dates['modified'] or iso_date(item.get('dateModified'))
    if not dates['published'] and parser.times:
        dates['published'] = iso_date(parser.times[0])
    if not dates['modified'] and last_modified:
        from email.utils import parsedate_to_datetime
        try:
            dates['modified'] = parsedate_to_datetime(last_modified).date().isoformat()
        except (TypeError, ValueError):
            pass
    main_headings = [h for h in parser.headings if not h['in_chrome']]
    internal = external = 0
    for link in parser.links:
        href = link['href']
        if href.startswith(('#', 'mailto:', 'tel:', 'javascript:')) or link['in_chrome']:
            continue
        target = urlparse(urljoin(url or '', href)).netloc.lower()
        if target == host:
            internal += 1
        else:
            external += 1
    content_images = [i for i in parser.images if not i['in_chrome'] and i['src'] and not i['src'].startswith('data:')]
    words = sum(len(b['text'].split()) for b in parser.blocks)
    words += sum(len(h['text'].split()) for h in main_headings)
    types = _types(items)
    faq = 'FAQPage' in types or any(h['text'].lower().startswith(('faq', 'frequently asked')) for h in main_headings)
    return {
        'url': url, 'host': host,
        'title': clean(parser.title)[:300] or None,
        'description': clean(parser.meta.get('description') or parser.meta.get('og:description'))[:400] or None,
        'canonical': parser.canonical,
        'robots': parser.meta.get('robots'),
        'h1': next((h['text'] for h in main_headings if h['level'] == 1), None),
        'outline': [{'level': h['level'], 'text': h['text']} for h in main_headings if h['level'] in (2, 3)][:60],
        'published': dates['published'], 'modified': dates['modified'],
        'schema_types': types,
        'word_count': words,
        'chrome_words': parser.chrome_words,
        'counts': {'h2': sum(1 for h in main_headings if h['level'] == 2), 'h3': sum(1 for h in main_headings if h['level'] == 3),
                   'ul': parser.counts['ul'], 'ol': parser.counts['ol'], 'list_items': parser.list_items,
                   'table': parser.counts['table'], 'images': len(content_images), 'figure': parser.counts['figure'],
                   'video': parser.counts['video'] + sum(1 for _ in re.finditer(r'youtube\.com/embed|player\.vimeo', html or '')),
                   'code': parser.counts['pre'], 'blockquote': parser.counts['blockquote'],
                   'internal_links': internal, 'external_links': external},
        'images': [{'src': i['src'][:300], 'alt': i['alt'][:200]} for i in content_images][:40],
        'image_alt_coverage': round(sum(1 for i in content_images if i['alt']) / len(content_images), 2) if content_images else None,
        'faq': faq,
        'howto': 'HowTo' in types,
        'question_headings': [h['text'] for h in main_headings if h['text'].endswith('?')][:20],
        'slug_words': slug_words(url),
        'blocks': parser.blocks,   # main text blocks (in memory only: passages + overlap guard; never written in full)
    }


class HtmlContentExtractor:
    """Tool: competitor HTML -> {title, description, dates, outline, counts, images, blocks, ...}."""

    extract = staticmethod(extract)
