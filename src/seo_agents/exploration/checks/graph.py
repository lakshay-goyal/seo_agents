"""Seven independent skill-guided agents, followed by cross-report validation."""

import os
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from seo_agents.log import done, start
from seo_agents.exploration.checks.agents.extractor import run_exploration_agent
from seo_agents.exploration.checks.agents.repository import snapshot_repository

class OnpageState(TypedDict, total=False):
    repo_path: str
    artifacts: dict
    agent_runs: dict
    snapshot: dict


CHECKS = ('urls', 'content', 'page_structure', 'internal_links', 'metadata', 'media', 'eeat')


def make_node(key, mode):
    def node(state):
        begun = start("check: " + key)
        saved = dict(state.get("artifacts") or {})
        if mode == 'agent':
            route_pages = saved.get('urls', {}).get('pages')
            routes = [{k: p[k] for k in ('url', 'template', 'is_dynamic')} for p in route_pages] if route_pages is not None else None
            extraction = run_exploration_agent(key, snapshot=state['snapshot'], routes=routes)
            saved[key] = extraction['report']
            runs = dict(state.get('agent_runs') or {})
            runs[key] = extraction['run']
            result = {'artifacts': saved, 'agent_runs': runs}
        else:
            # Explicit legacy/offline mode; never a silent fallback from a failed agent.
            from seo_agents.exploration.checks.tools.inventory import inventory
            from seo_agents.exploration.checks.tools.helpers import resolve_repo_path
            saved[key] = inventory(resolve_repo_path(state.get('repo_path')), key)
            result = {'artifacts': saved}
        done("check: " + key, begun)
        return result
    return node


def validate_artifact_links(state):
    artifacts = state['artifacts']
    routes = None
    for report in artifacts.values():
        current = {(p['url'], p['template']) for p in report['pages']}
        if routes is not None and current != routes:
            raise ValueError('Independent agents discovered inconsistent route inventories')
        routes = current
    links = {(p['url'], p['template']): {r['source_id'] for r in p['links']} for p in artifacts['internal_links']['pages']}
    media = {(p['url'], p['template']): {r['source_id'] for r in p['items']} for p in artifacts['media']['pages']}
    for page in artifacts['content']['pages']:
        for field, targets, filename in [('link_refs', links, 'internal_links.json'), ('media_refs', media, 'media.json')]:
            for ref in page[field]:
                if ref['report'] != filename or ref['source_id'] not in targets.get((page['url'], page['template']), set()):
                    raise ValueError('Unmatched cross-report reference: ' + ref['source_id'])
    return {}


def build_onpage_graph(mode=None):
    mode = mode or os.getenv('SEO_EXPLORATION_MODE', 'agent')
    if mode not in {'agent', 'static'}:
        raise ValueError('SEO_EXPLORATION_MODE must be agent or static')
    graph = StateGraph(OnpageState)
    for key in CHECKS:
        graph.add_node(key, make_node(key, mode))
    if mode == 'agent':
        graph.add_node('snapshot', lambda state: {'snapshot': snapshot_repository(state.get('repo_path'))})
        graph.add_edge(START, 'snapshot')
        graph.add_edge('snapshot', CHECKS[0])
        graph.add_node('validate_artifacts', validate_artifact_links)
        graph.add_edge(CHECKS[-1], 'validate_artifacts')
        graph.add_edge('validate_artifacts', END)
    else:
        graph.add_edge(START, CHECKS[0])
        graph.add_edge(CHECKS[-1], END)
    for i in range(len(CHECKS) - 1):
        graph.add_edge(CHECKS[i], CHECKS[i + 1])
    return graph.compile()


def run_onpage_graph(repo_path=None, *, mode=None):
    begun = start("on-page graph (7 checks)")
    graph = build_onpage_graph(mode)
    start_state = {"artifacts": {}}
    if repo_path:
        start_state["repo_path"] = repo_path
    result = graph.invoke(start_state)
    done("on-page graph (7 checks)", begun)
    return result
