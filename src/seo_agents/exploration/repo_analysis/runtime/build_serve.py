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

Building runs repository code. It is meant for the sandbox container only and is opt-in.
Safety: install/build/preview get an allowlisted environment (`safe_env`: no tokens or API keys),
run in their own process group (killed as a group on timeout) and, when the driver runs as root,
as the unprivileged SEO_BUILD_USER. Readiness means a 2xx/3xx answer, not "any HTTP answer".
Package managers follow `packageManager`/lockfiles (yarn berry `--immutable`, monorepo installs at
the workspace root); preview servers get per-tool port flags (`next start -p`, `vite preview --port`).
Committed build output is served only when no build runs (SEO_MEASURE_PREBUILT=0 forbids it,
=1 prefers it) and is flagged as possibly stale. SEO_INSTRUMENT=1 stamps data-seo-src source
locations on Vite apps through a temporary wrapper config (best effort; client files untouched).
"""

from __future__ import annotations

import functools
import http.server
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import urlopen

STATIC_DIRS = ('out', 'dist', 'build', '_site', 'public', '.output/public', 'docs', 'site', 'www', '.vitepress/dist',
               'build/client', '.svelte-kit/output/prerendered/pages', 'storybook-static')


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


# ---------------------------------------------------------------- credential-free subprocess env
# Building/serving runs untrusted repository code (postinstall scripts, build plugins). Only these
# variables reach it; GITHUB_TOKEN, OPENROUTER_* and every other secret stay in the parent.
SAFE_ENV_KEYS = {'PATH', 'HOME', 'USER', 'LOGNAME', 'SHELL', 'LANG', 'LANGUAGE', 'TERM', 'TZ', 'TMPDIR', 'TEMP', 'TMP',
                 'NODE_ENV', 'NODE_OPTIONS', 'NVM_DIR', 'VOLTA_HOME', 'COREPACK_HOME', 'PNPM_HOME', 'BUN_INSTALL',
                 'GEM_HOME', 'GEM_PATH', 'BUNDLE_PATH', 'GOPATH', 'GOCACHE', 'GOROOT', 'PYTHONPATH',
                 'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'no_proxy',
                 'SSL_CERT_FILE', 'SSL_CERT_DIR', 'NODE_EXTRA_CA_CERTS', 'PLAYWRIGHT_BROWSERS_PATH'}
SAFE_ENV_PREFIXES = ('LC_', 'XDG_')
SAFE_NPM_CONFIG = {'npm_config_cache', 'npm_config_registry', 'npm_config_prefix', 'npm_config_userconfig',
                   'npm_config_update_notifier', 'npm_config_fund', 'npm_config_audit', 'npm_config_loglevel'}
SECRET_NAME = re.compile(r'(?i)token|secret|passw|api[_-]?key|auth|credential|private|session|cookie')
BUILD_DEFAULTS = {'CI': '1', 'NEXT_TELEMETRY_DISABLED': '1', 'ASTRO_TELEMETRY_DISABLED': '1', 'NUXT_TELEMETRY_DISABLED': '1',
                  'GATSBY_TELEMETRY_DISABLED': '1', 'STORYBOOK_DISABLE_TELEMETRY': '1', 'DO_NOT_TRACK': '1',
                  'HUSKY': '0', 'ADBLOCK': '1', 'DISABLE_OPENCOLLECTIVE': '1', 'npm_config_update_notifier': 'false'}


def safe_env(extra=None, base=None):
    """Allowlisted environment for repository code: never tokens or API keys."""
    base = os.environ if base is None else base
    env = {}
    for key, value in base.items():
        allowed = (key in SAFE_ENV_KEYS or key.startswith(SAFE_ENV_PREFIXES) or key.endswith('_TELEMETRY_DISABLED')
                   or key in SAFE_NPM_CONFIG)
        if allowed and not SECRET_NAME.search(key):
            env[key] = value
    env.update(BUILD_DEFAULTS)
    for key, value in (extra or {}).items():
        if value is not None and not SECRET_NAME.search(key):
            env[key] = str(value)
    return env


def build_identity():
    """When running as root (sandbox container) and SEO_BUILD_USER exists, repository code runs as that
    unprivileged user: it then cannot read the driver's environment (/proc/<pid>/environ) or root files."""
    name = os.getenv('SEO_BUILD_USER')
    if not name or not hasattr(os, 'geteuid') or os.geteuid() != 0:
        return None
    try:
        import pwd
        entry = pwd.getpwnam(name)
    except (ImportError, KeyError):
        return None
    return {'user': entry.pw_uid, 'group': entry.pw_gid, 'home': entry.pw_dir}


