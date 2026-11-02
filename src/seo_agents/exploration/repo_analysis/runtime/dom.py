"""One page summary shape for every lens (raw HTML, rendered DOM, built file).

`summarize_html(html, url)` parses served/built HTML with the stdlib parser. The
Playwright lens (`render.py`) returns the same keys from the live DOM, plus
layout facts (rendered sizes, above-the-fold, visibility) only a browser knows.
Keeping one shape lets attribution, validators and the site model treat lenses
interchangeably and compare them field by field.
"""

from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from urllib.parse import urljoin, urlsplit

SEMANTIC = {'main', 'nav', 'header', 'footer', 'aside', 'article', 'section'}
CHROME = {'header', 'nav', 'footer', 'aside'}
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
BLOCK_TAGS = {'p', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'td', 'th', 'dd', 'dt', 'figcaption',
              'summary', 'caption', 'pre', 'label', 'button'}
SKIP_TEXT = {'script', 'style', 'noscript', 'template', 'svg', 'head', 'title'}


def norm(value):
    return ' '.join(str(value or '').split())


def words(text):
    return len(re.findall(r"[^\W_]+(?:['’][^\W_]+)*", text or '', re.UNICODE))


class _Summary(HTMLParser):
    def __init__(self, url):
        super().__init__(convert_charrefs=True)
        self.url = url
        self.base = url
        self.stack = []
        self.counts = [{}]
        self.out = {'url': url, 'lang': None, 'title': None, 'meta': [], 'head_links': [], 'json_ld': [],
                    'headings': [], 'links': [], 'images': [], 'blocks': [], 'iframes': 0, 'scripts': 0,
                    'stylesheets': 0, 'inline_script_bytes': 0}
        self.title_parts = None
        self.script = None
        self.body_text, self.main_text = [], []

    def path(self):
        return ' > '.join(f"{n['tag']}:nth-of-type({n['nth']})" for n in self.stack[-6:])

    def section(self):
        return next((n['tag'] for n in reversed(self.stack) if n['tag'] in SEMANTIC), 'body')

    def in_main(self):
        tags = {n['tag'] for n in self.stack}
        return not tags & CHROME

    def handle_starttag(self, tag, attrs):
        attrs = {k.lower(): (v if v is not None else '') for k, v in attrs}
        siblings = self.counts[-1]
        siblings[tag] = siblings.get(tag, 0) + 1
        node = {'tag': tag, 'attrs': attrs, 'nth': siblings[tag], 'text': []}
        if tag == 'base' and attrs.get('href'):
            self.base = urljoin(self.url, attrs['href'])
        if tag == 'html':
            self.out['lang'] = attrs.get('lang') or None
        elif tag == 'meta':
            self.out['meta'].append({k: attrs.get(k) for k in ('name', 'property', 'content', 'http-equiv', 'charset', 'itemprop') if k in attrs})
        elif tag == 'link':
            self.out['head_links'].append({k: attrs.get(k) for k in ('rel', 'href', 'hreflang', 'type', 'sizes', 'as', 'media') if k in attrs})
            if 'stylesheet' in (attrs.get('rel') or '').lower():
                self.out['stylesheets'] += 1
        elif tag == 'title' and self.out['title'] is None and not any(n['tag'] == 'svg' for n in self.stack):
            self.title_parts = []
        elif tag == 'script':
            self.out['scripts'] += 1
            if (attrs.get('type') or '').lower() == 'application/ld+json':
                self.script = []
        elif tag == 'img':
            self.out['images'].append({'src': attrs.get('src'), 'resolved': self._resolve(attrs.get('src')),
                                       'alt': attrs.get('alt') if 'alt' in attrs else None,
                                       'width': attrs.get('width'), 'height': attrs.get('height'),
                                       'loading': attrs.get('loading'), 'decoding': attrs.get('decoding'),
                                       'fetchpriority': attrs.get('fetchpriority'), 'srcset': attrs.get('srcset'),
                                       'sizes': attrs.get('sizes'), 'section': self.section(), 'in_main': self.in_main(),
                                       'path': self.path() + f' > img:nth-of-type({siblings[tag]})'})
            for ancestor in self.stack:
                if ancestor['tag'] == 'a' and attrs.get('alt'):
                    ancestor.setdefault('img_alt', []).append(attrs['alt'])
        elif tag == 'iframe':
            self.out['iframes'] += 1
        if tag in VOID:
            return
        self.stack.append(node)
        self.counts.append({})
        if tag == 'a' and 'href' in attrs:
            node['link'] = {'href': attrs.get('href'), 'resolved': self._resolve(attrs.get('href')), 'text': None,
                            'aria_label': attrs.get('aria-label'), 'title': attrs.get('title'), 'rel': attrs.get('rel'),
                            'target': attrs.get('target'), 'section': self.section(), 'in_main': self.in_main(),
                            'path': self.path()}
        if re.fullmatch(r'h[1-6]', tag):
            node['heading'] = {'level': int(tag[1]), 'text': None, 'section': self.section(), 'path': self.path()}

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == 'title' and self.title_parts is not None:
            self.out['title'] = norm(''.join(self.title_parts))
            self.title_parts = None
        if tag == 'script' and self.script is not None:
            raw = ''.join(self.script)
            self.out['json_ld'].append(parse_json_ld(raw))
            self.script = None
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'] == tag:
                for node in reversed(self.stack[index:]):
                    self._close(node)
                del self.stack[index:]
                del self.counts[index + 1:]
                return

    def _close(self, node):
        text = norm(''.join(node['text']))
        if 'link' in node:
            link = node['link']
            link['text'] = text
            if node.get('img_alt'):
                link['img_alt'] = node['img_alt']
            self.out['links'].append(link)
        if 'heading' in node:
            node['heading']['text'] = text
            self.out['headings'].append(node['heading'])
        if node['tag'] in BLOCK_TAGS and text and not any(n['tag'] in BLOCK_TAGS for n in self.stack[:-1] if n is not node):
            self.out['blocks'].append({'tag': node['tag'], 'text': text[:2000], 'section': next(
                (n['tag'] for n in reversed(self.stack) if n['tag'] in SEMANTIC and n is not node), 'body'),
                'in_main': self.in_main(), 'path': self.path()})

    def handle_data(self, data):
        if self.title_parts is not None:
            self.title_parts.append(data)
        if self.script is not None:
            self.script.append(data)
            return
        tags = {n['tag'] for n in self.stack}
        if tags & SKIP_TEXT or any('hidden' in n['attrs'] or n['attrs'].get('aria-hidden') == 'true' for n in self.stack):
            if 'script' in tags:
                self.out['inline_script_bytes'] += len(data.encode())
            return
        for node in self.stack:
            node['text'].append(data)
        if data.strip() and 'body' in tags | {'body'}:
            self.body_text.append(data)
            if self.in_main():
                self.main_text.append(data)

    def _resolve(self, href):
        if href is None:
            return None
        try:
            return urljoin(self.base, href.strip())
        except ValueError:
            return None


