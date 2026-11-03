"""Capture a bounded, read-only source snapshot shared by independent agents."""

import hashlib
import json
import re
import os
import codecs
import stat
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from seo_agents.repo_analysis.tools.repository.repository_constants import SKIP_DIRS

MAX_FILES = 2000          # source/text files loaded; binary assets have their own budget below
MAX_ASSETS = 5000
MAX_FILE_BYTES = 500_000
MAX_SOURCE_BYTES = 20_000_000
ASSET_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.avif', '.ico',
              '.mp4', '.webm', '.mp3', '.wav', '.pdf', '.woff', '.woff2'}
SECRET_EXTS = {'.pem', '.key', '.p12', '.pfx', '.sqlite', '.db'}
SKIP_NAMES = {'credentials', 'credentials.json', 'secrets.json', 'secrets.yaml',
              'id_rsa', 'id_ed25519'}
HIDDEN_DIRS = {'.well-known', '.vitepress', '.vuepress', '.config'}
HIDDEN_FILES = {'.htaccess', '.eleventy.js', '.eleventy.cjs', '.eleventy.json', '.nvmrc', '.node-version', '.tool-versions',
                '.python-version', '.ruby-version'}
# Env files are read only to learn the site's public origin: every value is redacted except URL-shaped
# values of non-secret keys (SITE_URL=https://example.com). Never stored verbatim.
ENV_FILES = {'.env', '.env.production', '.env.example', '.env.sample'}
SECRET_KEY = re.compile(r'(?i)SECRET|TOKEN|PASSWORD|PASSWD|PRIVATE|API_?KEY|ACCESS_?KEY|CREDENTIAL|AUTH|SESSION|SALT|DSN|DATABASE|_KEY$')
# Generated/build output and caches: never source, and indexing them misattributes edits.
GENERATED_DIRS = {'out', '_site', '.next', '.nuxt', '.output', '.svelte-kit', '.astro', '.vercel', '.netlify', 'coverage',
                  'storybook-static', '.turbo', '.cache', '.parcel-cache', '.docusaurus', '.expo', 'resources/_gen'}
# Walk these first so an asset-heavy public/ can never starve the source budget.
SOURCE_DIRS = ('src', 'app', 'pages', 'components', 'layouts', 'layout', 'templates', 'template-parts', 'content', 'routes',
               'sections', 'snippets', 'blocks', 'theme', 'themes', 'views', 'resources', 'lib', 'config', 'data', 'locales',
               '_layouts', '_includes', '_posts', '_data', 'archetypes', 'i18n')
DOC_NAME = re.compile(r'^(?:README|CHANGELOG|CHANGES|HISTORY|CONTRIBUTING|CODE_OF_CONDUCT|LICEN[CS]E|SECURITY|AUTHORS|NOTICE)(?:\.[\w.]+)?$', re.I)
DOCS_SITE_MARKERS = re.compile(r'(?:^|/)(?:docusaurus\.config\.[cm]?[jt]s|mkdocs\.ya?ml|\.vitepress/config\.[cm]?[jt]s|book\.toml|'
                               r'docs/(?:_config\.ya?ml|\.vuepress/)|antora\.yml)$')
MANIFESTS = ('routes-manifest.json', 'prerender-manifest.json',
             'server/app-paths-manifest.json', 'server/pages-manifest.json')


def file_roles(path):
    """Candidate contribution, never a claim that a file has been understood."""
    p = PurePosixPath(path.lower())
    name, parts = p.name, set(p.parts)
    roles = []
    if name == 'package.json' or 'config' in name or name in {'composer.json', 'pyproject.toml', '.htaccess'}:
        roles.append('configuration')
    if any(word in name for word in ('route', 'sitemap', 'manifest')) or parts & {'pages', 'routes', 'app'}:
        roles.append('routing')
    if any(word in name for word in ('layout', 'header', 'footer', 'navbar')):
        roles.append('layout')
    if any(word in name for word in ('metadata', 'seo', 'head.', 'robots', 'redirect', 'middleware')):
        roles.append('metadata-policy')
    if parts & {'locales', 'i18n', 'translations', 'lang'}:
        roles.append('localization')
    if p.suffix in {'.md', '.mdx', '.json', '.yaml', '.yml', '.csv'} or parts & {'content', 'data', 'posts', 'collections'}:
        roles.append('content-data')
    if p.suffix in {'.html', '.htm', '.vue', '.svelte', '.astro', '.tsx', '.jsx', '.twig', '.liquid', '.php', '.erb', '.ejs', '.hbs'}:
        roles.append('template')
    if parts & {'test', 'tests', 'fixtures', '__tests__'} or '.test.' in name or '.spec.' in name:
        roles.append('tests-fixtures')
    if DOC_NAME.match(PurePosixPath(path).name) or str(p).startswith('.github/'):
        roles.append('doc')  # repository documentation: never served, never page content or keywords
    if p.suffix in ASSET_EXTS:
        roles.append('asset')
    return roles or ['implementation']


VOLATILE_FIELDS = {'mtime_ns'}


