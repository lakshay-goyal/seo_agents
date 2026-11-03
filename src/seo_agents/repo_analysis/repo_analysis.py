"""Repository analysis: the one LangGraph workflow that calls every repo-analysis agent and tool, in order.

    START
      -> capture_snapshot              RepositorySnapshotTool + SourceIndex (one capture shared by every step)
      -> code_exploration              CodeExplorationAgent
      -> routes                        RouteDiscoveryAgent
      -> page_inventory                PageInventoryAgent
      -> keywords                      KeywordExtractionAgent
      -> sitemap_robots                SitemapRobotsAgent
      -> canonicals_redirects          CanonicalsRedirectsAgent
      -> client_diagnostics            ClientDiagnosticsAgent
      -> rendering_strategy            RenderingStrategyAgent
      -> performance_web_vitals        PerformanceAssetsAgent
      -> https_security_headers        HttpsSecurityHeadersAgent
      -> structured_data               StructuredDataAgent
      -> mobile_parity                 MobileParityAgent
      -> hreflang_i18n                 HreflangI18nAgent
      -> shareability_link_assets      ShareabilityLinkAssetsAgent
      -> brand_entity_signals          BrandEntitySignalsAgent
      -> local_business_presence       LocalBusinessPresenceAgent
      -> ai_search_readiness           AiSearchReadinessAgent
      -> write_repo_context            RepoContextBuilder -> seo_reports/repo_context.json
      -> build_site_model              SiteModelBuilder -> seo_reports/site_model/ + seo_reports/exploration/
                                       (its own LangGraph: site_model/site_model_workflow.py; gap workers call models)
      -> run_legacy_llm_extractors     LegacyExtractionWorkflow (DEPRECATED, opt-in: SEO_RUN_LLM_AGENTS=1)
      -> finish                        rewrite repo_context.json with the site-model / legacy summaries
    END

The deterministic agents never call a model; their tools are listed in lib/tool_mapping.py. Each artifact is written
as soon as it exists, so a later failure keeps earlier outputs. A legacy-extractor failure never blocks the rest.

CLI (no clone, no PR): python -m seo_agents.repo_analysis /path/to/site [--site-url URL] [--render] [--build]
"""

import os
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from seo_agents.repo_analysis.agents.codebase.code_exploration_agent import CodeExplorationAgent
from seo_agents.repo_analysis.agents.codebase.keyword_extraction_agent import KeywordExtractionAgent
from seo_agents.repo_analysis.agents.codebase.page_inventory_agent import PageInventoryAgent
from seo_agents.repo_analysis.agents.codebase.route_discovery_agent import RouteDiscoveryAgent
from seo_agents.repo_analysis.agents.crawling_indexing.canonicals_redirects_agent import CanonicalsRedirectsAgent
from seo_agents.repo_analysis.agents.crawling_indexing.hreflang_i18n_agent import HreflangI18nAgent
from seo_agents.repo_analysis.agents.crawling_indexing.sitemap_robots_agent import SitemapRobotsAgent
from seo_agents.repo_analysis.agents.entity_signals.ai_search_readiness_agent import AiSearchReadinessAgent
from seo_agents.repo_analysis.agents.entity_signals.brand_entity_signals_agent import BrandEntitySignalsAgent
from seo_agents.repo_analysis.agents.entity_signals.local_business_presence_agent import LocalBusinessPresenceAgent
from seo_agents.repo_analysis.agents.entity_signals.shareability_link_assets_agent import ShareabilityLinkAssetsAgent
from seo_agents.repo_analysis.agents.entity_signals.structured_data_agent import StructuredDataAgent
from seo_agents.repo_analysis.agents.rendering_performance.client_diagnostics_agent import ClientDiagnosticsAgent
from seo_agents.repo_analysis.agents.rendering_performance.mobile_parity_agent import MobileParityAgent
from seo_agents.repo_analysis.agents.rendering_performance.performance_assets_agent import PerformanceAssetsAgent
from seo_agents.repo_analysis.agents.rendering_performance.rendering_strategy_agent import RenderingStrategyAgent
from seo_agents.repo_analysis.agents.security.https_security_headers_agent import HttpsSecurityHeadersAgent
from seo_agents.repo_analysis.repo_context.repo_context_builder import RepoContextBuilder
from seo_agents.repo_analysis.tools.repository.repository_snapshot import RepositorySnapshotTool
from seo_agents.repo_analysis.tools.repository.source_index import SourceIndex
from seo_agents.utils.state_reducers import merge_dicts
from seo_agents.utils.step_logger import done, log, progress, start

DEFAULT_OUTPUT = 'seo_reports/repo_context.json'
DEFAULT_SITE_MODEL = 'seo_reports/site_model'

