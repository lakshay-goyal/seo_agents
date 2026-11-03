"""Which tools every agent and workflow stage is given. Read top to bottom to see who calls what.

TOOL_CLASSES      tool name -> 'module:Class' (every tool is one class; `tool_class(name)` imports it)
AGENT_TOOLS       agent / stage name -> the tool names it uses
LEGACY_EXTRACTOR_TOOLS  the LangChain tools (in prompt order) handed to each legacy LLM category extractor

A test (tests/test_tool_mapping.py) checks that every mapped tool resolves to a real class and that every agent
class has an entry here, so this file cannot silently drift from the code.
"""

from __future__ import annotations

from importlib import import_module

_RA = 'seo_agents.repo_analysis'
_KR = 'seo_agents.keyword_research'

TOOL_CLASSES = {
    # ---- repository capture and parsing (repo_analysis/tools)
    'repository_snapshot': f'{_RA}.tools.repository.repository_snapshot:RepositorySnapshotTool',
    'source_index': f'{_RA}.tools.repository.source_index:SourceIndex',
    'framework_detection': f'{_RA}.tools.repository.framework_detection:FrameworkDetector',
    'project_profile': f'{_RA}.tools.repository.project_profile:ProjectProfileTool',
    'element_index': f'{_RA}.tools.parsing.element_index:ElementIndexBuilder',
    'source_context': f'{_RA}.tools.parsing.source_context:SourceContextBuilder',
    'safe_xml_parser': f'{_RA}.tools.parsing.safe_xml_parser:SafeXmlParser',
    'framework_route_adapters': f'{_RA}.tools.routing.framework_route_adapters:FrameworkRouteAdapters',
    'head_recipes': f'{_RA}.tools.routing.head_recipes:HeadRecipeTool',
    'route_discovery': f'{_RA}.tools.routing.route_discovery:RouteDiscoveryTool',
    'page_inventory': f'{_RA}.tools.content.page_inventory:PageInventoryTool',
    'keyword_extraction': f'{_RA}.tools.content.keyword_extraction:KeywordExtractionTool',
    'asset_probe': f'{_RA}.tools.content.asset_probe:AssetProbe',
    'signal_profile_runner': f'{_RA}.tools.signal_profiles.signal_profile_runner:SignalProfileRunner',
    'http_probe': f'{_RA}.tools.http.http_probe:HttpProbe',
    'initial_response': f'{_RA}.tools.http.initial_response:InitialResponseParser',
    'runtime_observation': f'{_RA}.tools.http.runtime_observation:RuntimeObserver',
    # ---- measurement lenses (repo_analysis/measurement)
    'site_server': f'{_RA}.measurement.site_server:SiteServer',
    'html_summary': f'{_RA}.measurement.html_summary:HtmlSummarizer',
    'page_renderer': f'{_RA}.measurement.page_renderer:PageRenderer',
    'lighthouse_audit': f'{_RA}.measurement.lighthouse_audit:LighthouseAuditor',
    'link_checker': f'{_RA}.measurement.link_checker:LinkChecker',
    # ---- site model building blocks (repo_analysis/site_model)
    'url_manifest': f'{_RA}.site_model.url_manifest:UrlManifestBuilder',
    'source_lens': f'{_RA}.site_model.source_lens:SourceLens',
    'value_attribution': f'{_RA}.site_model.value_attribution:Attributor',
    'site_model_builder': f'{_RA}.site_model.site_model_builder:SiteModelBuilder',
    # ---- outputs
    'repo_context': f'{_RA}.repo_context.repo_context_builder:RepoContextBuilder',
    'evidence_bundle': f'{_RA}.evidence.evidence_bundle:EvidenceBundleBuilder',
    'exploration_writer': f'{_RA}.output.exploration_writer:ExplorationWriter',
    'issue_ranking': f'{_RA}.output.issue_ranking:IssueRanker',
    # ---- models (seo_agents/llm)
    'chat_model_factory': 'seo_agents.llm.chat_model_factory:ChatModelFactory',
    # ---- on-page stage (seo_agents/onpage)
    'exploration_reports': 'seo_agents.onpage.reports.exploration_reports:ExplorationReportsLoader',
    'page_context': 'seo_agents.onpage.page_context.page_context_builder:PageContextBuilder',
    'content_writer': 'seo_agents.onpage.writer.content_writer:ContentWriter',
    'change_operations': 'seo_agents.onpage.changes.change_operations:ChangeOperations',
    'content_validators': 'seo_agents.onpage.validation.content_validators:ContentValidators',
    'page_preview_renderer': 'seo_agents.onpage.changes.page_preview_renderer:PagePreviewRenderer',
    'site_level_builder': 'seo_agents.onpage.output.site_level_builder:SiteLevelBuilder',
    'onpage_output_writer': 'seo_agents.onpage.output.onpage_output_writer:OnpageOutputWriter',
    'pr_publisher': 'seo_agents.onpage.output.pr_publisher:OnpagePrPublisher',
    # ---- on-page content studio (seo_agents/onpage/content)
    'competitor_fetcher': 'seo_agents.onpage.content.competitor_fetcher:CompetitorFetcher',
    'serp_source': 'seo_agents.onpage.content.competitor_fetcher:SerpSource',
    'html_content_extractor': 'seo_agents.onpage.content.html_extractor:HtmlContentExtractor',
    'competitor_pattern_analyzer': 'seo_agents.onpage.content.competitor_analyzer:CompetitorPatternAnalyzer',
    'competitor_research': 'seo_agents.onpage.content.competitor_research:CompetitorResearch',
    'article_planner': 'seo_agents.onpage.content.article_planner:ArticlePlanner',
    'article_writer': 'seo_agents.onpage.content.article_writer:ArticleWriter',
    'article_rules': 'seo_agents.onpage.content.article_rules:ArticleRules',
    'article_fact_checker': 'seo_agents.onpage.content.article_fact_check:ArticleFactChecker',
    'article_renderer': 'seo_agents.onpage.content.article_renderer:ArticleRenderer',
    'article_studio': 'seo_agents.onpage.content.content_studio:ArticleStudio',
    # ---- keyword research stage (seo_agents/keyword_research)
    'keyword_page_selector': f'{_KR}.page_selection:PageSelector',
    'keyword_research_toolkit': f'{_KR}.providers.research_toolkit:ProviderResearchToolkit',
    'keyword_generator': f'{_KR}.generation.keyword_generator:LlmKeywordGenerator',
    'keyword_judge': f'{_KR}.judge.keyword_judge:JevKeywordJudge',
    'keyword_mapper': f'{_KR}.mapping.keyword_mapper:KeywordMapper',
    'keyword_output_writer': f'{_KR}.output.keyword_output_writer:KeywordOutputWriter',
}

