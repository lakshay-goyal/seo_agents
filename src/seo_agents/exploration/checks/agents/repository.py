"""Capture a bounded, read-only source snapshot shared by independent agents."""

import hashlib
import json
import os
import codecs
import stat
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from seo_agents.exploration.checks.tools.helpers import SKIP_DIRS

MAX_FILES = 2000
MAX_FILE_BYTES = 500_000
MAX_SOURCE_BYTES = 20_000_000
ASSET_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.avif', '.ico',
              '.mp4', '.webm', '.mp3', '.wav', '.pdf', '.woff', '.woff2'}
SECRET_EXTS = {'.pem', '.key', '.p12', '.pfx', '.sqlite', '.db'}
SKIP_NAMES = {'credentials', 'credentials.json', 'secrets.json', 'secrets.yaml',
              'id_rsa', 'id_ed25519'}
HIDDEN_DIRS = {'.well-known', '.vitepress', '.vuepress', '.config'}
HIDDEN_FILES = {'.htaccess', '.eleventy.js', '.eleventy.cjs', '.eleventy.json'}
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
    if p.suffix in ASSET_EXTS:
        roles.append('asset')
    return roles or ['implementation']


def secret_name(name):
    name = name.lower()
    return (name in SKIP_NAMES or name in {'.npmrc', '.pypirc', '.netrc'}
            or name.startswith(('.env', 'credentials.', 'secrets.', 'service-account', 'id_rsa', 'id_ed25519'))
            or PurePosixPath(name).suffix in SECRET_EXTS)


def snapshot_repository(repo_path=None, *, max_files=None, max_file_bytes=None, max_source_bytes=None):
    root = Path(repo_path or Path.cwd()).resolve()
    if not root.is_dir():
        raise ValueError('Repository directory does not exist')
    max_files = MAX_FILES if max_files is None else max_files
    max_file_bytes = MAX_FILE_BYTES if max_file_bytes is None else max_file_bytes
    max_source_bytes = MAX_SOURCE_BYTES if max_source_bytes is None else max_source_bytes
    if min(max_files, max_file_bytes, max_source_bytes) < 1:
        raise ValueError('Snapshot budgets must be positive')
    files, assets, limitations, inventory = {}, {}, [], []
    total = count = source_reads = asset_probes = 0
    walk_errors = []

    def capture(path):
        nonlocal total, count, source_reads, asset_probes
        relative = path.relative_to(root).as_posix()
        row = {'path': relative, 'kind': 'file', 'roles': file_roles(relative)}
        inventory.append(row)
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
            if count >= max_files or total >= max_source_bytes:
                row.update(status='not_loaded', reason='snapshot-budget', mtime_ns=info.st_mtime_ns)
                return
            count += 1
            if path.suffix.lower() in ASSET_EXTS:
                assets[relative] = {'size_bytes': info.st_size}
                if path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'}:
                    from seo_agents.exploration.checks.tools.asset_probe import probe_asset
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
        for name in sorted(dirs):
            path = Path(directory) / name
            reason = ('symlink' if path.is_symlink() else 'credential-or-secret' if secret_name(name)
                      else 'dependencies-or-generated' if name in SKIP_DIRS | {'vendor', 'seo_reports'}
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
    payload = {'files': files, 'assets': assets, 'limitations': limitations,
               'inventory': sorted(inventory, key=lambda r: r['path']), 'coverage': coverage}
    payload['sha256'] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    payload['observed_at'] = datetime.now(timezone.utc).isoformat()
    return payload
