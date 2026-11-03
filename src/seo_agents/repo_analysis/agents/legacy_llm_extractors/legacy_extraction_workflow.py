"""Legacy LLM category extractors (DEPRECATED, opt-in): seven skill-guided agents on a LangGraph DAG.

    prepare -> urls -> {page_structure, internal_links, media, metadata} (parallel) -> content -> eeat
            -> validate_artifacts (agent mode) -> evidence -> END

Each category node runs one `CategoryExtractorAgent`. The snapshot, source context and element index are computed
once (or passed in by repo_analysis.py) and shared by every agent. A failure keeps finished reports in
`ExplorationIncomplete.checkpoint` so a caller can resume.
"""

import os
import hashlib
import json
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import threading
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from seo_agents.lib.prompts import LEGACY_EXTRACTOR_PROMPTS_DIR
from seo_agents.utils.state_reducers import merge_dicts
from seo_agents.utils.step_logger import done, log, progress, start
from seo_agents.repo_analysis.agents.legacy_llm_extractors.category_extractor_agent import (CategoryExtractorAgent,
                                                                                           ExtractionFailed)
from seo_agents.repo_analysis.tools.repository.repository_snapshot import RepositorySnapshotTool
from seo_agents.repo_analysis.tools.parsing.source_context import SourceContextBuilder
from seo_agents.repo_analysis.agents.legacy_llm_extractors.prior_report_handoff import build_handoff
from seo_agents.repo_analysis.agents.legacy_llm_extractors.cross_report_references import validate_content_references
from seo_agents.repo_analysis.agents.legacy_llm_extractors.observation_verification import validate_observations
from seo_agents.repo_analysis.agents.legacy_llm_extractors.report_contracts import PAGE_FIELDS, validate_report
from seo_agents.repo_analysis.tools.repository.repository_constants import REPORT_FILES
from seo_agents.repo_analysis.evidence.evidence_bundle import EvidenceBundleBuilder
from seo_agents.repo_analysis.agents.legacy_llm_extractors.page_latency_check import PageLatencyCheck
from seo_agents.repo_analysis.tools.http.runtime_observation import RuntimeObserver

REPO_ANALYSIS_DIR = Path(__file__).parents[2]  # every repo_analysis source file is part of the extraction identity


class LegacyExtractionState(TypedDict, total=False):
    repo_path: str
    artifacts: Annotated[dict, merge_dicts]
    agent_runs: Annotated[dict, merge_dicts]
    snapshot: dict
    context: dict
    evidence: dict
    checkpoint: dict


CATEGORIES = ('urls', 'page_structure', 'internal_links', 'media', 'content', 'metadata', 'eeat')
PARALLEL_CATEGORIES = ('page_structure', 'internal_links', 'media', 'metadata')
DEPENDENCIES = {'urls': (), 'page_structure': ('urls',), 'internal_links': ('urls',), 'media': ('urls',),
                'metadata': ('urls',), 'content': ('urls',) + PARALLEL_CATEGORIES, 'eeat': ('content',)}


class ExplorationIncomplete(RuntimeError):
    """A failed/interrupted scan retains completed work for a caller-controlled resume."""
    def __init__(self, error, checkpoint):
        super().__init__('Exploration incomplete: ' + str(error))
        self.checkpoint = checkpoint


def extraction_identity(mode):
    """Hash of the code, prompts, model settings and dependency versions; a resume must match it."""
    paths = sorted(REPO_ANALYSIS_DIR.rglob('*.py')) + sorted(LEGACY_EXTRACTOR_PROMPTS_DIR.rglob('*.md'))
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


def deterministic_route_candidates(snapshot):
    """File-convention/router candidates for the URL agent (and its script's `routes` input)."""
    try:
        from seo_agents.repo_analysis.tools.routing.route_discovery import RouteDiscoveryTool
        from seo_agents.repo_analysis.tools.repository.source_index import SourceIndex
        rows = RouteDiscoveryTool().discover(SourceIndex(snapshot))
    except Exception as error:  # Candidates are optional leads; never block the agent.
        log('route candidates unavailable: ' + type(error).__name__ + ': ' + str(error)[:200])
        return None
    candidates, seen = [], set()
    for row in rows:
        if row.get('kind') not in {'page', 'content-page', 'template'}:
            continue
        template = row['source']['location']['file']
        identity = (row['route'], template)
        if identity in seen:
            continue
        seen.add(identity)
        candidates.append({'url': row['route'], 'template': template, 'is_dynamic': row['is_dynamic'],
                           'kind': row['kind'], 'method': row['method']})
    return candidates


