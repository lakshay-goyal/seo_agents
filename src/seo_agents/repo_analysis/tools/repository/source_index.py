"""Line-addressable view over a repository snapshot.

`SourceIndex.evidence()` is the single way extractors record a fact. Each record
holds the file reference (path + line range + JSON pointer into the `files` map)
together with the exact source lines and a small surrounding context window.
"""

from bisect import bisect_right
import fnmatch
import hashlib
from pathlib import PurePosixPath
import re

from seo_agents.repo_analysis.tools.repository.repository_snapshot import file_roles

LANGUAGES = {
    '.js': 'javascript', '.jsx': 'javascript', '.mjs': 'javascript', '.cjs': 'javascript',
    '.ts': 'typescript', '.tsx': 'typescript', '.mts': 'typescript', '.cts': 'typescript',
    '.vue': 'vue', '.svelte': 'svelte', '.astro': 'astro', '.html': 'html', '.htm': 'html',
    '.md': 'markdown', '.mdx': 'mdx', '.markdown': 'markdown', '.json': 'json', '.yaml': 'yaml',
    '.yml': 'yaml', '.toml': 'toml', '.xml': 'xml', '.txt': 'text', '.css': 'css', '.scss': 'scss',
    '.sass': 'sass', '.less': 'less', '.py': 'python', '.rb': 'ruby', '.erb': 'erb', '.php': 'php',
    '.twig': 'twig', '.liquid': 'liquid', '.njk': 'nunjucks', '.hbs': 'handlebars', '.ejs': 'ejs',
    '.pug': 'pug', '.go': 'go', '.rs': 'rust', '.java': 'java', '.kt': 'kotlin', '.cs': 'csharp',
    '.cshtml': 'razor', '.razor': 'razor', '.jinja': 'jinja', '.j2': 'jinja', '.dart': 'dart',
}
TEMPLATE_LANGUAGES = {'javascript', 'typescript', 'vue', 'svelte', 'astro', 'html', 'mdx', 'erb', 'php',
                      'twig', 'liquid', 'nunjucks', 'handlebars', 'ejs', 'pug', 'razor', 'jinja', 'markdown'}
CONTEXT_LINES = 3
# Repository prose (never served): README, changelogs, licences. `docs/` counts as prose unless the repo
# has a documentation-site generator that serves it (Docusaurus, VitePress, MkDocs, Starlight, Nextra...).
DOC_NAMES = re.compile(r'(?:^|/)(?:README|CHANGELOG|CHANGES|HISTORY|LICENSE|LICENCE|CONTRIBUTING|CODE_OF_CONDUCT|'
                       r'SECURITY|AUTHORS|NOTICE|UPGRADING|MIGRATION)[^/]*$', re.I)
DOCS_DIR = re.compile(r'^(?:docs?|documentation|\.github)/', re.I)
DOCS_SITE_MARKERS = re.compile(r'(?:^|/)(?:docusaurus\.config\.[cm]?[jt]s|mkdocs\.ya?ml|\.vitepress/config\.[cm]?[jt]s|'
                               r'\.vuepress/config\.[cm]?[jt]s|book\.toml|theme\.config\.[jt]sx?|_config\.ya?ml)$|starlight')
MAX_SNIPPET_LINES = 80
OPENERS, CLOSERS = '([{', ')]}'


def pointer_escape(token):
    return token.replace('~', '~0').replace('/', '~1')


def glob_match(path, pattern):
    """Match a glob against the full relative path or just the file name."""
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(PurePosixPath(path).name, pattern)


def language_of(path):
    name = PurePosixPath(path).name.lower()
    if name in {'dockerfile', 'makefile', 'gemfile', 'procfile'}:
        return name
    if name in {'.htaccess', '_redirects', '_headers'}:
        return 'server-config'
    if name.endswith('.blade.php'):
        return 'blade'
    return LANGUAGES.get(PurePosixPath(name).suffix, 'other')


