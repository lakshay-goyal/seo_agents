"""Stage B: build and serve the target site (best effort, time-boxed) so lenses can measure it.

Order of preference:
1. `site_url` given              -> measure the deployed site (no build);
2. static HTML already on disk   -> serve it (plain HTML repos, committed `docs/`, `out/`);
3. build enabled (SEO_BUILD=1)   -> install + build with the detected package manager or
                                    generator (npm/pnpm/yarn/bun, hugo, jekyll, eleventy),
                                    then serve the output dir, or start the preview server;
4. otherwise                     -> source-only mode, with the reason recorded.

Server-rendered stacks that need their own runtime (WordPress/PHP, Shopify, Django,
Rails, Laravel, .NET, Ghost) are measured only through `site_url`; their source is
still fully analysed by the source lens.

Building runs repository code. It is meant for the sandbox container only and is
opt-in. Network access during install is whatever the container allows.
"""

from __future__ import annotations

import functools
import http.server
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import threading
import time
from urllib.error import HTTPError
from urllib.request import urlopen

STATIC_DIRS = ('out', 'dist', 'build', '_site', 'public', '.output/public', 'docs', 'site', 'www', '.vitepress/dist',
               'build/client', '.svelte-kit/output/prerendered/pages', 'storybook-static')
INSTALL = {
    'npm': lambda root: ['npm', 'ci', '--no-audit', '--no-fund'] if (root / 'package-lock.json').exists()
           else ['npm', 'install', '--no-audit', '--no-fund'],
    'pnpm': lambda root: ['pnpm', 'install', '--frozen-lockfile'],
    'yarn': lambda root: ['yarn', 'install', '--frozen-lockfile'],
    'bun': lambda root: ['bun', 'install'],
}


class CleanURLHandler(http.server.SimpleHTTPRequestHandler):
    """Static hosting semantics: /about -> about.html or about/index.html; custom 404.html;
    no directory listings; optional SPA fallback (unknown extension-less paths -> /index.html),
    which is how Vercel/Netlify rewrites serve client-routed apps."""

    spa_fallback = False

    def log_message(self, *args):
        return

    def list_directory(self, path):
        return self._not_found()

    def _not_found(self):
        not_found = os.path.join(self.directory, '404.html')
        body = open(not_found, 'rb').read() if os.path.exists(not_found) else b'<h1>Not Found</h1>'
        self.send_response(404)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        return _BytesFile(body)

    def send_head(self):
        path = self.translate_path(self.path)
        if not os.path.exists(path):
            clean = self.path.split('?', 1)[0].split('#', 1)[0].rstrip('/')
            if os.path.exists(path.rstrip('/') + '.html'):
                self.path = clean + '.html'
                return super().send_head()
            if self.spa_fallback and '.' not in clean.rsplit('/', 1)[-1]:
                self.path = '/index.html'
                return super().send_head()
            return self._not_found()
        if os.path.isdir(path) and not os.path.exists(os.path.join(path, 'index.html')):
            if self.spa_fallback:
                self.path = '/index.html'
                return super().send_head()
            return self._not_found()
        return super().send_head()


class _BytesFile:
    def __init__(self, data):
        self.data = data

    def read(self, *args):
        data, self.data = self.data, b''
        return data

    def close(self):
        pass