def validate_artifact_links(state):
    """Every report must cover the same routes, and content references must resolve in the other reports."""
    artifacts = state['artifacts']
    routes = None
    for report in artifacts.values():
        current = {(p['url'], p['template']) for p in report['pages']}
        if routes is not None and current != routes:
            raise ValueError('Independent agents discovered inconsistent route inventories')
        routes = current
    validate_content_references(artifacts['content'], artifacts, require_targets=True)
    return {}


# ---------------------------------------------------------------- graph nodes
class PrepareNode:
    """Node: snapshot + source context, unless the caller already passed them in."""

    def __call__(self, state):
        if 'snapshot' in state and 'context' in state:
            return {}
        snapshot = RepositorySnapshotTool().capture(state.get('repo_path'))
        return {'snapshot': snapshot, 'context': SourceContextBuilder().build(snapshot)}


class CategoryExtractionNode:
    """Node: one category report, from `CategoryExtractorAgent` (agent mode) or the static extractor (static mode)."""

    def __init__(self, category, mode, cache, shared):
        self.category, self.mode, self.cache, self.shared = category, mode, cache, shared

    def __call__(self, state):
        key = self.category
        saved = dict(state.get("artifacts") or {})
        label = 'check ' + str(CATEGORIES.index(key) + 1) + '/' + str(len(CATEGORIES)) + ': ' + key
        if key in saved:
            log('SKIP ' + label + ' (reusing verified report)')
            return {}  # Resume inputs were checked against snapshot and extractor identities.
        begun = start(label)
        if self.mode == 'agent':
            report, result = self.extract_with_agent(key, saved, state)
        else:
            report, result = self.extract_statically(key, state)
        log('report ready: ' + REPORT_FILES[key] + ' (' + str(len(report['pages'])) + ' pages; retained in memory)')
        for page in report['pages']:
            counts = {field: len(page[field]) if isinstance(page[field], list) else page[field]
                      for field in sorted(PAGE_FIELDS[key] - {'interpretation', 'inheritance_note'})}
            counts['limitations'] = len(page['coverage']['limitations'])
            log('agent ' + key + ': page ' + json.dumps(page['url']) + ' completed ' + json.dumps(counts))
        done(label, begun)
        return result

    def extract_with_agent(self, key, saved, state):
        route_pages = saved.get('urls', {}).get('pages')
        routes = [{k: p[k] for k in ('url', 'template', 'is_dynamic', 'route_family', 'instance_status', 'locale') if k in p}
                  for p in route_pages] if route_pages is not None else None
        handoff = build_handoff(key, saved, state.get('agent_runs') or {})
        extraction = CategoryExtractorAgent(key).run(
            snapshot=state['snapshot'], routes=routes, context=state['context'], handoff=handoff,
            element_index=self.shared.get('element_index'),
            route_candidates=self.shared.get('route_candidates') if key == 'urls' else None)
        report = extraction['report']
        return report, {'artifacts': {key: report}, 'agent_runs': {key: extraction['run']}}

    def extract_statically(self, key, state):
        # Explicit legacy/offline mode; never a silent fallback from a failed agent.
        from seo_agents.repo_analysis.agents.legacy_llm_extractors.static_mode.static_inventory import inventory
        from seo_agents.repo_analysis.agents.legacy_llm_extractors.static_mode.static_inventory import pages
        from seo_agents.repo_analysis.tools.repository.repository_constants import resolve_repo_path
        cache = self.cache
        repo = resolve_repo_path(state.get('repo_path'))
        identity = (str(repo), state['snapshot']['sha256'])
        with cache['lock']:
            if cache.get('identity') != identity:
                cache['pages'] = pages(repo)
                cache['identity'] = identity
            page_inventory = cache['pages']
        report = inventory(repo, key, page_inventory=page_inventory)
        return report, {'artifacts': {key: report}}


class ValidateArtifactsNode:
    """Node: cross-report validation (agent mode only)."""

    def __call__(self, state):
        with progress('cross-report validation'):
            return validate_artifact_links(state)


