"""Route discovery from the framework-agnostic routes skill."""

import re

from seo_agents.exploration.repo_analysis.tools.source_index import glob_match
from seo_agents.exploration.repo_analysis.tools.skill_runner import skill

DYNAMIC_SEGMENT = re.compile(r'\[|\]|(?:^|/):|\$|\{|\*|<\w')


def normalize_route(raw, dot_segments=False):
    route = raw.strip('/')
    if dot_segments:
        route = re.sub(r'\.(?![^\[]*\])', '/', route)
        route = re.sub(r'(^|/)\$(\w+)', r'\1:\2', route)
        route = re.sub(r'(^|/)\$$', r'\1*', route)
    parts = []
    for part in route.split('/'):
        if not part or re.fullmatch(r'\(.*\)', part) or part.startswith('@'):
            continue
        if dot_segments and part.startswith('_') and part != '_index':
            continue
        parts.append(part)
    if parts and parts[-1] in {'index', '_index', '+page', 'page'}:
        parts.pop()
    return '/' + '/'.join(parts)


def discover_routes(index):
    profile = skill('routes')['profile']
    routes, claimed = [], set()
    for rule in profile['conventions']:
        pattern = re.compile(rule['pattern'])
        exclude = re.compile(rule['exclude']) if rule.get('exclude') else None
        for path in index.paths():
            if path in claimed:
                continue  # first (most specific) convention wins
            match = pattern.match(path)
            if not match:
                continue
            raw = match.group('route')
            if exclude and exclude.search(raw.strip('/')):
                continue
            claimed.add(path)
            route = normalize_route(raw, rule.get('dot_segments', False))
            routes.append({'route': route, 'kind': rule['kind'], 'method': 'file-system-convention',
                           'is_dynamic': bool(DYNAMIC_SEGMENT.search(route)),
                           'source': index.evidence_lines('route-file', path, 1, len(index.lines(path)),
                                                          route=route, route_kind=rule['kind'])})
    seen = set()
    for rule in profile['declarations']:
        regex = re.compile(rule['regex'], re.M)
        requires = re.compile(rule['requires']) if rule.get('requires') else None
        for path in index.paths():
            if not glob_match(path, rule['glob']):
                continue
            text = index.files[path]
            if requires and not requires.search(text):
                continue
            for match in regex.finditer(text):
                if (path, match.start()) in seen:
                    continue
                seen.add((path, match.start()))
                value = match.group(1).strip()
                route = value if value.startswith(('/', '*', '^')) else '/' + value
                routes.append({'route': route, 'kind': rule['kind'], 'method': 'code-declaration',
                               'is_dynamic': bool(DYNAMIC_SEGMENT.search(route)),
                               'source': index.evidence('route-declaration', path, match.start(), match.end(),
                                                        route=route, route_kind=rule['kind'])})
    routes.sort(key=lambda r: (r['kind'] not in {'page', 'content-page'}, r['route'], r['source']['location']['file']))
    return routes