def content_identity(payload):
    """Identity of the captured content only: timestamps are excluded so a fresh clone
    of the same commit yields the same hash and checkpoint resume can match."""
    stable = {key: payload[key] for key in ('files', 'assets', 'limitations', 'coverage')}
    stable['inventory'] = [{k: v for k, v in row.items() if k not in VOLATILE_FIELDS} for row in payload['inventory']]
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()


def secret_name(name):
    name = name.lower()
    return (name in SKIP_NAMES or name in {'.npmrc', '.pypirc', '.netrc'}
            or name.startswith(('.env', 'credentials.', 'secrets.', 'service-account', 'id_rsa', 'id_ed25519'))
            or PurePosixPath(name).suffix in SECRET_EXTS)


def redact_env(text):
    """KEY=value lines -> KEY=<redacted>, except public URL values of non-secret keys (host hints for the manifest)."""
    out = []
    for line in text.splitlines()[:400]:
        match = re.match(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$', line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip().strip('"\'')
        url = re.fullmatch(r'https?://([A-Za-z0-9.\-]+(?::\d+)?)(/[\w\-./]*)?', value)
        if url and not SECRET_KEY.search(key) and '@' not in value:
            out.append(f'{key}={value}')
        else:
            out.append(f'{key}=<redacted>')
    return '\n'.join(out) + ('\n' if out else '')


def snapshot_repository(repo_path=None, *, max_files=None, max_file_bytes=None, max_source_bytes=None):
    root = Path(repo_path or Path.cwd()).resolve()
    if not root.is_dir():
        raise ValueError('Repository directory does not exist')
    max_files = MAX_FILES if max_files is None else max_files
    max_file_bytes = MAX_FILE_BYTES if max_file_bytes is None else max_file_bytes
    max_source_bytes = MAX_SOURCE_BYTES if max_source_bytes is None else max_source_bytes
    if min(max_files, max_file_bytes, max_source_bytes) < 1:
        raise ValueError('Snapshot budgets must be positive')
    files, assets, limitations, inventory, env_hints = {}, {}, [], [], {}
    total = count = asset_count = source_reads = asset_probes = 0
    hugo_site = any((root / name).is_file() and re.search(r'(?im)^\s*(?:baseURL|theme|languageCode)\s*[=:]', (root / name).read_text(errors='replace'))
                    for name in ('hugo.toml', 'hugo.yaml', 'hugo.yml', 'config.toml', 'config.yaml', 'config.yml')) or (root / 'archetypes').is_dir()
    walk_errors = []

    def capture(path):
        nonlocal total, count, asset_count, source_reads, asset_probes
        relative = path.relative_to(root).as_posix()
        row = {'path': relative, 'kind': 'file', 'roles': file_roles(relative)}
        inventory.append(row)
        if path.name in ENV_FILES and not path.is_symlink():
            # The file stays excluded (never in `files`, never shown to a model); only public URL values of
            # non-secret keys are kept as host hints for the URL manifest.
            try:
                if path.stat().st_size <= 200_000:
                    hints = [line for line in redact_env(path.read_text(errors='replace')).splitlines() if not line.endswith('=<redacted>')]
                    if hints:
                        env_hints[relative] = '\n'.join(hints) + '\n'
            except OSError:
                pass
        if path.is_symlink() or secret_name(path.name):
            row.update(status='excluded', reason='symlink' if path.is_symlink() else 'credential-or-secret')
            return
        if path.name.startswith('.') and path.name not in HIDDEN_FILES:
            row.update(status='excluded', reason='hidden-file-outside-declared-scope')
            return
        if path.name.endswith('.lock') or path.name in {'package-lock.json', 'pnpm-lock.yaml'}:
            row.update(status='excluded', reason='dependency-lockfile')
            return
        try:
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):
                row.update(status='excluded', reason='not-a-regular-file')
                return
            row['size_bytes'] = info.st_size
            is_asset = path.suffix.lower() in ASSET_EXTS
            if is_asset and asset_count >= MAX_ASSETS:
                row.update(status='not_loaded', reason='asset-budget', mtime_ns=info.st_mtime_ns)
                return
            if not is_asset and (count >= max_files or total >= max_source_bytes):
                row.update(status='not_loaded', reason='snapshot-budget', mtime_ns=info.st_mtime_ns)
                return
            if is_asset:
                asset_count += 1
            else:
                count += 1
            if is_asset:
                assets[relative] = {'size_bytes': info.st_size}
                if info.st_size <= 10_000_000:
                    # Content hash, not mtime: identity must survive a fresh clone.
                    assets[relative]['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
                if path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'}:
                    from seo_agents.repo_analysis.tools.content.asset_probe import probe_asset
                    asset_probes += 1
                    assets[relative].update(probe_asset(path))
                row.update(status='asset', mtime_ns=info.st_mtime_ns)
                return
            budget = min(max_file_bytes, max_source_bytes - total)
            source_reads += 1
            with path.open('rb') as stream:
                raw = stream.read(budget + 1)
            if b'\x00' in raw:
                row.update(status='excluded', reason='binary-file')
                return
            truncated = len(raw) > budget
            text = codecs.getincrementaldecoder('utf-8')().decode(raw[:budget], final=not truncated)
            files[relative] = text
            total += len(raw[:budget])
            row.update(status='truncated' if truncated else 'captured',
                       captured_bytes=len(text.encode()), sha256=hashlib.sha256(text.encode()).hexdigest())
            if truncated:
                row.update(reason='per-file-or-total-byte-budget', mtime_ns=info.st_mtime_ns)
        except UnicodeDecodeError:
            row.update(status='excluded', reason='non-UTF-8')
        except OSError as error:
            row.update(status='unreadable', reason=type(error).__name__)

    for directory, dirs, names in os.walk(root, followlinks=False,
                                         onerror=lambda error: walk_errors.append(type(error).__name__)):
        kept = []
        at_root = Path(directory) == root

        def walk_order(name):  # source dirs first, then other dirs, static/public asset dirs last
            lowered = name.lower()
            return (0 if lowered in SOURCE_DIRS else 2 if lowered in {'public', 'static', 'assets', 'images', 'img', 'media', 'uploads', 'fonts'} else 1, name)
        for name in sorted(dirs, key=walk_order):
            path = Path(directory) / name
            relative_dir = path.relative_to(root).as_posix()
            reason = ('symlink' if path.is_symlink() else 'credential-or-secret' if secret_name(name)
                      else 'dependencies-or-generated' if name in SKIP_DIRS | {'vendor', 'seo_reports'}
                      else 'build-output' if name in GENERATED_DIRS or relative_dir in GENERATED_DIRS
                      else 'hugo-build-output' if at_root and name == 'public' and hugo_site
                      else 'hidden-directory-outside-declared-scope' if name.startswith('.') and name not in HIDDEN_DIRS else None)
            if reason:
                inventory.append({'path': path.relative_to(root).as_posix(), 'kind': 'subtree',
                                  'status': 'excluded', 'reason': reason, 'descendant_count': None})
                if name == '.next' and not path.is_symlink():
                    for manifest in MANIFESTS:
                        candidate = path / manifest
                        if candidate.is_file() and not any(p.is_symlink() for p in [candidate, candidate.parent]):
                            capture(candidate)
            else:
                kept.append(name)
        dirs[:] = kept
        # Prioritize configuration within a directory; inventory continues after all caps.
        for name in sorted(names, key=lambda n: ('configuration' not in file_roles(n), n)):
            capture(Path(directory) / name)
    docs_site = any(DOCS_SITE_MARKERS.search(row['path']) for row in inventory)
    doc_files = []
    for row in inventory:
        if row['kind'] != 'file':
            continue
        if not docs_site and row['path'].startswith('docs/') and 'doc' not in row['roles']:
            row['roles'] = row['roles'] + ['doc']
        if 'doc' in row['roles']:
            doc_files.append(row['path'])
    if not files:
        limitations.append('No textual source captured; inspect inventory exclusions, unreadable files and budgets.')
    counts = Counter(row['status'] for row in inventory if row['kind'] == 'file')
    for status in ('truncated', 'not_loaded', 'unreadable'):
        if counts[status]:
            limitations.append(str(counts[status]) + ' files: ' + status + '; query inventory for paths and reasons.')
    if walk_errors:
        limitations.append('Directory enumeration failed: ' + ', '.join(walk_errors))
    coverage = {'inventory_complete_within_scope': not walk_errors,
                'capture_complete_within_scope': not walk_errors and not any(counts[s] for s in ('truncated', 'not_loaded', 'unreadable')),
                'file_status_counts': dict(counts), 'in_scope_files': sum(counts[s] for s in ('captured', 'truncated', 'not_loaded', 'unreadable', 'asset')),
                'excluded_subtrees': sum(r['kind'] == 'subtree' for r in inventory),
                'source_bytes_captured': total, 'source_read_attempts': source_reads, 'asset_probe_attempts': asset_probes,
                'limits': {'max_files_loaded': max_files, 'max_file_bytes': max_file_bytes, 'max_source_bytes': max_source_bytes},
                'denominator_note': 'All enumerated in-scope files; excluded subtree descendants are not counted. Listed/captured does not mean understood.'}
    coverage['assets_loaded'] = asset_count
    coverage['limits']['max_assets'] = MAX_ASSETS
    payload = {'files': files, 'assets': assets, 'limitations': limitations, 'doc_files': sorted(doc_files),
               'env_hints': env_hints,
               'inventory': sorted(inventory, key=lambda r: r['path']), 'coverage': coverage}
    payload['sha256'] = content_identity(payload)
    payload['observed_at'] = datetime.now(timezone.utc).isoformat()
    return payload


class RepositorySnapshotTool:
    """Tool: capture one bounded, read-only snapshot of a repository (content-hash identity, secrets redacted)."""

    name = 'repository_snapshot'

    def capture(self, repo_path=None, *, max_files=None, max_file_bytes=None, max_source_bytes=None):
        return snapshot_repository(repo_path, max_files=max_files, max_file_bytes=max_file_bytes,
                                   max_source_bytes=max_source_bytes)