class EvidenceNode:
    """Node: combined evidence bundle plus the HTTP baseline of concrete URLs."""

    def __init__(self, site_url, max_runtime_pages, runtime_timeout):
        self.site_url, self.max_runtime_pages, self.runtime_timeout = site_url, max_runtime_pages, runtime_timeout

    def __call__(self, state):
        with progress('build combined evidence'):
            bundle = EvidenceBundleBuilder().build(state, site_url=self.site_url)
        with progress('runtime page checks'):
            PageLatencyCheck().measure(bundle, site_url=self.site_url, max_pages=self.max_runtime_pages,
                                       timeout=self.runtime_timeout)
        return {'evidence': bundle}


# ---------------------------------------------------------------- workflow
class LegacyExtractionWorkflow:
    """The seven legacy LLM category extractors as one LangGraph run (with resume / checkpoint support)."""

    def __init__(self, mode=None, *, site_url=None, max_runtime_pages=100, runtime_timeout=12):
        self.mode = mode or os.getenv('SEO_EXPLORATION_MODE', 'agent')
        self.site_url, self.max_runtime_pages, self.runtime_timeout = site_url, max_runtime_pages, runtime_timeout

    def build_graph(self, shared=None):
        mode = self.mode
        if mode not in {'agent', 'static'}:
            raise ValueError('SEO_EXPLORATION_MODE must be agent or static')
        if mode == 'static':
            log('DEPRECATED: static mode (React/Next-only legacy extractor). The framework-agnostic site model '
                '(site_model.explore_site) is the offline, no-LLM path; static mode remains for offline graph tests only.')
        RuntimeObserver().validate_config(self.site_url, self.max_runtime_pages, self.runtime_timeout)
        shared = shared if shared is not None else {}
        graph = StateGraph(LegacyExtractionState)
        cache = {'lock': threading.Lock()}
        graph.add_node('prepare', PrepareNode())
        graph.add_edge(START, 'prepare')
        for key in CATEGORIES:
            graph.add_node(key, CategoryExtractionNode(key, mode, cache, shared))
        # Fan-out / fan-in edges follow DEPENDENCIES.
        graph.add_edge('prepare', 'urls')
        for key in PARALLEL_CATEGORIES:
            graph.add_edge('urls', key)
        graph.add_edge(list(PARALLEL_CATEGORIES), 'content')
        graph.add_edge('content', 'eeat')
        if mode == 'agent':
            graph.add_node('validate_artifacts', ValidateArtifactsNode())
            graph.add_edge('eeat', 'validate_artifacts')
            graph.add_edge('validate_artifacts', 'evidence')
        else:
            graph.add_edge('eeat', 'evidence')
        graph.add_node('evidence', EvidenceNode(self.site_url, self.max_runtime_pages, self.runtime_timeout))
        graph.add_edge('evidence', END)
        return graph.compile()

    def run(self, repo_path=None, *, resume=None, on_checkpoint=None, snapshot=None, context=None,
            element_index=None, routes=None, max_concurrency=None):
        """Run the seven categories. Pass `snapshot`/`context`/`element_index` to reuse the pipeline's single capture."""
        begun = start("on-page graph (7 checks)")
        max_concurrency = int(os.getenv('SEO_AGENT_CONCURRENCY', '4')) if max_concurrency is None else max_concurrency
        if type(max_concurrency) is not int or not 1 <= max_concurrency <= 16:
            raise ValueError('SEO_AGENT_CONCURRENCY must be an integer from 1 to 16')
        log('on-page execution: DAG urls -> {page_structure, internal_links, media, metadata} -> content -> eeat '
            '(max_concurrency=' + str(max_concurrency) + ')')
        mode = self.mode
        shared = {}
        graph = self.build_graph(shared)
        if snapshot is None:
            with progress('repository snapshot'):
                snapshot = RepositorySnapshotTool().capture(repo_path)
        log('repository captured: ' + str(len(snapshot['files'])) + ' source files, ' + str(len(snapshot['assets'])) + ' assets')
        if context is None:
            with progress('repository context'):
                context = SourceContextBuilder().build(snapshot)
        if mode == 'agent' and snapshot['files']:
            if element_index is None:
                with progress('element index'):
                    from seo_agents.repo_analysis.tools.parsing.element_index import ElementIndexBuilder
                    element_index = ElementIndexBuilder().build(snapshot)
            shared['element_index'] = element_index
            shared['route_candidates'] = routes if routes is not None else deterministic_route_candidates(snapshot)
        header = {'snapshot_sha256': snapshot['sha256'], 'extraction_identity': extraction_identity(mode),
                  'mode': mode, 'completed': []}
        start_state = {'artifacts': {}, 'snapshot': snapshot, 'context': context, 'checkpoint': header}
        if resume:
            self.restore(resume, header, snapshot, start_state)
        if repo_path:
            start_state["repo_path"] = repo_path
        result = dict(start_state)
        try:
            if not snapshot['files']:
                raise ValueError('No textual source captured; inventory is retained for diagnosing scope/capture gaps')
            for update in graph.stream(start_state, config={'max_concurrency': max_concurrency}, stream_mode='updates'):
                for values in update.values():
                    if not values:
                        continue
                    for key, value in values.items():
                        if key in {'artifacts', 'agent_runs'}:
                            result[key] = merge_dicts(result.get(key), value)
                        else:
                            result[key] = value
                    if 'artifacts' in values:
                        log('on-page progress: ' + str(len(result['artifacts'])) + '/' + str(len(CATEGORIES)) + ' reports completed')
                result['checkpoint'] = {**header, 'completed': [key for key in CATEGORIES if key in result['artifacts']],
                                        'agent_runs': result.get('agent_runs', {})}
                if on_checkpoint:
                    on_checkpoint(dict(result))
        except (Exception, KeyboardInterrupt) as error:
            completed = [key for key in CATEGORIES if key in result['artifacts']]
            log('on-page stopped after ' + str(len(completed)) + '/' + str(len(CATEGORIES)) +
                ' reports; completed: ' + ', '.join(completed))
            if isinstance(error, ExtractionFailed):
                result['agent_runs'] = {**result.get('agent_runs', {}), error.category: error.run}
                result['checkpoint'] = {**result['checkpoint'], 'agent_runs': result['agent_runs']}
                if on_checkpoint:
                    on_checkpoint(dict(result))
            raise ExplorationIncomplete(error, result) from error
        result['artifacts'] = result['evidence']['reports']  # One shared object for reports and evidence.
        result['evidence']['checkpoint'] = result['checkpoint']
        done("on-page graph (7 checks)", begun)
        return result

    def restore(self, resume, header, snapshot, start_state):
        """Reuse verified reports from an earlier checkpoint (identity must match exactly)."""
        old_header = resume.get('checkpoint', {})
        if any(old_header.get(key) != header[key] for key in ('snapshot_sha256', 'extraction_identity', 'mode')):
            raise ValueError('Checkpoint identity changed; recompute after source/config/skill/model changes')
        if not snapshot.get('coverage', {}).get('capture_complete_within_scope'):
            raise ValueError('Cannot reuse results from a partial source capture; narrow scope or increase capture budgets')
        reports = resume.get('artifacts', resume.get('reports', {}))
        for key, report in reports.items():
            validate_observations(key, report, snapshot) if self.mode == 'agent' else validate_report(key, report)
        start_state['artifacts'] = dict(reports)
        start_state['agent_runs'] = dict(resume.get('agent_runs', old_header.get('agent_runs', {})))


def build_legacy_extraction_graph(mode=None, *, site_url=None, max_runtime_pages=100, runtime_timeout=12, shared=None):
    return LegacyExtractionWorkflow(mode, site_url=site_url, max_runtime_pages=max_runtime_pages,
                                    runtime_timeout=runtime_timeout).build_graph(shared)


def run_legacy_extraction(repo_path=None, *, mode=None, site_url=None, max_runtime_pages=100,
                          runtime_timeout=12, resume=None, on_checkpoint=None, snapshot=None, context=None,
                          element_index=None, routes=None, max_concurrency=None):
    """Function form of `LegacyExtractionWorkflow(mode, ...).run(repo_path, ...)`."""
    return LegacyExtractionWorkflow(mode, site_url=site_url, max_runtime_pages=max_runtime_pages,
                                    runtime_timeout=runtime_timeout).run(
        repo_path, resume=resume, on_checkpoint=on_checkpoint, snapshot=snapshot, context=context,
        element_index=element_index, routes=routes, max_concurrency=max_concurrency)
