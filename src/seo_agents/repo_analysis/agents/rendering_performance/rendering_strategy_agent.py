"""Rendering mode per route: config -> root layout -> page cascade (static/SSG/ISR/SSR/CSR/hybrid).

Framework config sets the default (Next `output: 'export'`, Nuxt `ssr: false`, SvelteKit adapter-static or a
root `export const prerender = true`, Astro `output`, Gatsby, a Vite/CRA SPA shell); layouts override config;
page files override layouts. `route_modes` is one row per page route: `{route, file, mode, modes, source_chain}`
(`modes` = [resolved mode], kept as a list for consumers of the earlier shape).
Client-rendering hazards from the client-diagnostics agent (hash routing, JS-only navigation, runtime head,
effect fetches) are folded in per file.
"""

from collections import Counter, defaultdict
from pathlib import PurePosixPath
import re

from seo_agents.repo_analysis.tools.signal_profiles.signal_profile_runner import run_signal_profile
from seo_agents.repo_analysis.agents.base_agent import RepoAnalysisAgent

PRIORITY = ('hybrid', 'SSR', 'ISR', 'SSG', 'server-default', 'hydration', 'CSR', 'static')
EXPLICIT = ('SSR', 'ISR', 'SSG', 'CSR', 'hybrid')
LAYOUT_NAMES = re.compile(r'(?:^|/)(?:layout\.[jt]sx?|\+layout(?:\.server)?\.[jt]s|\+layout\.svelte|_app\.[jt]sx?|app\.vue|'
                          r'layouts/default\.vue|_layout\.[jt]sx?|root\.[jt]sx)$')
HAZARD_SIGNALS = ('hash-router', 'js-navigation', 'js-only-anchor', 'runtime-head', 'effect-fetch')


def _config_mode(index):
    """(mode, file, reason) from framework configuration, or None."""
    for path in index.paths('next.config.*'):
        text = index.files[path]
        if re.search(r'''output\s*:\s*['"]export['"]''', text):
            return 'SSG', path, "next.config output: 'export'"
        return 'server-default', path, 'Next.js default (server components, static when possible)'
    for path in index.paths('nuxt.config.*'):
        text = index.files[path]
        if re.search(r'\bssr\s*:\s*false\b', text):
            return 'CSR', path, 'nuxt.config ssr: false'
        if re.search(r'\bnitro\s*:\s*\{[^}]*\bprerender\b|\bprerender\s*:\s*true', text, re.S):
            return 'SSG', path, 'nuxt.config prerender'
        return 'SSR', path, 'Nuxt default (universal rendering)'
    for path in index.paths('svelte.config.*'):
        text = index.files[path]
        if 'adapter-static' in text:
            return 'SSG', path, 'SvelteKit adapter-static'
        return 'SSR', path, 'SvelteKit default (SSR)'
    for path in index.paths('astro.config.*'):
        text = index.files[path]
        match = re.search(r'''output\s*:\s*['"](server|static|hybrid)['"]''', text)
        mode = {'server': 'SSR', 'static': 'SSG', 'hybrid': 'hybrid'}.get(match.group(1)) if match else 'SSG'
        return mode, path, 'astro.config output: ' + (match.group(1) if match else 'static (default)')
    for path in index.paths('gatsby-config.*'):
        return 'SSG', path, 'Gatsby (build-time rendering)'
    for path in index.paths('angular.json'):
        text = index.files[path]
        if '"prerender"' in text or '"ssr"' in text:
            return 'SSR', path, 'Angular SSR/prerender configured'
        return 'CSR', path, 'Angular application without SSR'
    for path in index.paths('index.html', 'public/index.html', 'src/index.html'):
        text = index.files[path]
        shell = re.search(r'''<div\s+id=['"](?:root|app|q-app)['"][^>]*>\s*</div>|<app-root\b''', text)
        scripted = re.search(r'<script\b[^>]*(?:type=["\']module["\']|src=)', text) or index.paths('vite.config.*')
        if shell and scripted:
            prerender = any(re.search(r'vite-ssg|ViteSSG|react-snap|vite-plugin-prerender|vike|@prerenderer', index.files[p])
                            for p in index.paths('package.json', 'vite.config.*'))
            if prerender:
                return 'SSG', path, 'SPA shell with a prerender plugin'
            return 'CSR', path, 'SPA shell (empty mount node) without SSR or prerendering'
    return None


def _file_mode(modes):
    explicit = [m for m in PRIORITY if m in modes and m in EXPLICIT]
    if explicit:
        return explicit[0]
    return next((m for m in PRIORITY if m in modes), None)