def parse_json_ld(raw):
    try:
        value = json.loads(raw)
    except ValueError as error:
        return {'valid': False, 'error': str(error)[:200], 'types': [], 'raw': raw[:4000]}
    types = []

    def collect(item):
        if isinstance(item, dict):
            kind = item.get('@type')
            if isinstance(kind, str):
                types.append(kind)
            elif isinstance(kind, list):
                types.extend(str(k) for k in kind)
            for child in item.values():
                if isinstance(child, (dict, list)):
                    collect(child)
        elif isinstance(item, list):
            for child in item:
                collect(child)
    collect(value)
    return {'valid': True, 'types': sorted(set(types)), 'value': value}


def summarize_html(html, url):
    parser = _Summary(url)
    try:
        parser.feed(html or '')
        parser.close()
    except (AssertionError, ValueError):
        pass
    for node in reversed(parser.stack):
        parser._close(node)
    out = parser.out
    out['word_count'] = words(' '.join(parser.body_text))
    out['main_word_count'] = words(' '.join(parser.main_text))
    return finalize(out)


def finalize(out):
    """Derive head fields from meta/link lists; identical for every lens."""
    meta = out.get('meta') or []
    def meta_value(key, attr='name'):
        for row in meta:
            if (row.get(attr) or '').lower() == key and row.get('content') is not None:
                return row['content']
        return None
    out['description'] = meta_value('description')
    out['robots'] = meta_value('robots')
    out['viewport'] = meta_value('viewport')
    out['og'] = {row['property'][3:]: row.get('content') for row in meta
                 if (row.get('property') or '').lower().startswith('og:')}
    out['twitter'] = {(row.get('name') or row.get('property'))[8:]: row.get('content') for row in meta
                      if (row.get('name') or row.get('property') or '').lower().startswith('twitter:')}
    links = out.get('head_links') or []
    canonical = [row.get('href') for row in links if 'canonical' in (row.get('rel') or '').lower().split()]
    out['canonical'] = canonical[0] if canonical else None
    out['canonical_count'] = len(canonical)
    out['hreflang'] = [{'hreflang': row.get('hreflang'), 'href': row.get('href')} for row in links
                       if 'alternate' in (row.get('rel') or '').lower().split() and row.get('hreflang')]
    out['h1'] = [h['text'] for h in out.get('headings', []) if h['level'] == 1]
    return out


def same_origin(a, b):
    pa, pb = urlsplit(a or ''), urlsplit(b or '')
    return (pa.scheme, (pa.hostname or '').lower(), pa.port) == (pb.scheme, (pb.hostname or '').lower(), pb.port)