AGENT_TOOLS = {
    # ---- repo analysis: deterministic agents, in execution order (repo_analysis/repo_analysis.py)
    'code_exploration': ('signal_profile_runner', 'project_profile'),
    'routes': ('route_discovery', 'signal_profile_runner'),
    'page_inventory': ('page_inventory', 'signal_profile_runner'),
    'keywords': ('keyword_extraction', 'signal_profile_runner'),
    'sitemap_robots': ('signal_profile_runner', 'safe_xml_parser'),
    'canonicals_redirects': ('signal_profile_runner',),
    'client_diagnostics': ('signal_profile_runner',),
    'rendering_strategy': ('signal_profile_runner',),
    'performance_web_vitals': ('signal_profile_runner',),
    'https_security_headers': ('signal_profile_runner',),
    'structured_data': ('signal_profile_runner',),
    'mobile_parity': ('signal_profile_runner',),
    'hreflang_i18n': ('signal_profile_runner',),
    'shareability_link_assets': ('signal_profile_runner',),
    'brand_entity_signals': ('signal_profile_runner',),
    'local_business_presence': ('signal_profile_runner',),
    'ai_search_readiness': ('signal_profile_runner',),

    # ---- repo analysis: whole-workflow steps
    'repo_analysis.repository_snapshot': ('repository_snapshot', 'source_index'),
    'repo_analysis.repo_context': ('repo_context',),
    'repo_analysis.site_model': ('site_model_builder',),
    'repo_analysis.legacy_llm_extractors': ('source_context', 'element_index'),

    # ---- site model workflow stages (repo_analysis/site_model/site_model_workflow.py)
    'site_model.acquire': ('repository_snapshot', 'framework_detection', 'element_index', 'route_discovery',
                'framework_route_adapters'),
    'site_model.build_serve': ('site_server', 'url_manifest', 'page_renderer'),
    'site_model.url_manifest': ('url_manifest',),
    'site_model.measure_page': ('page_renderer', 'url_manifest'),
    'site_model.discover': (),
    'site_model.attribute': ('url_manifest', 'source_lens', 'value_attribution', 'lighthouse_audit', 'link_checker'),
    'site_model.gap_worker': ('chat_model_factory',),
    'site_model.assemble': ('site_model_builder', 'exploration_writer'),

    # ---- gap workers (one model call per task, no tools: a self-contained packet in, JSON out)
    'gap_worker.attribute-value': ('chat_model_factory',),
    'gap_worker.explain-computed': ('chat_model_factory',),
    'gap_worker.topic-intent': ('chat_model_factory',),
    'gap_worker.eeat-classify': ('chat_model_factory',),
    'gap_worker.enumerate-instances': ('chat_model_factory',),
    'gap_worker.route-adapter': ('chat_model_factory',),

    # ---- legacy LLM category extractors (see LEGACY_EXTRACTOR_TOOLS for the LangChain tools)
    'legacy_extractor': ('chat_model_factory', 'repository_snapshot', 'source_context', 'element_index'),
    'legacy_extractor.evidence': ('evidence_bundle', 'runtime_observation'),

    # ---- on-page workflow (onpage/onpage_analysis.py), in execution order
    'onpage.load': ('exploration_reports', 'page_context'),
    'onpage.competitors': ('competitor_research', 'competitor_fetcher', 'serp_source', 'html_content_extractor',
                           'competitor_pattern_analyzer'),
    'onpage.intent': ('content_writer', 'content_validators'),
    'onpage.keyword_plan': ('content_validators',),
    'onpage.page_plan': ('content_writer', 'content_validators', 'change_operations'),
    'onpage.media_links': ('content_writer', 'content_validators', 'change_operations'),
    'onpage.advice': ('content_writer', 'change_operations'),
    'onpage.media_technical': ('change_operations',),
    'onpage.accessibility': ('change_operations',),
    'onpage.technical': ('change_operations',),
    'onpage.structured_data': ('change_operations',),
    'onpage.social_meta': ('change_operations',),
    'onpage.fact_check': ('content_validators', 'change_operations'),
    'onpage.head_hygiene': ('change_operations',),
    'onpage.heading_structure': ('change_operations',),
    'onpage.content_quality': ('change_operations',),
    'onpage.link_hygiene': ('change_operations',),
    'onpage.indexability': ('change_operations',),
    'onpage.hreflang_i18n': ('change_operations',),
    'onpage.security': ('change_operations',),
    'onpage.performance': ('change_operations',),
    'onpage.crawlability': ('change_operations',),
    'onpage.qa': ('change_operations', 'page_preview_renderer'),
    'onpage.site': ('site_level_builder',),
    'onpage.articles': ('article_studio', 'article_planner', 'competitor_research', 'article_writer', 'content_writer',
                        'article_rules', 'article_fact_checker', 'article_renderer'),
    'onpage.write': ('onpage_output_writer',),

    # ---- keyword research workflow (keyword_research/keyword_research.py), in execution order
    'keyword_research.load': ('keyword_page_selector', 'exploration_reports', 'page_context', 'keyword_research_toolkit',
                              'keyword_judge'),
    'keyword_research.business_profile': ('keyword_generator',),
    'keyword_research.competitor_discovery': ('keyword_research_toolkit',),
    'keyword_research.research_loop': ('keyword_research_toolkit', 'keyword_generator', 'keyword_judge'),
    'keyword_research.map': ('keyword_mapper', 'keyword_judge'),
    'keyword_research.write': ('keyword_output_writer',),
    # per-page loop subgraph run by research_loop: generate -> expand -> enrich -> judge -> (generate | finish)
    'keyword_research.page_loop.generate': ('keyword_generator',),
    'keyword_research.page_loop.expand': ('keyword_research_toolkit',),
    'keyword_research.page_loop.enrich': ('keyword_research_toolkit',),
    'keyword_research.page_loop.judge': ('keyword_judge',),
    'keyword_research.page_loop.finish': (),
}

# LangChain tools given to every legacy LLM category extractor, in the order the model sees them.
# Draft tools exist only when a draft buffer is passed; prior-report tools only when earlier reports exist.
LEGACY_EXTRACTOR_TOOLS = (
    'read', 'grep', 'symbols', 'find_definition', 'resolve_import', 'query_index', 'list_files', 'list_routes',
    'write_file', 'edit_file', 'test_script',            # draft extractor.py (only with a draft buffer)
    'read_source', 'read_sources', 'search_source', 'search_sources', 'query_context', 'read_context_record',
    'read_index_record',
    'query_prior_report', 'read_prior_report',           # only when prior reports are handed over
)
LEGACY_EXTRACTOR_DRAFT_TOOLS = ('write_file', 'edit_file', 'test_script')
LEGACY_EXTRACTOR_HANDOFF_TOOLS = ('query_prior_report', 'read_prior_report')


def tools_for(agent):
    """Tool names mapped to an agent / stage ('' tuple when it uses none)."""
    return AGENT_TOOLS.get(agent, ())


def tool_class(name):
    """Import and return the class registered for a tool name."""
    module, _, attribute = TOOL_CLASSES[name].partition(':')
    return getattr(import_module(module), attribute)
