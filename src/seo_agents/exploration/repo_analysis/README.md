# `repo_analysis`: everything used to explore a codebase

Entry point: `repo_analysis()` in [`pipeline.py`](pipeline.py). Every agent call is
listed in one function, `pipeline.explore_codebase()`, in run order.

```sh
python -m seo_agents.exploration.repo_analysis <checkout> -o repo_context.json
python -m seo_agents.exploration.repo_analysis <checkout> --with-llm-agents
```

```
repo_analysis/
├── __init__.py / __main__.py   exports + CLI
├── pipeline.py                 repo_analysis(), explore_codebase() (all agent calls), run_llm_agents()
├── context_builder.py          turns agent results into repo_context.json (refs + source code)
├── graph.py                    LangGraph runner for the 7 LLM agents (sequential, verified)
├── evidence.py                 combined evidence bundle over LLM reports + corpus
├── inspect.py                  read-only CLI: explore/export/query evidence (no PR)
├── json_analysis.py            review findings from saved LLM report JSON (no source/model)
│
├── agents/                     one file per agent → its skill
│   ├── code_exploration.py          skills/code-exploration
│   ├── sitemap_robots.py            skills/sitemap-robots
│   ├── canonicals_redirects.py      skills/canonicals-redirects
│   ├── rendering_strategy.py        skills/rendering-strategy
│   ├── react_diagnostics.py         skills/client-diagnostics (any JS framework)
│   ├── state_management.py          skills/state-management
│   ├── performance_assets.py        skills/performance-assets (Core Web Vitals)
│   ├── https_security_headers.py    skills/https-security-headers
│   ├── structured_data.py           skills/structured-data
│   ├── mobile_parity.py             skills/mobile-parity
│   ├── hreflang_i18n.py             skills/hreflang-i18n
│   ├── shareability_link_assets.py  skills/shareability-link-assets
│   ├── brand_entity_signals.py      skills/brand-entity-signals
│   ├── local_business_presence.py   skills/local-business-presence
│   ├── ai_search_readiness.py       skills/ai-search-readiness
│   ├── page_latency.py              opt-in HTTP baselines (--site-url, via graph.py)
│   ├── url_information_architecture.py, page_structure.py, internal_links.py,
│   │   media_alt_text.py, content_arrangement.py, metadata_social_cards.py,
│   │   eeat_trust_signals.py        the 7 LLM agents → agents/llm/skills/<category>
│   └── llm/                         LLM agent machinery
│       ├── extractor.py                 agent loop (LangChain), discovery budget, repair
│       ├── verification.py              validate + run the generated script twice
│       ├── script_policy.py / script_worker.py   sandboxed script execution
│       ├── handoff.py / references.py   cross-agent handoff and reference checks
│       └── skills/                      7 LLM SKILL.md + extraction-contract.md
│
├── tools/                      shared tools used by the agents
│   ├── repository_snapshot.py   bounded read-only source/asset snapshot (+ inventory)
│   ├── source_index.py          line-addressable index; evidence = ref + exact code + context
│   ├── skill_runner.py          loads skills/*/SKILL.md, applies signals, computes gaps
│   ├── project.py               manifests, dependencies, languages, entry points, hosting config
│   ├── routes.py                file-system + code-declared routes (skills/routes)
│   ├── page_inventory.py        tags, headings, text, front matter, links, media (skills/metadata, content)
│   ├── keywords.py              weighted n-gram keyword candidates (skills/keywords)
│   ├── source_context.py        tree-sitter/JSON/HTML index used by LLM agents + evidence
│   ├── agent_retrieval.py       list/read/search tools exposed to LLM agents
│   ├── runtime.py, initial_response.py, http_probe.py   opt-in HTTP fetch + initial HTML parsing
│   ├── asset_probe.py           local image dimensions
│   ├── llm.py                   chat model factory (OpenRouter)
│   ├── contracts.py, helpers.py report schema + shared constants
│   └── inventory.py, crawl.py, react_source.py   legacy static (offline) extractor
│
└── skills/                     19 framework-agnostic agent skills (see skills/README.md)
```

JSON layout (`seo_reports/repo_context.json`): `agents` (every result by agent name),
`pages` (per-route view), `edit_guide`, `gaps`, `files` (full content of referenced
files), `file_inventory`, `summary`.
