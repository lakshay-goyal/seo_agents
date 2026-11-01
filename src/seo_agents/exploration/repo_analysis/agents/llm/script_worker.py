"""Stdlib-only worker launched with python -I -B; never imports target code."""

import builtins
import copy
from collections.abc import Mapping
from html.parser import HTMLParser
import importlib.util
import json
from pathlib import Path
import re
import resource
import signal
import sys
from urllib.parse import urljoin


class MarkupParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.nodes = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        node = {'tag': tag, 'attrs': dict(attrs), 'line': self.getpos()[0],
                'column': self.getpos()[1], 'text': '',
                'section': next((n['tag'] for n in reversed(self.stack)
                                 if n['tag'] in {'main', 'nav', 'header', 'footer', 'article', 'section', 'aside'}), 'body')}
        self.nodes.append(node)
        if tag in {'main', 'nav', 'header', 'footer', 'article', 'section', 'aside'}:
            node['section'] = tag
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if any(n['tag'] in {'script', 'style', 'template'} or 'hidden' in n['attrs']
               or n['attrs'].get('aria-hidden') == 'true' for n in self.stack):
            return
        for node in self.stack:
            node['text'] += data


def parse_html(text):
    parser = MarkupParser()
    parser.feed(text)
    parser.close()
    for node in parser.nodes:
        node['text'] = ' '.join(node['text'].split())
    return parser.nodes


def re_sub(pattern, replacement, text, flags=0):
    return re.sub(pattern, replacement, text, flags=flags)


class SourceFiles(Mapping):
    def __init__(self, files):
        self._files = files
        self.accessed = set()

    def __getitem__(self, key):
        value = self._files[key]
        self.accessed.add(key)
        return value

    def __iter__(self):
        return iter(self._files)

    def __len__(self):
        return len(self._files)

    def copy(self):
        return dict(self)


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
    # Linux (the pipeline container) enforces address space; macOS rejects that
    # limit, so local runs check peak resident bytes on a timer instead.
    if sys.platform == 'darwin':
        def check_memory(signum, frame):
            if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss > 512 * 1024 * 1024:
                raise MemoryError('Extractor exceeded resident memory limit')
        signal.signal(signal.SIGALRM, check_memory)
        signal.setitimer(signal.ITIMER_REAL, 0.05, 0.05)
    else:
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    spec = importlib.util.spec_from_file_location('script_policy', Path(__file__).with_name('script_policy.py'))
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    raw = sys.stdin.buffer.read(64_000_001)
    if len(raw) > 64_000_000:
        raise ValueError('Worker input exceeds 64 MB limit')
    payload = json.loads(raw)
    tree = policy.validate_script(payload['source'])
    cache, parse_stats = {}, {'calls': 0, 'cache_hits': 0}
    def cached_html(text):
        if text in cache:
            parse_stats['cache_hits'] += 1
            return copy.deepcopy(cache[text])
        nodes = parse_html(text)
        parse_stats['calls'] += 1
        if len(cache) < 128:
            cache[text] = nodes
        return copy.deepcopy(nodes)
    namespace = {'__builtins__': {key: getattr(builtins, key) for key in policy.BUILTINS},
                 'parse_html': cached_html, 'json_loads': json.loads,
                 're_findall': re.findall, 're_sub': re_sub, 're_escape': re.escape,
                 'resolve_url': urljoin}
    exec(compile(tree, '<verified-extractor>', 'exec'), namespace)
    files = SourceFiles(payload['files'])
    result = namespace['extract'](files, payload['assets'])
    output = json.dumps({'report': result, 'accessed_files': sorted(files.accessed),
                         'html_parsing': parse_stats}, ensure_ascii=False, allow_nan=False)
    if len(output.encode()) > 16_000_000:
        raise ValueError('Report exceeds 16 MB output limit')
    sys.stdout.write(output)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        sys.stderr.write(type(error).__name__ + ': ' + str(error)[:2000])
        sys.exit(1)
