"""Capture a bounded, read-only source snapshot shared by independent agents."""

import hashlib
import json
import os
from pathlib import Path

from seo_agents.exploration.checks.tools.helpers import SKIP_DIRS

MAX_FILES = 2000
MAX_FILE_BYTES = 500_000
MAX_SOURCE_BYTES = 20_000_000
ASSET_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.avif', '.ico',
              '.mp4', '.webm', '.mp3', '.wav', '.pdf', '.woff', '.woff2'}
SECRET_EXTS = {'.pem', '.key', '.p12', '.pfx', '.sqlite', '.db'}
SKIP_NAMES = {'credentials', 'credentials.json', 'secrets.json', 'secrets.yaml',
              'id_rsa', 'id_ed25519', 'seo_reports'}


def snapshot_repository(repo_path=None):
    root = Path(repo_path or Path.cwd()).resolve()
    if not root.is_dir():
        raise ValueError('Repository directory does not exist')
    files, assets, limitations = {}, {}, []
    total = count = 0
    exhausted = False
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS | SKIP_NAMES
                         and not d.startswith('.') and not (Path(directory) / d).is_symlink())
        for name in sorted(names):
            path = Path(directory) / name
            if (name.startswith('.') or name.lower() in SKIP_NAMES or path.is_symlink()
                    or path.suffix.lower() in SECRET_EXTS or name.endswith('.lock')
                    or name in {'package-lock.json', 'pnpm-lock.yaml', 'yarn.lock'}):
                continue
            relative = path.relative_to(root).as_posix()
            count += 1
            if count > MAX_FILES:
                limitations.append('File scan capped at ' + str(MAX_FILES) + ' entries.')
                exhausted = True
                break
            if path.suffix.lower() in ASSET_EXTS:
                # Source asset measurements are data, never executable capabilities.
                assets[relative] = {'size_bytes': path.stat().st_size}
                if path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'}:
                    from seo_agents.exploration.checks.tools.asset_probe import probe_asset
                    assets[relative].update(probe_asset(path))
                continue
            with path.open('rb') as stream:
                raw = stream.read(MAX_FILE_BYTES + 1)
            if b'\x00' in raw:
                continue
            try:
                text = raw[:MAX_FILE_BYTES].decode('utf-8')
            except UnicodeDecodeError:
                limitations.append(relative + ': non-UTF-8 source omitted.')
                continue
            if total + len(raw) > MAX_SOURCE_BYTES:
                limitations.append('Source snapshot capped at ' + str(MAX_SOURCE_BYTES) + ' bytes.')
                exhausted = True
                break
            if len(raw) > MAX_FILE_BYTES:
                limitations.append(relative + ': source truncated at ' + str(MAX_FILE_BYTES) + ' bytes.')
            files[relative] = text
            total += len(raw)
        if exhausted:
            break
    if not files:
        raise ValueError('No readable source files found in repository')
    payload = {'files': files, 'assets': assets, 'limitations': limitations}
    payload['sha256'] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return payload
