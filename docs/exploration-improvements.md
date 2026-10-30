# Exploration review and implementation handoff

## Current-state map and baseline

The existing product is a Python source-analysis pipeline for developers/SEO
workflows working on website checkouts. Its seven category reports are observations
for downstream review, not remediation instructions or verified ranking failures.

| Area | Inspected implementation and finding |
| --- | --- |
| Inputs | [main.py](../src/seo_agents/main.py) uses a fixed GitHub URL and the Docker clone/analyze/PR flow. [graph.py](../src/seo_agents/exploration/checks/graph.py) accepts a local checkout. No deployed URL or browser was originally passed into extraction. |
| Agent harness | [extractor.py](../src/seo_agents/exploration/checks/agents/extractor.py): seven fresh LangChain agents, packaged category skills, URL agent first, generated Python scripts, three verification attempts. |
| Execution | [verification.py](../src/seo_agents/exploration/checks/agents/verification.py) and [script_worker.py](../src/seo_agents/exploration/checks/agents/script_worker.py): policy-checked source, restricted capabilities, bounded workers, repeatability check. |
| Discovery | [repository.py](../src/seo_agents/exploration/checks/agents/repository.py) originally stopped scanning at capture caps, omitted all hidden configuration, and sent all captured paths/assets to each agent. File listing did not establish semantic coverage. |
| Existing parsers | [react_source.py](../src/seo_agents/exploration/checks/tools/react_source.py) has tree-sitter React composition; [inventory.py](../src/seo_agents/exploration/checks/tools/inventory.py) handles supported React/Next/static HTML. Other frameworks rely on generated adapters, not established exhaustive semantic support. |
| Outputs | [contracts.py](../src/seo_agents/exploration/checks/tools/contracts.py), [json_analysis.py](../src/seo_agents/exploration/checks/json_analysis.py): seven schema-2.0 inventories and a JSON-only review analyzer. [pr.py](../src/seo_agents/sandbox/pr.py) is a separate publishing path. |
| Runtime/performance | `page_latency.py` and `performance_assets.py` were placeholders; browser, Lighthouse, CMS credentials and field-data integrations did not exist. This was an unsupported capability, not a broken measurement implementation. |
| Reuse/failure | Completed work lived only in final graph state; a failed invocation did not expose a resumable API checkpoint. Static mode independently reconstructed the same pages seven times. |

The baseline test suite contained 46 passing tests, primarily static extraction,
worker boundaries, memory-only publication and a local model double. It did not
prove live-model extraction quality across arbitrary frameworks.

Final local validation: 73 tests passed, including the new corpus/coverage,
runtime boundary, byte-budget/recovery and checkpoint cases. The offline benchmark
also passed report/source equality checks. HTTP behavior was tested with controlled
response doubles; no paid-provider, deployed-site, browser or CMS run was performed.

### Follow-up review of the existing working tree

The checkout already contained the corpus/coverage/retrieval/checkpoint changes
and 67 passing tests when this review began. Those changes were preserved. This
increment added six ground-truth tests and two derived capabilities:

- Reverse source attribution: `source_relationships` maps a captured file to
  directly observed pages and candidate dependency pages separately. Unassociated
  files and report references outside the capture remain visible. This supports
  downstream investigation of shared content without claiming dependency imports
  prove runtime use or that unassociated files are unused.
- Richer literal HTML and response reconciliation: headings, navigation-link
  occurrences, media attributes, text boundaries, raw JSON-LD and title declarations
  share one parser. Runtime link resolution uses the final URL and first base href;
  source-versus-response title comparisons retain both evidence locations and
  reasons when comparison is unavailable. Partial bodies and HTTP failures retain
  their evidence without becoming complete observations.

These are additive views over the existing reports, source snapshot and responses;
they introduce no dependency, service, model request or HTTP request. Response link
discovery stays separate from scheduling: newly observed internal destinations are
listed explicitly as unscheduled. Source and runtime link inventories remain
separate rather than implying source-only links were observed in a response.