def hand_over(directory, identity):
    """Give the build user ownership of the checkout it must write into (node_modules, dist)."""
    if not identity:
        return
    for path in [Path(directory), *Path(directory).rglob('*')]:
        try:
            os.lchown(path, identity['user'], identity['group'])
        except OSError:
            pass


def popen_identity(identity):
    if not identity:
        return {}
    return {'user': identity['user'], 'group': identity['group'], 'extra_groups': []}


def wait_for(url, timeout=60, paths=('/',), process=None):
    """Ready = a 2xx/3xx answer on a known route. 4xx/5xx (e.g. an SSR crash page) is not "up"."""
    deadline = time.time() + timeout
    targets = [url.rstrip('/') + '/' + p.lstrip('/') for p in (paths or ('/',))]
    while time.time() < deadline:
        if process is not None and process.poll() is not None:
            return False  # the server exited: no point waiting
        for target in targets:
            try:
                with urlopen(target, timeout=3) as response:
                    if 200 <= response.status < 400:
                        return True
            except HTTPError as error:
                error.close()
                if 300 <= error.code < 400:
                    return True
            except Exception:  # noqa: BLE001 - refused/reset just means "not ready yet"
                pass
        time.sleep(0.5)
    return False


def run(command, cwd, timeout, log, env=None):
    """Run one install/build step in its own process group with a credential-free env.
    On timeout the whole group (npm -> node -> esbuild/workers) is killed, not only the parent."""
    executable = shutil.which(command[0])
    if not executable:
        log.append('missing tool: ' + command[0])
        return False
    began = time.time()
    identity = build_identity()
    extra = dict(env or {})
    if identity:
        extra['HOME'] = identity['home']
    try:
        process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   env=safe_env(extra), start_new_session=True, **popen_identity(identity))
    except OSError as error:
        log.append(' '.join(command) + ': could not start: ' + type(error).__name__)
        return False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        stop_process_group(process)
        try:
            process.communicate(timeout=5)  # drain and close the pipes
        except (subprocess.TimeoutExpired, ValueError, OSError):
            pass
        log.append(' '.join(command) + ': timeout after ' + str(timeout) + 's (process group killed)')
        return False
    log.append(' '.join(command) + ': exit ' + str(process.returncode) + ' in ' + str(round(time.time() - began)) + 's')
    if process.returncode:
        log.append((stderr or stdout or '')[-1500:])
    return process.returncode == 0


