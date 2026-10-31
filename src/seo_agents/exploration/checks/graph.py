"""Seven independent skill-guided agents, followed by cross-report validation."""

import os
import hashlib
import json
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from seo_agents.log import done, start
from seo_agents.exploration.checks.agents.extractor import ExtractionFailed, run_exploration_agent
from seo_agents.exploration.checks.agents.repository import snapshot_repository
from seo_agents.exploration.checks.agents.context import build_context
from seo_agents.exploration.checks.agents.verification import validate_observations
from seo_agents.exploration.checks.tools.contracts import validate_report
from seo_agents.exploration.checks.evidence import build_evidence
from seo_agents.exploration.checks.technical.page_latency import measure_page_latency
from seo_agents.exploration.checks.technical.runtime import validate_runtime_config

class OnpageState(TypedDict, total=False):
    repo_path: str
    artifacts: dict
    agent_runs: dict
    snapshot: dict
    context: dict
    evidence: dict
    checkpoint: dict


CHECKS = ('urls', 'content', 'page_structure', 'internal_links', 'metadata', 'media', 'eeat')


class ExplorationIncomplete(RuntimeError):
    """A failed/interrupted scan retains completed work for a caller-controlled resume."""
    def __init__(self, error, checkpoint):
        super().__init__('Exploration incomplete: ' + str(error))
        self.checkpoint = checkpoint


def extraction_identity(mode):
    directory = Path(__file__).parent
    paths = sorted(directory.rglob('*.py')) + sorted((directory / 'agents/skills').rglob('*.md'))
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    digest.update(json.dumps([mode, os.getenv('OPENROUTER_MODEL'), os.getenv('OPENROUTER_BASE_URL'),
        os.getenv('OPENROUTER_MAX_TOKENS'), os.getenv('OPENROUTER_REASONING_EFFORT'),
        os.getenv('SEO_MAX_DISCOVERY_ROUNDS'), dependency_versions()]).encode())
    return digest.hexdigest()


@lru_cache(maxsize=1)
def dependency_versions():
    versions = {}
    for name in ('langchain', 'langgraph', 'langchain-core', 'langchain-openai',
                 'tree-sitter', 'tree-sitter-javascript', 'tree-sitter-typescript'):
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = 'unavailable'
    return versions


def prepare(state):
    if 'snapshot' in state and 'context' in state:
        return {}
    snapshot = snapshot_repository(state.get('repo_path'))
    return {'snapshot': snapshot, 'context': build_context(snapshot)}


def make_node(key, mode, cache):
    def node(state):
        begun = start("check: " + key)
        saved = dict(state.get("artifacts") or {})
        if key in saved:
            return {}  # Resume inputs were checked against snapshot and extractor identities.
        if mode == 'agent':
            route_pages = saved.get('urls', {}).get('pages')
            routes = [{k: p[k] for k in ('url', 'template', 'is_dynamic', 'route_family', 'instance_status', 'locale') if k in p}
                      for p in route_pages] if route_pages is not None else None
            extraction = run_exploration_agent(key, snapshot=state['snapshot'], routes=routes, context=state['context'])
            saved[key] = extraction['report']
            runs = dict(state.get('agent_runs') or {})
            runs[key] = extraction['run']
            result = {'artifacts': saved, 'agent_runs': runs}
        else:
            # Explicit legacy/offline mode; never a silent fallback from a failed agent.
            from seo_agents.exploration.checks.tools.inventory import inventory
            from seo_agents.exploration.checks.tools.inventory import pages
            from seo_agents.exploration.checks.tools.helpers import resolve_repo_path
            repo = resolve_repo_path(state.get('repo_path'))
            identity = (str(repo), state['snapshot']['sha256'])
            if cache.get('identity') != identity:
                cache['pages'] = pages(repo)
                cache['identity'] = identity
            saved[key] = inventory(repo, key, page_inventory=cache['pages'])
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