# Execution order of the deterministic agents. An agent may only read results of agents listed before it.
DETERMINISTIC_AGENTS = (
    # Codebase structure: manifests/dependencies, routes, page inventory (tags, headings, text, links, media), keywords
    CodeExplorationAgent(),
    RouteDiscoveryAgent(),
    PageInventoryAgent(),
    KeywordExtractionAgent(),
    # Crawl, rendering, Core Web Vitals, security, structured data, mobile and i18n
    SitemapRobotsAgent(),
    CanonicalsRedirectsAgent(),
    ClientDiagnosticsAgent(),
    RenderingStrategyAgent(),
    PerformanceAssetsAgent(),
    HttpsSecurityHeadersAgent(),
    StructuredDataAgent(),  # single @type source for the entity agents below
    MobileParityAgent(),
    HreflangI18nAgent(),
    # Shareability, brand/entity, local business presence and AI-search readiness
    ShareabilityLinkAssetsAgent(),
    BrandEntitySignalsAgent(),
    LocalBusinessPresenceAgent(),
    AiSearchReadinessAgent(),
)


class RepoAnalysisState(TypedDict, total=False):
    snapshot: dict
    index: Any
    results: Annotated[dict, merge_dicts]   # {agent name: result}
    repo_context: dict
    repo_context_path: Any
    site_model: Any
    site_model_dir: Any
    legacy_state: dict


class AgentNode:
    """LangGraph node that runs one deterministic agent and stores its result under the agent's name."""

    def __init__(self, agent):
        self.agent = agent

    def __call__(self, state):
        results = state.get('results') or {}
        return {'results': {self.agent.name: run_agent(self.agent, state['index'], results)}}


def run_agent(agent, index, results):
    """Run one agent with the numbered progress line used in the run log ('agent 3: page_inventory')."""
    with progress('agent ' + str(len(results) + 1) + ': ' + agent.name):
        return agent.run(index, results)


def explore_codebase(index, agents=DETERMINISTIC_AGENTS):
    """Run every deterministic agent over one snapshot index, in order. Returns {agent name: result}."""
    results = {}
    for agent in agents:
        results[agent.name] = run_agent(agent, index, results)
    return results