def _layout_chain(file, layouts):
    parts = PurePosixPath(file).parts
    chain = []
    for layout in layouts:
        directory = PurePosixPath(layout).parent.parts
        if parts[:len(directory)] == directory and layout != file:
            chain.append(layout)
    return sorted(chain, key=lambda p: len(PurePosixPath(p).parts))


def analyze_rendering_strategy(index, routes=None, client=None):
    result = run_signal_profile(index, 'rendering-strategy')
    per_file, modes = defaultdict(set), Counter()
    for signal in result['signals'].values():
        for record in signal['records']:
            if 'mode' not in record:
                continue
            per_file[record['location']['file']].add(record['mode'])
            modes[record['mode']] += 1
    config = _config_mode(index)
    if config:
        modes[config[0]] += 1
    project_modes = [m for m in PRIORITY if modes[m]]
    if not project_modes and any(p.endswith(('.html', '.htm')) for p in index.files):
        project_modes = ['static']
    csr_only = (bool(modes['CSR']) or (config and config[0] == 'CSR')) and \
        not any(modes[m] for m in ('SSR', 'SSG', 'ISR', 'hybrid', 'hydration'))
    result['mode_counts'] = dict(modes)
    result['project_modes'] = project_modes
    result['config_mode'] = {'mode': config[0], 'file': config[1], 'reason': config[2]} if config else None
    result['csr_only'] = bool(csr_only)
    # Source signals cannot prove what a crawler receives; only a client-only shell is a safe static verdict.
    result['initial_html_contains_content'] = False if csr_only else None
    result['per_route_verdict'] = 'requires-measurement'
    result['verdict_note'] = ('Compare raw HTML (no JS) with the rendered DOM per URL to confirm initial content; '
                              'project-level signals are hints, not per-route facts.')
    result['per_file_modes'] = {f: sorted(m) for f, m in sorted(per_file.items())}
    layouts = sorted(f for f in index.paths() if LAYOUT_NAMES.search(f))
    route_modes = []
    for route in routes or []:
        if route.get('kind') not in {'page', 'content-page'}:
            continue
        file = route['source']['location']['file']
        chain = []
        if config:
            chain.append({'level': 'config', 'file': config[1], 'mode': config[0], 'reason': config[2]})
        for layout in _layout_chain(file, layouts):
            mode = _file_mode(per_file.get(layout, ()))
            if mode:
                chain.append({'level': 'layout', 'file': layout, 'mode': mode})
        mode = _file_mode(per_file.get(file, ()))
        if mode:
            chain.append({'level': 'page', 'file': file, 'mode': mode})
        # Most specific explicit mode wins; weak defaults ('server-default', 'hydration') never override explicit ones.
        resolved = next((c['mode'] for c in reversed(chain) if c['mode'] in EXPLICIT), None) or \
            next((c['mode'] for c in reversed(chain)), None)
        route_modes.append({'route': route['route'], 'file': file, 'mode': resolved, 'modes': [resolved] if resolved else [],
                            'source_chain': chain})
    result['route_modes'] = route_modes
    if client is not None:
        hazards = defaultdict(list)
        for sid in HAZARD_SIGNALS:
            for record in client['signals'].get(sid, {}).get('records', []):
                hazards[record['location']['file']].append({'signal': sid, 'line': record['location']['line_start']})
        result['client_hazards_by_file'] = {f: v for f, v in sorted(hazards.items())}
        result['client_hazard_counts'] = {sid: client['signals'][sid]['total_matches'] for sid in HAZARD_SIGNALS
                                          if client['signals'].get(sid, {}).get('total_matches')}
    result['interpretation'] = ('Client-side rendering only: crawlers that do not execute JavaScript see an empty shell.'
                                if csr_only else 'Server/build-time rendering signals present.' if project_modes
                                else 'No rendering signal matched; inspect entry points.')
    return result


class RenderingStrategyAgent(RepoAnalysisAgent):
    """Agent: Rendering mode per route: config -> root layout -> page cascade (static/SSG/ISR/SSR/CSR/hybrid)."""

    name = 'rendering_strategy'
    description = 'Rendering mode per route: config -> root layout -> page cascade (static/SSG/ISR/SSR/CSR/hybrid).'
    signal_profile = 'rendering-strategy'
    requires = ('routes', 'client_diagnostics')

    def run(self, index, results):
        return analyze_rendering_strategy(index, results['routes'], results['client_diagnostics'])