class SourceIndex:
    """Read-only index over `snapshot['files']` with evidence bookkeeping."""

    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.files = snapshot['files']
        self.assets = snapshot.get('assets', {})
        self._offsets, self._lines = {}, {}
        self.records = {}          # id -> evidence record
        self._docs_served, self._roles = None, None
        self.referenced = {}       # file -> [record ids]

    # ---- file access -------------------------------------------------
    def is_doc(self, path):
        """Repository documentation that is never served as a page (excluded from content/keywords)."""
        if self._roles is None:
            self._roles = {row['path']: row.get('roles') or [] for row in self.snapshot.get('inventory', [])
                           if isinstance(row, dict) and row.get('path')}
        if 'doc' in self._roles.get(path, ()) or 'doc' in file_roles(path):
            return True
        if DOC_NAMES.search(path):
            return True
        if DOCS_DIR.match(path):
            if self._docs_served is None:
                self._docs_served = any(DOCS_SITE_MARKERS.search(p) for p in self.files)
            return not self._docs_served
        return False

    def paths(self, *globs, languages=None, exclude_tests=True, exclude_docs=False):
        """Captured paths matching any glob (all paths when none given)."""
        result = []
        for path in sorted(self.files):
            if exclude_tests and 'tests-fixtures' in file_roles(path):
                continue
            if exclude_docs and self.is_doc(path):
                continue
            if globs and not any(glob_match(path, g) for g in globs):
                continue
            if languages and language_of(path) not in languages:
                continue
            result.append(path)
        return result

    def templates(self):
        """Template/content files that can render a page (repository prose excluded)."""
        return self.paths(languages=TEMPLATE_LANGUAGES, exclude_docs=True)

    def lines(self, path):
        if path not in self._lines:
            self._lines[path] = self.files[path].splitlines()
        return self._lines[path]

    def line_of(self, path, offset):
        if path not in self._offsets:
            starts, position = [0], 0
            for line in self.files[path].splitlines(keepends=True):
                position += len(line)
                starts.append(position)
            self._offsets[path] = starts
        return bisect_right(self._offsets[path], offset)

    # ---- searching ---------------------------------------------------
    def search(self, pattern, paths=None, flags=re.IGNORECASE, limit_per_file=200):
        """Yield (path, match) for a regex over the given (default: all) paths."""
        regex = re.compile(pattern, flags) if isinstance(pattern, str) else pattern
        for path in (paths if paths is not None else self.paths()):
            for count, match in enumerate(regex.finditer(self.files[path])):
                if count >= limit_per_file:
                    break
                yield path, match

    def block_end(self, path, offset, max_chars=20_000):
        """End offset of the first balanced bracket block starting at/after offset."""
        text, depth, index, quote, started = self.files[path], 0, offset, None, False
        limit = min(len(text), offset + max_chars)
        while index < limit:
            char = text[index]
            if quote:
                if char == '\\':
                    index += 2
                    continue
                if char == quote:
                    quote = None
            elif char in '"\'`':
                quote = char
            elif text.startswith('//', index) and not text.startswith('://', index - 1):
                newline = text.find('\n', index)
                index = limit if newline < 0 else newline
                continue
            elif char in OPENERS:
                depth += 1
                started = True
            elif char in CLOSERS:
                depth -= 1
                if started and depth <= 0:
                    # `fn(args) {body}`, `(args) => {...}`, `fn(): Type {`: keep going into the body.
                    rest = text[index + 1:index + 200].lstrip()
                    if char == ')' and (rest.startswith(('{', '=>')) or re.match(r':\s*[\w<>\[\]|. ,]+\s*\{', rest)):
                        depth, started = 0, False
                        index += 1
                        continue
                    return index + 1
            elif char == ';' and not started:
                return index + 1
            index += 1
        return limit

    # ---- evidence ----------------------------------------------------
    def evidence(self, kind, path, start, end=None, *, block=False, **fields):
        """Record a fact with its file reference and exact source text.

        `start`/`end` are character offsets (ints) into the file. With
        `block=True` the record extends to the end of the balanced bracket
        block that follows `start`, capturing whole objects/functions.
        """
        if block:
            end = self.block_end(path, start)
        end = start if end is None else max(start, end - 1)
        first, last = self.line_of(path, start), self.line_of(path, end)
        return self.evidence_lines(kind, path, first, last, **fields)

    def evidence_lines(self, kind, path, first, last=None, **fields):
        lines = self.lines(path)
        last = first if last is None else last
        first, last = max(1, first), max(first, min(last, max(len(lines), 1)))
        truncated = last - first + 1 > MAX_SNIPPET_LINES
        snippet_last = first + MAX_SNIPPET_LINES - 1 if truncated else last
        before_first, after_last = max(1, first - CONTEXT_LINES), min(len(lines), last + CONTEXT_LINES)
        record_id = kind + ':' + path + ':' + str(first)
        if record_id in self.records:
            suffix = 2
            while record_id + '#' + str(suffix) in self.records:
                suffix += 1
            record_id += '#' + str(suffix)
        record = {
            'id': record_id, 'kind': kind,
            'location': {'file': path, 'line_start': first, 'line_end': last,
                         'file_ref': '/files/' + pointer_escape(path)},
            **fields,
            'code': '\n'.join(lines[first - 1:snippet_last]),
            'code_truncated': truncated,
            'context': {'line_start': before_first, 'line_end': after_last,
                        'text': '\n'.join(lines[before_first - 1:after_last])},
        }
        self.records[record_id] = record
        self.referenced.setdefault(path, []).append(record_id)
        return record

    def file_entry(self, path, include_content=True):
        text = self.files[path]
        entry = {'path': path, 'language': language_of(path), 'roles': file_roles(path),
                 'line_count': len(self.lines(path)), 'size_bytes': len(text.encode()),
                 'sha256': hashlib.sha256(text.encode()).hexdigest(),
                 'referenced_by': self.referenced.get(path, [])}
        if include_content:
            entry['content'] = text
        return entry


def strip_quotes(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'`':
        return value[1:-1]
    return value
