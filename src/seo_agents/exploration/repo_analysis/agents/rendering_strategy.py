"""Rendering mode per file and for the project (static/SSG/ISR/SSR/CSR/hybrid)."""

from collections import Counter, defaultdict

from seo_agents.exploration.repo_analysis.tools.skill_runner import run_skill

PRIORITY = ('hybrid', 'SSR', 'ISR', 'SSG', 'server-default', 'hydration', 'CSR', 'static')


def analyze_rendering_strategy(index, routes=None):
    result = run_skill(index, 'rendering-strategy')
    per_file, modes = defaultdict(set), Counter()
    for signal in result['signals'].values():
        for record in signal['records']:
            if 'mode' not in record:
                continue
            per_file[record['location']['file']].add(record['mode'])
            modes[record['mode']] += 1
    project_modes = [m for m in PRIORITY if modes[m]]
    if not project_modes and any(p.endswith(('.html', '.htm')) for p in index.files):
        project_modes = ['static']
    csr_only = bool(modes['CSR']) and not any(modes[m] for m in ('SSR', 'SSG', 'ISR', 'hybrid', 'hydration', 'server-default'))
    result['mode_counts'] = dict(modes)
    result['project_modes'] = project_modes
    # Source signals cannot prove what a crawler receives; only a client-only shell is a safe static verdict.
    result['initial_html_contains_content'] = False if csr_only else None
    result['per_route_verdict'] = 'requires-measurement'
    result['verdict_note'] = ('Compare raw HTML (no JS) with the rendered DOM per URL to confirm initial content; '
                              'project-level signals are hints, not per-route facts.')
    result['per_file_modes'] = {f: sorted(m) for f, m in sorted(per_file.items())}
    if routes is not None:
        result['route_modes'] = [{'route': r['route'], 'file': r['source']['location']['file'],
                                  'modes': sorted(per_file.get(r['source']['location']['file'], []))}
                                 for r in routes if r['kind'] in {'page', 'content-page'}]
    result['interpretation'] = ('Client-side rendering only: crawlers that do not execute JavaScript see an empty shell.'
                                if csr_only else 'Server/build-time rendering signals present.' if project_modes
                                else 'No rendering signal matched; inspect entry points.')
    return result