class RepoAnalysisWorkflow:
    """Explore one checkout: deterministic agents -> repo_context.json -> site model -> optional legacy extractors.

    output_path     repo_context.json path (default `<repo>/seo_reports/repo_context.json`; False skips writing)
    site_model_dir  site model directory (default `<repo>/seo_reports/site_model`)
    site_url / render / build   runtime lenses (see `site_model_builder.explore_site`); env SEO_SITE_URL/SEO_RENDER/SEO_BUILD
    run_agents      also run the 7 legacy LLM category extractors (default: env SEO_RUN_LLM_AGENTS, else off)
    include_content 'none' (references + hashes), 'referenced' or 'all' for repo_context.json
    """

    def __init__(self, github_url=None, repo_path=None, *, output_path=None, run_agents=None, include_content='none',
                 site_url=None, render=None, build=None, site_model=True, site_model_dir=None,
                 agents=DETERMINISTIC_AGENTS):
        self.github_url, self.repo_path = github_url, repo_path
        self.root = Path(repo_path or Path.cwd())
        self.output_path, self.run_agents, self.include_content = output_path, run_agents, include_content
        self.site_url, self.render, self.build = site_url, render, build
        self.site_model, self.site_model_dir = site_model, site_model_dir
        self.agents = tuple(agents)

    # ------------------------------------------------------------ graph
    def build_graph(self):
        graph = StateGraph(RepoAnalysisState)
        steps = [('capture_snapshot', self.capture_snapshot)]
        steps += [(agent.name, AgentNode(agent)) for agent in self.agents]
        steps += [('write_repo_context', self.write_repo_context), ('build_site_model', self.build_site_model),
                  ('run_legacy_llm_extractors', self.run_legacy_llm_extractors), ('finish', self.finish)]
        previous = START
        for name, node in steps:
            graph.add_node(name, node)
            graph.add_edge(previous, name)
            previous = name
        graph.add_edge(previous, END)
        return graph.compile()

    def run(self):
        """Returns the legacy state (if any) plus `repo_context`, `repo_context_path`, `site_model`, `site_model_dir`."""
        label = "repo analysis: " + (self.github_url or str(self.repo_path or Path.cwd()))
        begun = start(label)
        final = self.build_graph().invoke({}, config={'recursion_limit': 2 * len(self.agents) + 20})
        state = dict(final.get('legacy_state') or {})
        state['repo_context'] = final['repo_context']
        state['repo_context_path'] = final.get('repo_context_path')
        state['site_model'] = final.get('site_model')
        state['site_model_dir'] = final.get('site_model_dir')
        done(label, begun)
        return state

    # ------------------------------------------------------------ nodes
    def capture_snapshot(self, state):
        with progress('repository snapshot (single capture shared by every stage)'):
            snapshot = RepositorySnapshotTool().capture(self.root)
            index = SourceIndex(snapshot)
        return {'snapshot': snapshot, 'index': index, 'results': {}}

    def write_repo_context(self, state):
        builder = RepoContextBuilder()
        context = builder.build(state['index'], state['results'], repo_path=self.root, github_url=self.github_url,
                                include_content=self.include_content)
        path = None
        if self.output_path is not False:
            path = builder.write(context, self.output_path or self.root / DEFAULT_OUTPUT)
        return {'repo_context': context, 'repo_context_path': path}

    def build_site_model(self, state):
        if not self.site_model:
            return {'site_model': None, 'site_model_dir': None}
        from seo_agents.repo_analysis.site_model.site_model_builder import SiteModelBuilder
        model_dir = Path(self.site_model_dir or self.root / DEFAULT_SITE_MODEL)
        writes = self.output_path is not False or self.site_model_dir
        model = SiteModelBuilder().build(self.root, site_url=self.site_url, render=self.render, build=self.build,
                                         snapshot=state['snapshot'], deterministic=state['results'],
                                         output_dir=model_dir if writes else None)
        state['repo_context']['site_model'] = {'directory': str(model_dir), 'pages': len(model['pages']),
                                               'framework': model['site']['framework']['primary'],
                                               'note': 'Page-centric model: index.json, site.json, pages/<id>.json.'}
        return {'site_model': model, 'site_model_dir': str(model_dir) if writes else None,
                'repo_context': state['repo_context']}

    def run_legacy_llm_extractors(self, state):
        run_agents = self.run_agents
        if run_agents is None:
            run_agents = os.getenv('SEO_RUN_LLM_AGENTS', '0') == '1'
        if not run_agents:
            return {'legacy_state': {}}
        context, model, snapshot = state['repo_context'], state.get('site_model'), state['snapshot']
        try:
            element_index = (model or {}).get('_element_index')
            if element_index is None and model is not None:
                from seo_agents.repo_analysis.tools.parsing.element_index import ElementIndexBuilder
                element_index = ElementIndexBuilder().build(snapshot)
            legacy = run_legacy_llm_extractors(repo_path=self.repo_path, snapshot=snapshot, element_index=element_index,
                                               routes=state['results'].get('routes'))
            artifacts = legacy.get("artifacts") or {}
            log("LLM agent reports in memory: " + str(sorted(artifacts)))
            context['llm_agent_reports'] = {'categories': sorted(artifacts), 'status': 'completed',
                                            'note': 'Deprecated legacy reports, published separately under seo_reports/onpage/.'}
        except Exception as error:  # noqa: BLE001 - legacy extractors must never block the site model / repo context
            log('legacy LLM agents failed (site model and repo_context.json are kept): ' + type(error).__name__ + ': ' + str(error)[:300])
            failed = getattr(error, 'checkpoint', None) or {}
            legacy = {}
            context['llm_agent_reports'] = {'status': 'failed', 'error': type(error).__name__ + ': ' + str(error)[:500],
                                            'completed_categories': sorted((failed.get('artifacts') or {}))}
        return {'legacy_state': legacy, 'repo_context': context}

    def finish(self, state):
        if state.get('repo_context_path'):
            RepoContextBuilder().write(state['repo_context'], state['repo_context_path'])
        model = state.get('site_model')
        if isinstance(model, dict):
            model.pop('_element_index', None)
        return {'repo_context_path': str(state['repo_context_path']) if state.get('repo_context_path') else None}


def run_legacy_llm_extractors(repo_path=None, *, snapshot=None, element_index=None, routes=None):
    """Seven legacy LLM category extractors (legacy_extraction_workflow.py), reusing the pipeline's single snapshot."""
    from seo_agents.repo_analysis.agents.legacy_llm_extractors.legacy_extraction_workflow import LegacyExtractionWorkflow
    from seo_agents.repo_analysis.tools.parsing.source_context import SourceContextBuilder

    context = SourceContextBuilder().build(snapshot) if snapshot is not None else None
    return LegacyExtractionWorkflow().run(repo_path, snapshot=snapshot, context=context,
                                          element_index=element_index, routes=routes)


def repo_analysis(github_url=None, repo_path=None, *, output_path=None, run_agents=None,
                  include_content='none', site_url=None, render=None, build=None, site_model=True,
                  site_model_dir=None):
    """Explore the whole codebase and write the SEO context JSON plus the page-centric site model.

    Function form of `RepoAnalysisWorkflow(...).run()`; see that class for the options.
    """
    return RepoAnalysisWorkflow(github_url, repo_path, output_path=output_path, run_agents=run_agents,
                                include_content=include_content, site_url=site_url, render=render, build=build,
                                site_model=site_model, site_model_dir=site_model_dir).run()