def build_onpage_graph(mode=None, *, site_url=None, max_runtime_pages=100, runtime_timeout=12):
    mode = mode or os.getenv('SEO_EXPLORATION_MODE', 'agent')
    if mode not in {'agent', 'static'}:
        raise ValueError('SEO_EXPLORATION_MODE must be agent or static')
    validate_runtime_config(site_url, max_runtime_pages, runtime_timeout)
    graph = StateGraph(OnpageState)
    cache = {}
    graph.add_node('prepare', prepare)
    graph.add_edge(START, 'prepare')
    graph.add_edge('prepare', CHECKS[0])
    for key in CHECKS:
        graph.add_node(key, make_node(key, mode, cache))
    if mode == 'agent':
        graph.add_node('validate_artifacts', validate_artifact_links)
        graph.add_edge(CHECKS[-1], 'validate_artifacts')
        graph.add_edge('validate_artifacts', 'evidence')
    else:
        graph.add_edge(CHECKS[-1], 'evidence')
    def evidence_node(state):
        bundle = build_evidence(state, site_url=site_url)
        measure_page_latency(bundle, site_url=site_url, max_pages=max_runtime_pages, timeout=runtime_timeout)
        return {'evidence': bundle}
    graph.add_node('evidence', evidence_node)
    graph.add_edge('evidence', END)
    for i in range(len(CHECKS) - 1):
        graph.add_edge(CHECKS[i], CHECKS[i + 1])
    return graph.compile()


def run_onpage_graph(repo_path=None, *, mode=None, site_url=None, max_runtime_pages=100,
                     runtime_timeout=12, resume=None, on_checkpoint=None):
    begun = start("on-page graph (7 checks)")
    mode = mode or os.getenv('SEO_EXPLORATION_MODE', 'agent')
    graph = build_onpage_graph(mode, site_url=site_url, max_runtime_pages=max_runtime_pages, runtime_timeout=runtime_timeout)
    snapshot = snapshot_repository(repo_path)
    header = {'snapshot_sha256': snapshot['sha256'], 'extraction_identity': extraction_identity(mode),
              'mode': mode, 'completed': []}
    start_state = {'artifacts': {}, 'snapshot': snapshot, 'context': build_context(snapshot), 'checkpoint': header}
    if resume:
        old_header = resume.get('checkpoint', {})
        if any(old_header.get(key) != header[key] for key in ('snapshot_sha256', 'extraction_identity', 'mode')):
            raise ValueError('Checkpoint identity changed; recompute after source/config/skill/model changes')
        if not snapshot.get('coverage', {}).get('capture_complete_within_scope'):
            raise ValueError('Cannot reuse results from a partial source capture; narrow scope or increase capture budgets')
        reports = resume.get('artifacts', resume.get('reports', {}))
        for key, report in reports.items():
            validate_observations(key, report, snapshot) if mode == 'agent' else validate_report(key, report)
        start_state['artifacts'] = dict(reports)
        start_state['agent_runs'] = dict(resume.get('agent_runs', old_header.get('agent_runs', {})))
    if repo_path:
        start_state["repo_path"] = repo_path
    result = dict(start_state)
    try:
        if not snapshot['files']:
            raise ValueError('No textual source captured; inventory is retained for diagnosing scope/capture gaps')
        for update in graph.stream(start_state, stream_mode='updates'):
            for values in update.values():
                if values:
                    result.update(values)
            result['checkpoint'] = {**header, 'completed': [key for key in CHECKS if key in result['artifacts']],
                                    'agent_runs': result.get('agent_runs', {})}
            if on_checkpoint:
                on_checkpoint(dict(result))
    except (Exception, KeyboardInterrupt) as error:
        if isinstance(error, ExtractionFailed):
            result['agent_runs'] = {**result.get('agent_runs', {}), error.category: error.run}
            result['checkpoint'] = {**result['checkpoint'], 'agent_runs': result['agent_runs']}
            if on_checkpoint:
                on_checkpoint(dict(result))
        raise ExplorationIncomplete(error, result) from error
    result['evidence']['checkpoint'] = result['checkpoint']
    done("on-page graph (7 checks)", begun)
    return result
