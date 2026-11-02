# `repo_analysis`: everything used to explore a codebase

Entry point: `repo_analysis()` in [`pipeline.py`](pipeline.py): one snapshot, then the
deterministic skill runners (`explore_codebase()`), the page-centric site model
(`site_model.explore_site()`, orchestrated by `site_graph.py`), and optionally the seven
legacy LLM category extractors (`graph.py`). Output contract: [`docs/site-model.md`](../../../../docs/site-model.md).

```sh
python -m seo_agents.exploration.repo_analysis <checkout>                        # repo_context.json + site_model/
python -m seo_agents.exploration.repo_analysis <checkout> --site-url https://x.y  # + raw/rendered/Lighthouse lenses
python -m seo_agents.exploration.repo_analysis <checkout> --render --build        # build+serve in a sandbox, render
python -m seo_agents.exploration.repo_analysis <checkout> --with-llm-agents       # + 7 legacy LLM reports
```

```
repo_analysis/
├── __init__.py / __main__.py   exports + CLI
├── pipeline.py                 repo_analysis(): snapshot once -> skills -> site model -> optional LLM categories
├── site_model.py               explore_site(), page assembly, validators (schema/grounding/lens), site facts, writers
├── site_graph.py               LangGraph orchestrator: acquire -> build_serve -> url_manifest -> measure_page (Send)
│                               -> discover (crawl waves) -> attribute -> gap_worker (Send) -> assemble
├── url_manifest.py             adapters ∪ static params ∪ content collections ∪ sitemaps ∪ build output ∪ crawl
├── source_lens.py              per-page values + edit locations from source (props, blocks, i18n, front matter, metadata APIs)
├── attribution.py              served value -> file:line:col / data pointer / i18n key; ambiguity -> gap queue
├── gap_workers.py              cheap-model task packets, validation, retry, tier escalation, votes, learned adapters, cache
├── context_builder.py          deterministic results -> repo_context.json (references + hashes)
├── graph.py                    7 legacy LLM category extractors as a DAG (urls -> 4 parallel -> content -> eeat)
├── evidence.py                 combined evidence bundle over LLM reports + corpus
├── inspect.py                  read-only CLI: explore/export/query evidence (no PR)
├── json_analysis.py            review findings from saved LLM report JSON (no source/model)
│
├── runtime/                    measurement lenses (no LLM)
│   ├── build_serve.py              site_url | pre-built HTML (+SPA fallback) | install+build+serve | source-only
│   ├── dom.py                      one page-summary shape for raw HTML / built files / rendered DOM
│   ├── render.py                   raw fetch + Playwright desktop/mobile render, mobile parity, JS dependence
│   ├── lighthouse.py               Lighthouse CLI (median of N) or PageSpeed Insights (+CrUX field data)
│   └── link_check.py               internal + sampled external link status with redirect chains
│
├── agents/
│   ├── gap/skills/                 gap-worker skills: attribute-value, explain-computed, topic-intent,
│   │                               eeat-classify, enumerate-instances, route-adapter
│   ├── llm/                        legacy LLM extractor harness (extractor, verification w/ grounding,
│   │                               script policy/worker, handoff, references, skills/contract-common.md + 7 skills)
│   └── *.py                        deterministic skill runners (sitemap_robots, canonicals_redirects, rendering_strategy,
│                                   react_diagnostics, state_management, performance_assets, https_security_headers,
│                                   structured_data, mobile_parity, hreflang_i18n, shareability_link_assets,
│                                   brand_entity_signals, local_business_presence, ai_search_readiness, page_latency)
│
├── tools/
│   ├── repository_snapshot.py      bounded read-only snapshot, content-hash identity (no mtimes)
│   ├── framework.py                framework fingerprint (deps, config, layout) for 35+ stacks
│   ├── element_index.py            unified element index, data/i18n index, composition refs, alias resolution
│   ├── adapters.py                 framework route adapters (URL pattern -> entry template)
│   ├── source_index.py             line-addressable index used by the skill runners
│   ├── skill_runner.py             loads profiles/*.json (validated), scoped signals, truncation flags
│   ├── routes.py                   generic route skill (fallback for unknown stacks)
│   ├── project.py, page_inventory.py, keywords.py   project profile, tag inventory, keyword candidates
│   ├── source_context.py           tree-sitter import/JSON/HTML records for the legacy LLM agents
│   ├── agent_retrieval.py          legacy agent tools: read, grep, symbols, find_definition, resolve_import, query_index, drafts
│   ├── runtime.py, http_probe.py, initial_response.py   isolated stdlib HTTP probe + initial-HTML parsing
│   ├── asset_probe.py, safe_xml.py local image dimensions; defusedxml-backed XML parsing
│   ├── llm.py                      model tiers (small/mid), per-call usage/cost logging, JSONL trace (SEO_TRACE)
│   ├── contracts.py, helpers.py    legacy report schema + shared constants
│   └── inventory.py, react_source.py   DEPRECATED React/Next static extractor (legacy offline graph tests only)
│
├── profiles/                   19 deterministic signal profiles (+ schema.json); examples run as unit tests
└── skills/                     human documentation for each profile
```