def stop_process_group(process):
    """npm/pnpm spawn the real server as a child: signal the whole session, then reap."""
    import signal
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, AttributeError):
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, AttributeError):
            process.kill()
        process.wait(timeout=5)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Served:
    """Handle for whatever is being measured; `close()` stops any server we started."""

    def __init__(self, mode, base_url=None, *, output_dir=None, log=None, limitations=None, server=None, process=None):
        self.mode, self.base_url, self.output_dir = mode, base_url, output_dir
        self.log, self.limitations = log or [], limitations or []
        self._server, self._process = server, process

    def describe(self):
        return {'mode': self.mode, 'base_url': self.base_url, 'output_dir': str(self.output_dir) if self.output_dir else None,
                'log': self.log[-20:], 'limitations': self.limitations}

    def close(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._process is not None:
            stop_process_group(self._process)
            self._process = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def serve_directory(directory, spa=False):
    port = free_port()
    handler_class = type('Handler', (CleanURLHandler,), {'spa_fallback': spa})
    handler = functools.partial(handler_class, directory=str(directory))
    server = http.server.ThreadingHTTPServer(('127.0.0.1', port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f'http://127.0.0.1:{port}'


UNBUILT_SHELL = re.compile(r'%PUBLIC_URL%|<script[^>]+src=["\']/?src/[^"\']+\.(?:[jt]sx?|vue|svelte)["\']|<%=?\s*BASE_URL', re.I)


def unbuilt(directory):
    index = Path(directory) / 'index.html'
    try:
        return index.is_file() and bool(UNBUILT_SHELL.search(index.read_text(errors='ignore')[:20000]))
    except OSError:
        return False


def html_root(directory):
    """Directory that holds the site's built index.html (itself or one level down); never a source shell."""
    directory = Path(directory)
    if unbuilt(directory):
        return None
    if (directory / 'index.html').is_file():
        return directory
    for child in sorted(directory.iterdir()) if directory.is_dir() else []:
        if child.is_dir() and (child / 'index.html').is_file() and not unbuilt(child):
            return child
    return directory if any(directory.glob('*.html')) else None


def wait_for(url, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=3):
                return True
        except HTTPError:
            return True  # the server answered (e.g. 404 at / under a basePath): it is up
        except Exception:  # noqa: BLE001 - refused/reset just means "not ready yet"
            time.sleep(0.5)
    return False


def run(command, cwd, timeout, log, env=None):
    executable = shutil.which(command[0])
    if not executable:
        log.append('missing tool: ' + command[0])
        return False
    began = time.time()
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                                env={**os.environ, 'CI': '1', 'NEXT_TELEMETRY_DISABLED': '1', 'ASTRO_TELEMETRY_DISABLED': '1',
                                     'NUXT_TELEMETRY_DISABLED': '1', 'GATSBY_TELEMETRY_DISABLED': '1', **(env or {})})
    except subprocess.TimeoutExpired:
        log.append(' '.join(command) + ': timeout after ' + str(timeout) + 's')
        return False
    log.append(' '.join(command) + ': exit ' + str(result.returncode) + ' in ' + str(round(time.time() - began)) + 's')
    if result.returncode:
        log.append((result.stderr or result.stdout)[-1500:])
    return result.returncode == 0


def spa_rewrite_configured(root):
    """vercel.json / netlify _redirects / firebase.json rewrite everything to index.html."""
    for name in ('vercel.json', 'public/_redirects', '_redirects', 'netlify.toml', 'firebase.json', 'static.json'):
        path = Path(root) / name
        if path.is_file():
            text = path.read_text(errors='ignore')
            if re.search(r'(?:"destination"\s*:\s*"/index\.html"|/\*\s+/index\.html\s+200|to\s*=\s*"/index\.html")', text):
                return True
    return False


def build_and_serve(repo_path, fingerprint, *, site_url=None, build=None, timeout=900):
    root = Path(repo_path).resolve()
    build = os.getenv('SEO_BUILD', '0') == '1' if build is None else build
    log, limitations = [], []
    if site_url:
        return Served('site_url', site_url.rstrip('/'), log=['measuring user-supplied site_url'])
    if fingerprint.get('needs_runtime') and not fingerprint.get('build', {}).get('command'):
        limitations.append(f"{fingerprint['primary']} renders on a {fingerprint['needs_runtime']} runtime; pass site_url to measure served pages.")
        return Served('source-only', log=log, limitations=limitations)
    package_dir = root / Path(fingerprint.get('package_json') or 'package.json').parent
    spa = fingerprint.get('renderer') == 'csr' or spa_rewrite_configured(root)
    declared = [package_dir / d for d in fingerprint.get('build', {}).get('output_dirs', [])]
    # public/ and static/ hold *source* assets for most bundlers; only trust them when the framework builds into them.
    generic = [package_dir / d for d in STATIC_DIRS if d not in {'public', 'static', 'site', 'www', 'docs'} or
               (fingerprint.get('renderer') == 'static' and not fingerprint.get('package_json'))]
    candidates = declared + [d for d in generic if d not in declared]
    if fingerprint.get('primary') in {'static-html', 'unknown'} or fingerprint.get('family') == 'html':
        candidates.insert(0, root)
    if not build:
        # Only pre-existing static HTML; nothing from the repository is executed.
        for directory in candidates:
            served_root = html_root(directory) if directory.is_dir() else None
            if served_root and not (fingerprint.get('package_json') and directory == root and fingerprint.get('renderer') != 'static'):
                server, base = serve_directory(served_root, spa=spa)
                log.append('serving existing static HTML from ' + str(served_root.relative_to(root) if served_root != root else '.')
                           + (' (SPA fallback to index.html)' if spa else ''))
                return Served('static-dir', base, output_dir=served_root, log=log, server=server)
        limitations.append('No pre-built static HTML found and building is disabled (SEO_BUILD=1 / build=True); source-only mode.')
        return Served('source-only', log=log, limitations=limitations)
    manager = fingerprint.get('package_manager')
    if fingerprint.get('package_json') and manager in INSTALL:
        if not run(INSTALL[manager](package_dir), package_dir, timeout, log):
            limitations.append('Dependency install failed; source-only mode.')
            return Served('source-only', log=log, limitations=limitations)
    command = (fingerprint.get('build') or {}).get('command')
    if command and not run(command, package_dir, timeout, log, env={'NODE_ENV': 'production'}):
        limitations.append('Build failed; source-only mode. See build log.')
        return Served('source-only', log=log, limitations=limitations)
    for directory in candidates:
        served_root = html_root(directory) if directory.is_dir() else None
        if served_root and served_root != root:
            server, base = serve_directory(served_root, spa=spa)
            log.append('serving build output ' + str(served_root.relative_to(root)) + (' (SPA fallback)' if spa else ''))
            return Served('static-dir', base, output_dir=served_root, log=log, server=server)
    preview = (fingerprint.get('build') or {}).get('preview_script')
    if preview and manager:
        port = free_port()
        process = subprocess.Popen([manager, 'run', preview, '--', '--port', str(port), '--host', '127.0.0.1'],
                                   cwd=package_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   env={**os.environ, 'PORT': str(port), 'HOST': '127.0.0.1', 'NODE_ENV': 'production'},
                                   start_new_session=True)
        base = f'http://127.0.0.1:{port}'
        if wait_for(base, 90):
            log.append(f'preview server `{manager} run {preview}` on {base}')
            return Served('preview', base, log=log, process=process)
        stop_process_group(process)
        limitations.append('Preview server did not start within 90s; source-only mode.')
    else:
        limitations.append('Build produced no static HTML and no preview script exists; source-only mode.')
    return Served('source-only', log=log, limitations=limitations)