# ---------------------------------------------------------------- package managers
def read_json(path):
    try:
        value = json.loads(Path(path).read_text(errors='ignore'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def workspace_root(package_dir, root):
    """Monorepos install at the workspace root (lockfile / workspaces / pnpm-workspace.yaml), not the package."""
    package_dir, root = Path(package_dir).resolve(), Path(root).resolve()
    current = package_dir
    best = package_dir
    while True:
        if (current / 'pnpm-workspace.yaml').is_file() or read_json(current / 'package.json').get('workspaces'):
            best = current
        elif current != package_dir and any((current / lock).is_file() for lock in LOCKFILES):
            best = current
        if current == root or current.parent == current:
            break
        current = current.parent
    return best


LOCKFILES = ('package-lock.json', 'npm-shrinkwrap.json', 'pnpm-lock.yaml', 'yarn.lock', 'bun.lockb', 'bun.lock')


def package_manager_spec(install_dir, detected):
    """(manager, version) honouring package.json `packageManager` (corepack) over lockfile guessing."""
    declared = read_json(Path(install_dir) / 'package.json').get('packageManager')
    if isinstance(declared, str):
        match = re.match(r'(npm|pnpm|yarn|bun)@(\d+)', declared)
        if match:
            return match.group(1), int(match.group(2))
    return detected, None


def yarn_berry(install_dir, version=None):
    return (version or 0) >= 2 or (Path(install_dir) / '.yarnrc.yml').is_file()


def install_command(manager, install_dir, version=None):
    install_dir = Path(install_dir)
    if manager == 'npm':
        if (install_dir / 'package-lock.json').exists() or (install_dir / 'npm-shrinkwrap.json').exists():
            return ['npm', 'ci', '--no-audit', '--no-fund']
        return ['npm', 'install', '--no-audit', '--no-fund']
    if manager == 'pnpm':
        return ['pnpm', 'install', '--frozen-lockfile'] if (install_dir / 'pnpm-lock.yaml').exists() else ['pnpm', 'install']
    if manager == 'yarn':
        if yarn_berry(install_dir, version):
            return ['yarn', 'install', '--immutable']
        return ['yarn', 'install', '--frozen-lockfile'] if (install_dir / 'yarn.lock').exists() else ['yarn', 'install']
    if manager == 'bun':
        return ['bun', 'install', '--frozen-lockfile'] if any((install_dir / l).exists() for l in ('bun.lockb', 'bun.lock')) else ['bun', 'install']
    return None


INSTALL = {name: (lambda root, _n=name: install_command(_n, root)) for name in ('npm', 'pnpm', 'yarn', 'bun')}


def node_requirements(package_dir, install_dir):
    """.nvmrc / .node-version / engines.node: recorded so a mismatch explains a failed build."""
    found = {}
    for directory in dict.fromkeys([Path(package_dir), Path(install_dir)]):
        for name in ('.nvmrc', '.node-version'):
            path = directory / name
            if path.is_file() and 'nvmrc' not in found:
                found['nvmrc'] = path.read_text(errors='ignore').strip()[:40]
        engines = read_json(directory / 'package.json').get('engines')
        if isinstance(engines, dict) and engines.get('node') and 'engines' not in found:
            found['engines'] = str(engines['node'])[:40]
    return found


# ---------------------------------------------------------------- preview recipes
def preview_command(manager, script_name, script_body, port):
    """Command + env to start a preview server on 127.0.0.1:port. Flags differ per tool
    (`next start -p/-H`, `vite preview --port --strictPort`, Remix/Nuxt/SvelteKit-node read PORT)."""
    body = script_body or ''
    host = '127.0.0.1'
    env = {'PORT': str(port), 'HOST': host, 'HOSTNAME': host, 'NITRO_PORT': str(port), 'NITRO_HOST': host,
           'NODE_ENV': 'production'}
    if re.search(r'\bnext\s+start\b', body):
        args = ['-p', str(port), '-H', host]
    elif re.search(r'\b(?:vite|vitepress)\b[^&|;]*\bpreview\b', body):
        args = ['--port', str(port), '--strictPort', '--host', host]
    elif re.search(r'\bastro\s+preview\b', body):
        args = ['--port', str(port), '--host', host]
    elif re.search(r'\bng\s+serve\b', body):
        args = ['--port', str(port), '--host', host]
    elif re.search(r'\b(?:serve|sirv)\b', body) and not re.search(r'\b(?:remix|nuxt|node)\b', body):
        args = ['-l', str(port)] if re.search(r'(?:^|\s)serve\b', body) else ['--port', str(port), '--host', host]
    else:
        args = []  # remix-serve, nuxt/nitro, node build, gatsby serve via env only
        if re.search(r'\bgatsby\s+serve\b', body):
            args = ['-p', str(port), '-H', host]
    command = [manager, 'run', script_name]
    if args:
        # npm needs `--` to forward flags; pnpm/yarn/bun forward them directly (pnpm would pass a literal `--`).
        command += (['--'] if manager == 'npm' else []) + args
    return command, env


def log_tail(path, limit=1200):
    try:
        return Path(path).read_text(errors='ignore')[-limit:].strip()
    except OSError:
        return ''


# ---------------------------------------------------------------- optional source-location instrumentation
INSTRUMENT_TAGS = ('a', 'img', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li', 'title', 'meta', 'link', 'button',
                   'figcaption', 'blockquote', 'td', 'th', 'dt', 'dd', 'nav', 'main', 'header', 'footer', 'video', 'source')
VITE_WRAPPER = r"""// Generated by seo-agents (SEO_INSTRUMENT=1); removed after the build. Best effort:
// stamps data-seo-src="file:line:col" on intrinsic SEO-relevant JSX/Vue/Svelte elements.
import { defineConfig, loadConfigFromFile, mergeConfig } from 'vite';
import path from 'node:path';
const ROOT = __ROOT__;
const USER_CONFIG = __USER_CONFIG__;
const TAG = new RegExp('(^|[\\s(>{}=?:,])<(' + __TAGS__.join('|') + ')(?=[\\s>/])', 'g');
function seoDataSrc() {
  return {
    name: 'seo-agents-data-src', enforce: 'pre',
    transform(code, id) {
      const file = id.split('?')[0];
      if (file.includes('/node_modules/') || !/\.(jsx|tsx|vue|svelte)$/.test(file)) return null;
      const rel = path.relative(ROOT, file).split(path.sep).join('/');
      if (rel.startsWith('..')) return null;
      const starts = [0];
      for (let i = 0; i < code.length; i++) if (code[i] === '\n') starts.push(i + 1);
      const lineOf = (offset) => { let lo = 0, hi = starts.length - 1;
        while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (starts[mid] <= offset) lo = mid; else hi = mid - 1; } return lo; };
      const out = code.replace(TAG, (match, lead, tag, offset) => {
        const at = offset + lead.length; const line = lineOf(at);
        return lead + '<' + tag + ' data-seo-src="' + rel + ':' + (line + 1) + ':' + (at - starts[line] + 1) + '"';
      });
      return out === code ? null : { code: out, map: null };
    },
  };
}
export default defineConfig(async (env) => {
  const loaded = USER_CONFIG ? await loadConfigFromFile(env, USER_CONFIG, ROOT) : null;
  return mergeConfig(loaded ? loaded.config : {}, { plugins: [seoDataSrc()] });
});
"""
WRAPPER_NAME = 'vite.seo-agents-instrument.config.mjs'


def instrument_enabled():
    return os.getenv('SEO_INSTRUMENT', '0').lower() in {'1', 'true', 'yes'}


def instrumented_build(package_dir, fingerprint, log, limitations):
    """Vite-based apps only: a temporary wrapper config adds a pre-transform plugin; the client's own
    files are never edited. Returns (command, cleanup) or (None, noop) with a limitation note."""
    package_dir = Path(package_dir)
    deps = read_json(package_dir / 'package.json')
    names = {**(deps.get('dependencies') or {}), **(deps.get('devDependencies') or {})}
    meta = {'next', 'nuxt', 'astro', '@remix-run/dev', '@sveltejs/kit', 'gatsby', '@angular/core'} & set(names)
    if 'vite' not in names or meta:
        limitations.append('SEO_INSTRUMENT=1: source-location instrumentation only supports plain Vite apps '
                           '(React/Preact/Vue/Svelte); string-match attribution is used instead.')
        return None, lambda: None
    user = next((name for name in ('vite.config.ts', 'vite.config.mts', 'vite.config.js', 'vite.config.mjs', 'vite.config.cjs')
                 if (package_dir / name).is_file()), None)
    wrapper = package_dir / WRAPPER_NAME
    wrapper.write_text(VITE_WRAPPER.replace('__ROOT__', json.dumps(str(package_dir)))
                       .replace('__USER_CONFIG__', json.dumps(str(package_dir / user)) if user else 'null')
                       .replace('__TAGS__', json.dumps(list(INSTRUMENT_TAGS))))
    log.append('instrumentation: data-seo-src via temporary ' + WRAPPER_NAME + ' (best effort)')

    def cleanup():
        try:
            wrapper.unlink()
        except OSError:
            pass
    return ['npx', '--no-install', 'vite', 'build', '--config', WRAPPER_NAME], cleanup


def spa_rewrite_configured(root):
    """vercel.json / netlify _redirects / firebase.json rewrite everything to index.html."""
    for name in ('vercel.json', 'public/_redirects', '_redirects', 'netlify.toml', 'firebase.json', 'static.json'):
        path = Path(root) / name
        if path.is_file():
            text = path.read_text(errors='ignore')
            if re.search(r'(?:"destination"\s*:\s*"/index\.html"|/\*\s+/index\.html\s+200|to\s*=\s*"/index\.html")', text):
                return True
    return False


def prebuilt_allowed():
    """Committed build output (dist/, out/) may be stale; serve it only when no build runs, unless
    SEO_MEASURE_PREBUILT=0 forbids it or SEO_MEASURE_PREBUILT=1 prefers it over building."""
    return os.getenv('SEO_MEASURE_PREBUILT', 'auto').lower()


def serve_existing(candidates, root, fingerprint, spa, log, limitations):
    for directory in candidates:
        served_root = html_root(directory) if directory.is_dir() else None
        if served_root and not (fingerprint.get('package_json') and directory == root and fingerprint.get('renderer') != 'static'):
            server, base = serve_directory(served_root, spa=spa)
            where = str(served_root.relative_to(root)) if served_root != root else '.'
            log.append('serving existing static HTML from ' + where + (' (SPA fallback to index.html)' if spa else ''))
            if fingerprint.get('package_json') and served_root != root:
                limitations.append(f'Measured committed build output `{where}/` without rebuilding; it may be stale '
                                   'relative to the source (pass --build for a fresh build).')
            return Served('static-dir', base, output_dir=served_root, log=log, limitations=limitations, server=server)
    return None


def known_routes(fingerprint):
    paths = ['/']
    base_path = fingerprint.get('base_path')
    if isinstance(base_path, str) and base_path.startswith('/'):
        paths.insert(0, base_path)
    return paths


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
    prebuilt = prebuilt_allowed()
    if not build or prebuilt == '1':
        if prebuilt != '0':
            served = serve_existing(candidates, root, fingerprint, spa, log, limitations)
            if served:
                return served
        if not build:
            limitations.append('No pre-built static HTML found and building is disabled (SEO_BUILD=1 / build=True); source-only mode.'
                               if prebuilt != '0' else 'SEO_MEASURE_PREBUILT=0 and building is disabled; source-only mode.')
            return Served('source-only', log=log, limitations=limitations)
    detected = fingerprint.get('package_manager')
    install_dir = workspace_root(package_dir, root) if fingerprint.get('package_json') else package_dir
    manager, manager_version = package_manager_spec(install_dir, detected)
    requirements = node_requirements(package_dir, install_dir)
    if requirements:
        log.append('node version requirement: ' + json.dumps(requirements))
    identity = build_identity()
    if identity:
        hand_over(install_dir, identity)
        log.append('install/build/preview run as unprivileged user ' + str(os.getenv('SEO_BUILD_USER')))
    if fingerprint.get('package_json') and manager:
        if read_json(install_dir / 'package.json').get('packageManager') and shutil.which('corepack'):
            run(['corepack', 'enable'], install_dir, 120, log)
        command = install_command(manager, install_dir, manager_version)
        if command and not run(command, install_dir, timeout, log):
            limitations.append('Dependency install failed' + (' (declared node ' + json.dumps(requirements) + ')' if requirements else '')
                               + '; source-only mode.')
            return Served('source-only', log=log, limitations=limitations)
    command = list((fingerprint.get('build') or {}).get('command') or []) or None
    if command and detected and manager and command[0] == detected:
        command[0] = manager
    cleanup = lambda: None  # noqa: E731
    if instrument_enabled() and fingerprint.get('package_json'):
        instrumented, cleanup = instrumented_build(package_dir, fingerprint, log, limitations)
        command = instrumented or command
    try:
        built = not command or run(command, package_dir, timeout, log, env={'NODE_ENV': 'production'})
    finally:
        cleanup()
    if not built:
        limitations.append('Build failed; source-only mode. See build log.')
        return Served('source-only', log=log, limitations=limitations)
    for directory in candidates:
        served_root = html_root(directory) if directory.is_dir() else None
        if served_root and served_root != root:
            server, base = serve_directory(served_root, spa=spa)
            log.append('serving build output ' + str(served_root.relative_to(root)) + (' (SPA fallback)' if spa else ''))
            return Served('static-dir', base, output_dir=served_root, log=log, limitations=limitations, server=server)
    preview = (fingerprint.get('build') or {}).get('preview_script')
    if preview and manager:
        port = free_port()
        scripts = read_json(package_dir / 'package.json').get('scripts') or {}
        argv, extra = preview_command(manager, preview, scripts.get(preview), port)
        log_file = tempfile.NamedTemporaryFile(prefix='seo-preview-', suffix='.log', delete=False)
        if identity:
            extra['HOME'] = identity['home']
        try:
            process = subprocess.Popen(argv, cwd=package_dir, stdout=log_file, stderr=subprocess.STDOUT,
                                       env=safe_env(extra), start_new_session=True, **popen_identity(identity))
        except OSError as error:
            log_file.close()
            limitations.append('Preview server could not start (' + type(error).__name__ + '); source-only mode.')
            return Served('source-only', log=log, limitations=limitations)
        log_file.close()
        base = f'http://127.0.0.1:{port}'
        if wait_for(base, 90, paths=known_routes(fingerprint), process=process):
            log.append(f'preview server `{" ".join(argv)}` on {base}')
            return Served('preview', base, log=log, limitations=limitations, process=process)
        stop_process_group(process)
        tail = log_tail(log_file.name)
        limitations.append('Preview server did not answer 2xx/3xx within 90s; source-only mode.'
                           + (' Last output: ' + tail[-600:] if tail else ''))
        try:
            os.unlink(log_file.name)
        except OSError:
            pass
    else:
        limitations.append('Build produced no static HTML and no preview script exists; source-only mode.')
    return Served('source-only', log=log, limitations=limitations)
