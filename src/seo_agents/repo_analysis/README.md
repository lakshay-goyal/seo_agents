# `repo_analysis`: everything used to explore a codebase

Entry point: `RepoAnalysisWorkflow` / `repo_analysis()` in [`repo_analysis.py`](repo_analysis.py). It is one
sequential LangGraph: snapshot once, run the 17 deterministic agents in order, write `repo_context.json`, build the
page-centric site model (its own LangGraph in [`site_model/site_model_workflow.py`](site_model/site_model_workflow.py)),
and optionally run the seven legacy LLM category extractors. Output contract:
[`docs/site-model.md`](../../../docs/site-model.md).

```sh
python -m seo_agents.repo_analysis <checkout>                        # repo_context.json + site_model/
python -m seo_agents.repo_analysis <checkout> --site-url https://x.y  # + raw/rendered/Lighthouse lenses
python -m seo_agents.repo_analysis <checkout> --render --build        # build+serve in a sandbox, render
python -m seo_agents.repo_analysis <checkout> --with-llm-agents       # + 7 legacy LLM reports
```

Model per agent: [`lib/models.py`](../lib/models.py). Tools per agent: [`lib/tool_mapping.py`](../lib/tool_mapping.py).
Prompts: [`lib/prompts/`](../lib/prompts). Signal profiles: [`lib/signal_profiles/`](../lib/signal_profiles).

```
repo_analysis/
├── repo_analysis.py            RepoAnalysisWorkflow (LangGraph): capture_snapshot -> 17 agents -> write_repo_context
│                               -> build_site_model -> run_legacy_llm_extractors -> finish
├── cli.py / __main__.py        `python -m seo_agents.repo_analysis`
│
├── agents/                     one class per agent; base_agent.RepoAnalysisAgent (name, signal_profile, requires, run)
│   ├── codebase/                   CodeExplorationAgent, RouteDiscoveryAgent, PageInventoryAgent, KeywordExtractionAgent
│   ├── crawling_indexing/          SitemapRobotsAgent, CanonicalsRedirectsAgent, HreflangI18nAgent
│   ├── rendering_performance/      ClientDiagnosticsAgent, RenderingStrategyAgent, PerformanceAssetsAgent, MobileParityAgent
│   ├── security/                   HttpsSecurityHeadersAgent
│   ├── entity_signals/             StructuredDataAgent, ShareabilityLinkAssetsAgent, BrandEntitySignalsAgent,
│   │                               LocalBusinessPresenceAgent, AiSearchReadinessAgent
│   ├── gap_workers/                GapWorkerAgent (cheap-model tasks): task builder, prompts, solver, answer validation,
│   │                               result applier, route-adapter learner
│   └── legacy_llm_extractors/      DEPRECATED, opt-in: LegacyExtractionWorkflow (LangGraph DAG) + CategoryExtractorAgent,
│                                   observation verification, sandboxed script policy/worker, prior-report handoff,
│                                   cross-report references, LangChain retrieval tools, static_mode/ (offline tests)
│
├── tools/                      one class per tool (no model)
│   ├── repository/                 RepositorySnapshotTool, SourceIndex, FrameworkDetector, ProjectProfileTool, constants
│   ├── parsing/                    ElementIndexBuilder, SourceContextBuilder, SafeXmlParser
│   ├── routing/                    FrameworkRouteAdapters, HeadRecipeTool, RouteDiscoveryTool
│   ├── content/                    PageInventoryTool, KeywordExtractionTool, AssetProbe
│   ├── signal_profiles/            SignalProfileRunner (applies lib/signal_profiles/profiles/<name>.json)
│   └── http/                       HttpProbe (isolated worker), InitialResponseParser, RuntimeObserver
│
├── measurement/                runtime lenses (no model)
│   ├── site_server.py              SiteServer: site_url | pre-built HTML (+SPA fallback) | install+build+serve | source-only
│   ├── html_summary.py             HtmlSummarizer: one page-summary shape for raw HTML / built files / rendered DOM
│   ├── page_renderer.py            PageRenderer: raw fetch + Playwright desktop/mobile render
│   ├── lighthouse_audit.py         LighthouseAuditor: Lighthouse CLI (median of N) or PageSpeed Insights (+CrUX)
│   └── link_checker.py             LinkChecker: internal + sampled external link status with redirect chains
│
├── site_model/                 page-centric site model
│   ├── site_model_workflow.py      SiteModelWorkflow: AcquireStage -> BuildServeStage -> UrlManifestStage ->
│   │                               MeasurePageStage (Send) <-> CrawlDiscoveryStage -> AttributionStage ->
│   │                               GapWorkerStage (Send) -> AssembleStage
│   ├── site_model_builder.py       SiteModelBuilder / explore_site(): config, page assembly, validators, site facts, writers
│   ├── url_manifest.py             UrlManifestBuilder: adapters ∪ static params ∪ content ∪ sitemaps ∪ build output ∪ crawl
│   ├── source_lens.py              SourceLens: per-page values + edit locations from source
│   └── value_attribution.py        Attributor: served value -> file:line:col / data pointer / i18n key; ambiguity -> gap
│
├── repo_context/               RepoContextBuilder -> seo_reports/repo_context.json (references + hashes)
├── evidence/                   EvidenceBundleBuilder (legacy reports + corpus) and the read-only inspect CLI
└── output/                     ExplorationWriter (sharded seo_reports/exploration/), IssueRanker, JSON schema validation
```