The static legacy extractor still reads the filesystem rather than the bounded
snapshot. Its reports can reference content outside the corpus; the new attribution
coverage exposes those references. Strict snapshot-only static extraction would
require adapting its filesystem-based composition layer and remains deferred.

## Harness lessons applied

[Codex's prompting guide](https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide)
supports batching known independent reads and bounding results.
[OpenCode's tools](https://opencode.ai/docs/tools/) distinguish path discovery,
content search, targeted reads and optional code intelligence.
[Pi's session documentation](https://github.com/earendil-works/pi/tree/main/packages/coding-agent)
distinguishes compacted context from retained full history. These are retrieval and
state-management patterns; exposing search alone does not provide whole-repository
understanding. No harness architecture, service, database or extra agent layer was
copied into this MVP. The user's independent category agents remain intact.

## Decisions against the advisory menu

| Suggestion | Adopted/adapted/deferred and reason |
| --- | --- |
| 1. Investigate first | Mapped existing entry points, parsers, contracts, runtime gaps and baseline tests. Kept current LangChain/LangGraph and publishing boundary. |
| 2. Harness principles | Added role/prefix narrowing, batched retrieval, AST/data context and recoverable byte limits. Model strategy is in the skill; retrieval is deterministic. |
| 3. Measurable coverage | Inventory continues after capture caps; records statuses/reasons and excluded subtrees with unknown descendant counts. URL families, concrete instances, extraction access, initial HTTP, rendering and performance have separate states. |
| 4. Repository/runtime adaptation | Retained selected hidden framework config and Next generated manifests; indexed package roots, literal imports, local data, sitemaps/robots. Optional HTTP evidence stays separate. Browser rendering, CMS access, full monorepo deployment inference and remote sitemap expansion remain unsupported. |
| 5. SEO evidence/provenance | Bundle preserves seven full reports, captured source, JSON pointers, literal HTML and raw JSON-LD/errors. Added exact non-chrome content fingerprints and observed Unicode term counts, separate from declared meta-keywords and ungenerated target suggestions. No market metrics or indexing claims. |
| 6. Site graph | Added occurrence references, unique destinations, incoming-link candidates and source/dependency relationships as derived JSON views. No graph database. Zero observed incoming links remains inventory-relative. |
| 7. Every eligible page speed coverage | Added one serial HTTP baseline attempt per enumerated concrete URL within an explicit budget, with pending/failure outcomes. It cannot substitute for browser/device profiles, Lighthouse or field metrics; these remain pending capabilities. |
| 8. Reduce redundant work | Shared deterministic context, batched tools, compact initial context, within-worker HTML cache, cached line counts, single static composition pass. Kept two worker runs per accepted generated script for verification. |
| 9. Complete artifacts/compact responses | New in-memory/exportable evidence bundle contains complete captured source and reports. JSON-pointer queries return bounded previews/chunks with recovery. No automatic client artifacts are written into this repository. |
| 10. Small reusable scripts/adapters | Reused tree-sitter, HTMLParser, existing static parsers and worker contract. Kept repository-specific generated adapters for unusual frameworks instead of adding another generation framework. |
| 11. Incremental reliability | Added explicit checkpoint/resume API and CLI resume input. Whole-capture/code/skill/dependency/model identity invalidation is deliberately conservative. HTTP data is refreshed, not cached indefinitely. No dependency-aware partial invalidation or automatic durable database. |
| 12. Uncertainty | Observed source, dependency candidates, source declarations, initial responses and unresolved runtime work are distinct. Static access is not understanding; missing timing/token values stay null. |
| 13. Representative validation | Added coverage caps, UTF-8 boundaries, hidden configs, AST dependencies, JSON pointers, malformed JSON-LD, long-line recovery, dynamic templates/instances, URL scope, runtime failures, resume/invalidation and identical-output efficiency checks. |
| 14. Delivery evidence | README documents run/query/resume, contracts, limits and runtime scope. Reproducible offline benchmark and unit tests provide bounded evidence; production-model/browser claims are intentionally absent. |

## Coverage and evidence locations

`run_onpage_graph(...)["evidence"]` and the read-only CLI expose:

- `repository.inventory` and `repository.coverage`: denominator, captures,
  truncation, pending/unreadable files and explicit subtree exclusions.
- `source_corpus` and `asset_inventory`: captured source and measured asset data.
- `source_relationships` and `coverage.source_attribution`: reverse source-to-page
  references, candidate dependencies, unassociated files and out-of-capture evidence.
- `context.records`/`file_coverage`/`parse_errors`: cached source facts, JSON
  pointers, imports/data calls, raw declarations and unsupported/partial parsing.
- `reports`: the original seven inventories, once each. `pages[].report_refs`
  links derived views to the full text/metadata/media records.
- `pages`, `link_occurrences`, `destinations`, `concrete_urls`,
  `unresolved_route_families`: source identities, lineage candidates, queries,
  fragments, graph relationships and unknown instance denominators.
- `runtime.ledger`/`responses` and `coverage.performance`: known-frontier attempt
  states, selected headers, initial HTML, truncation and measured HTTP timings.
- `runtime.link_occurrences`/`destinations`/`source_comparisons` and
  `coverage.initial_response_extraction`: independent response observations,
  declaration-set differences and unscheduled internal destinations.
- `efficiency` and `checkpoint`: measured operation counters and reusable
  completed categories. Provider token usage is null when not reported.

Source facts do not establish initial-response or rendered behavior, consistent
with [Google's JavaScript SEO documentation](https://developers.google.com/search/docs/crawling-indexing/javascript/javascript-seo-basics).
The HTTP baseline does not measure Web Vitals or field experience; see
[Web Vitals](https://web.dev/articles/vitals). Existing meta-keywords are retained
as evidence, with no ranking requirement; see [Google's supported meta tags](https://developers.google.com/search/docs/crawling-indexing/special-tags).

## Quality/efficiency comparison

Run `PYTHONPATH=src python scripts/benchmark_exploration.py`. One local run used
20 synthetic HTML pages, five repeats, no model calls and no network requests:

| Measurement | Before-style operations | Current operations |
| --- | --- | --- |
| Seven report payloads | Baseline | Identical |
| Page-composition passes | 7 | 1 |
| Known eight-file read round trips | 8 | 1, identical source text |
| Initial context for a 2,000-path manifest | 172,051 bytes | 831 bytes |
| Median wall time | 22.238 ms | 15.892 ms |
| Observed range | 21.595–24.160 ms | 15.556–19.217 ms |

This is a synthetic operation comparison, not a paid-model benchmark or a promise
for arbitrary repositories. After timing includes inventory/context/evidence and
checkpoint work that the before-style seven-report loop did not do. Timing varies;
the report equality and operation counts are the stronger evidence. Generated
agent extraction still requires seven independent conversations and two accepted
script executions per category. No token/dollar saving is claimed without a real
provider run and comparable coverage.

## Remaining limits and next improvements

Capture budgets bound the corpus; unavailable CMS records and unknown dynamic
instances remain unknown. Generic context extraction does not implement every
framework's template semantics. Keyword extraction is lexical, not language-aware
topic/intent analysis. Dependency edges resolve literal relative imports; aliases,
conditional exports and cross-package data flow need further interpretation.

HTTP checks cover the known source/link/local-sitemap frontier within one explicit
origin and budget, not a recursive crawl. External health checks and browser-only
links remain unchecked. Single-run HTTP timings include network/TLS and are not
LCP/CLS/FCP/TBT/INP, user experience, or an inferred Lighthouse score. Runtime build
identity, auth states, locale domains and browser readiness require future inputs.

Source checkpoints can be retained through `on_checkpoint` or a handled failure's
checkpoint. The CLI exports partial results on handled failures; it does not write
automatic durable checkpoints or survive an unhandled hard kill.

The next highest-value step is ground-truth calibration on actual frontend
framework fixtures with a live model, followed by an explicitly configured browser
runner and production-like runtime. Any CMS integration should follow a concrete
repository's missing dependency rather than adding an unrelated service now.
