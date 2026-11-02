# SEO Agents

Explores any website repository — Next/Nuxt/SvelteKit/Astro/Remix/Gatsby, React/Vue/
Svelte/Angular SPAs, Shopify themes, WordPress themes, Hugo/Jekyll/Eleventy, Django,
Flask, Laravel, Rails, Express, ASP.NET Razor, Ghost or plain HTML — and produces one
edit-ready JSON model of the site for an SEO execution agent. Every value carries where
it came from and exactly where to change it. A reports PR is opened through the sandbox.

Design: [`docs/seo-agents-analysis.html`](docs/seo-agents-analysis.html). Output contract:
[`docs/site-model.md`](docs/site-model.md).

## Install and run

```sh
uv sync                                   # or: pip install -e .
uv pip install -e '.[render]'             # optional: Playwright for rendered-DOM lenses
seo-agents https://github.com/<owner>/<repo> [--site-url https://example.com] [--render] [--build] [--llm-agents]

# Local checkout, no clone/PR:
python -m seo_agents.exploration.repo_analysis /path/to/site [--site-url URL] [--render] [--build]
```

Requires Docker and `GITHUB_TOKEN` (classic PAT, `repo` scope) for the PR flow. Only
plain `https://github.com/<owner>/<repo>` URLs are accepted; every shell argument in the
clone/PR steps is quoted. Container dependencies are read from `pyproject.toml`.
`--render`/`--build` (or `SEO_RENDER=1`/`SEO_BUILD=1`) provision node, Playwright
Chromium and Lighthouse in the sandbox on a best-effort basis; failures only reduce the
available lenses, and the reason is recorded. See `.env.example` for every setting.

The PR publishes `seo_reports/site_model/` (index.json, site.json, pages/*.json) and
`seo_reports/repo_context.json`; with `--llm-agents`, also the seven legacy
`seo_reports/onpage/*.json` reports.

## How exploration works

Deterministic code produces the facts; cheap models resolve only what code cannot.

| Stage | What happens | Module |
| --- | --- | --- |
| A acquire | one snapshot (content-hash identity) → framework fingerprint → unified element index (JSX/TSX via tree-sitter; Vue/Svelte/Astro; HTML and every server template language with directives masked offset-for-offset; Markdown; JSON/YAML/TOML/PO data and i18n catalogs; composition edges incl. Liquid sections/snippets, `get_template_part`, `{% extends %}`, `@include`, partials, imports with tsconfig/Vite aliases) | `tools/framework.py`, `tools/element_index.py` |
| B build/serve | `site_url`, else pre-built static HTML (SPA fallback when the host rewrites to index.html), else opt-in install+build+serve, else source-only | `runtime/build_serve.py` |
| C URL manifest | framework adapters (file routes, router configs incl. nested children, Shopify JSON templates, WordPress template hierarchy, Django/Laravel/Rails/Flask/Express routes) ∪ literal static params ∪ content collections ∪ sitemaps ∪ build output ∪ crawl, with provenance | `tools/adapters.py`, `url_manifest.py` |
| D measure | per URL in parallel (LangGraph `Send`): raw HTML + headers/redirects, rendered DOM desktop+mobile (Playwright), mobile parity, Lighthouse/PSI (mobile+desktop, median of N), link status, served-behaviour probes (robots, soft 404, trailing slash, http→https, security headers); BFS crawl gives click depth | `runtime/*`, `site_graph.py` |
| E attribute | every served value → file:line:col + edit kind: lens agreement, exact literal, data/i18n match, composed data+template match; ties → gap queue | `source_lens.py`, `attribution.py` |
| F gap workers | small-model task packets with candidate IDs + enums; validator → fresh retry → mid-tier escalation; 3-vote self-consistency for topic/intent and E-E-A-T; learned declarative adapters for unknown stacks, cached | `gap_workers.py`, `agents/gap/skills/` |
| G assemble | index.json (planning), site.json (site facts, duplicates via SimHash, hreflang reciprocity, orphans, crawl errors, coverage ledger, model cost), pages/<id>.json; validators: schema, grounding, lens agreement | `site_model.py` |

Accuracy is measured, not assumed: `scripts/score_fixtures.py` scores 13 golden fixture
repos (one per major stack, `tests/fixtures/sites/`) for URL recall/precision, head-field
accuracy, heading/link/image precision+recall and edit-attribution accuracy against the
doc targets (≥98% URL recall, ≥97% head fields, ≥90% attribution).

```sh
PYTHONPATH=src .venv/bin/python scripts/score_fixtures.py --misses
```

## Repository context (`repo_context.json`)

The deterministic skill runners (`pipeline.explore_codebase()`, profiles in
[`repo_analysis/profiles/`](src/seo_agents/exploration/repo_analysis/profiles)) still write
`seo_reports/repo_context.json`: per-concern signals (sitemap/robots, canonicals/redirects,
rendering, client diagnostics, state, Core Web Vitals causes, security headers,
structured data, mobile, i18n, shareability, brand/entity, local business, AI-search
readiness) with precision labels, truncation flags and file references + content hashes
(no file copies by default). Its summaries also feed `site.json`.

## Legacy LLM category extractors (opt-in: `--llm-agents` / `SEO_RUN_LLM_AGENTS=1`)

Each category has its own LangChain agent and packaged
[`SKILL.md`](src/seo_agents/exploration/repo_analysis/agents/llm/skills): URLs, content, page
structure, internal links, metadata, media and E-E-A-T. The URL agent runs first;
its verified route/template catalog is shared with the other agents so all reports
cover the same pages. Each remaining agent has a fresh conversation, its own domain
skill and read-only listing, source-reading and search tools. Relevant earlier
reports, discovery notes and inspected paths are available on demand through
`query_prior_report` and `read_prior_report`. For example, content can look up the
link/media source IDs already extracted, metadata can inspect shared layouts and
headings, and E-E-A-T can consult copy and contact/policy links. Earlier notes are
leads to verify against source; using them is optional. No prior conversation or
extraction code is shared, and full reports are not inserted into prompts.

Agents run sequentially: URLs → page structure → internal links → media →
content → metadata → E-E-A-T. The terminal streams Docker output as it happens.
Each agent logs model rounds, completed source reads/searches, verification retries,
and a per-page report summary before the next agent starts. Long model calls emit
a waiting update every 15 seconds. Logs show task metadata and observation counts;
report payloads remain in memory until publication. Cross-report validation and
evidence assembly have their own progress messages. Failed runs keep the output
already streamed instead of printing the entire transcript again.

Content link/media references are checked against the earlier reports inside the
content agent's verification/repair loop, before metadata or E-E-A-T starts. Repair
feedback includes the failing JSON pointer, page and target report. After discovery
ends, a repair may spend up to two additional model rounds querying prior reports
and routes; broad source discovery stays closed. Unsupported references remain
explicit coverage gaps while the observed content is retained. The final cross-report
validation still checks all reports, including resumed results.

The workflow is:

1. Inventory the declared repository scope and capture a bounded in-memory
   source/asset snapshot. Record omissions and excluded subtrees; retain selected
   hidden framework configuration and generated Next.js route manifests.
2. Each agent reads configuration, entry points, templates, components and local
   data, then returns framework notes and a repository-specific Python script.
3. Check the script's Python syntax and allowed capabilities before execution.
4. Trial it in a separate worker with no credentials, imports, filesystem/network
   APIs or application execution. Enforce time, memory and output limits, then
   validate report fields, source locations, coverage and word counts.
5. Run the verified script again against the same snapshot; require identical
   output. Failed verification is returned to the agent for up to three attempts.
6. Validate route agreement and content-to-link/media references across reports,
   then send the seven in-memory reports to the existing JSON publisher.

Scripts, exploration notes, inspected paths and verification attempts are available
in graph state under `agent_runs`; JSON includes script/skill/snapshot hashes in
`extraction`. Client source, scripts and notes are not saved in this repository.
Rejected content observations identify the field's JSON pointer, actual type and
source location, with a repair hint. Every rejection is logged. Identical rejected
scripts reuse their validation error instead of being executed again. After three
failed attempts, `ExplorationIncomplete.checkpoint["agent_runs"][category]` retains
the last rejected script and repair errors; failed reports are never published.
Responses without the required `ExtractionPlan` are also returned for repair in
the existing conversation. Their finish reason and response type are retained in
run metrics; the script is null if the model never returned one.
Source relationships in `content_sources` use captured files and optional selectors
or JSON pointers. `pointer_validation` distinguishes resolved JSON pointers from
invalid pointers and unverified non-JSON selectors; unresolved relationships remain
coverage limitations. Observed content blocks still require valid file/line/source
IDs. Policy feedback lists multiple violations together, including source lines,
and nested pure helpers are checked and executed using the same capability rules.
Source capture is capped at 2,000 loaded files, 500 KB per file and 20 MB total;
inventory continues after capture limits. Omissions/truncation and unreadable files
are reported individually. A file listed or accessed is not a file understood.
Source-backed extraction
can adapt to different frameworks, but verification does not prove semantic
completeness or rendered-browser equivalence. Runtime API/CMS data and unsupported
framework semantics must remain explicit gaps. Linux workers enforce a 512 MiB
address-space limit; macOS local workers periodically check resident memory.

Set `OPENROUTER_API_KEY` and a tool-calling `OPENROUTER_MODEL` in `.env`. Agent
mode is the default (`SEO_EXPLORATION_MODE=agent`) and fails explicitly if the model
is disabled or extraction cannot be verified. To use the previous deterministic
React/Next.js extractor offline, set `SEO_EXPLORATION_MODE=static` (or pass
`mode="static"` to `run_onpage_graph`). `SEO_NO_LLM=1` alone does not select static
mode. No model key is required for static mode or JSON-only analysis.

Model responses use a combined reasoning/output cap of 32,768 tokens by default
(`OPENROUTER_MAX_TOKENS`). The default Flash model disables reasoning to
leave room for the structured extractor; other models retain their provider's
reasoning default. Override with `OPENROUTER_REASONING_EFFORT` (`low`, `medium`,
another supported effort, or `default` to omit the override). These values are
forwarded into Docker and included in resume identity. A `length` finish reason
is reported with token usage when available; it does not become a valid report.
`SEO_MAX_DISCOVERY_ROUNDS` defaults to 12 model rounds with repository tools per
category. After that, the final structured-response tool remains available and
repository tools are removed. Budget exhaustion is recorded in run metrics,
report extraction metadata and page coverage limitations; it is not complete
semantic exploration. The generated script still receives the full captured
source snapshot. This setting is configurable (1–100) and invalidates resume reuse.

## Corpus evidence and read-only exploration

`run_onpage_graph` also returns `state["evidence"]`, a separate version 1.0 bundle.
It contains the seven reports, complete **captured** textual source corpus, asset
inventory, file roles/capture states, cached AST import/data-source candidates,
local JSON values with JSON pointers, Markdown references, literal HTML evidence,
raw JSON-LD and syntax errors, source sitemaps/robots, page/source relationships,
link occurrences and destination inventory, exact content fingerprints, and lexical
term counts separated from declared keywords. These are source observations and
qualified derived views. There are no invented search-volume or ranking metrics.
Generic index candidates are not automatically treated as public page content.

`source_relationships` provides a reverse lookup by relative source path: directly
observed page IDs, pages connected only by candidate import dependencies, capture
status, candidate file roles, and a JSON pointer to the captured source. Files
without a page association remain explicit; this does not prove they are unused.
`coverage.source_attribution` counts these states and identifies report-referenced
files outside the captured corpus. This is especially relevant in legacy static
mode, whose filesystem extractor can inspect files beyond the snapshot budgets.

The literal HTML index also preserves headings, navigation-link occurrences and
media attributes, including anchor text, image alternatives and semantic sections.
Canonical/hreflang annotations remain distinct from navigational links. Agents can
query these facts without reparsing a file for each field.

The bundle retains full captured source so unsupported or omitted semantic
extraction has a recovery path. It cannot contain unavailable CMS/API records,
unreadable files or content beyond declared capture limits. Those are explicit
gaps. It does not claim every possible URL or every rendered state was discovered.

Agents receive a compact summary, then use role-filtered inventories, batched
reads/searches and paginated context queries. Tool responses are limited to 16 KB
with recovery cursors, including within long/minified lines. Per-agent metrics
record tool calls, returned bytes, repeated reads, model response counts and
provider token usage when available; unavailable token counts remain null.
Static mode composes/parses pages once for all seven reports.

Explore without cloning, committing or publishing; JSON goes to stdout and logs
to stderr. Choose an artifact location outside this agent checkout:

```sh
python -m seo_agents.exploration.repo_analysis.inspect /path/to/site --mode agent > /tmp/exploration.json
# Offline legacy extraction plus the same corpus/evidence views:
python -m seo_agents.exploration.repo_analysis.inspect /path/to/site --mode static > /tmp/exploration.json
# Query the saved evidence without source, models or network access:
python -m seo_agents.exploration.repo_analysis.inspect /tmp/exploration.json --query /pages
python -m seo_agents.exploration.repo_analysis.inspect /tmp/exploration.json --query /reports/content/pages/0
# Recover source/page relationships for src/layout.tsx (JSON-pointer escaping):
python -m seo_agents.exploration.repo_analysis.inspect /tmp/exploration.json --query /source_relationships/src~1layout.tsx
```

`query_evidence(bundle, pointer, offset=...)` supports JSON-pointer queries. List
results contain previews and item pointers. Larger values return JSON text chunks
with character offsets. Full content remains in the bundle; previews are not the
artifact. Source paths in pointers escape `/` as `~1` and `~` as `~0`.

## Runtime observations and speed coverage

Without a runtime URL, every enumerated eligible concrete URL has a pending HTTP
baseline record. Dynamic families remain unresolved with an unknown instance
denominator. Concrete dynamic instances may use optional `route_family` and
`instance_status="concrete"` page context; unknown instances are never invented.
Optional page fields also include `locale` and `content_sources`.

An explicit runtime URL enables serial initial-response observations:

```sh
python -m seo_agents.exploration.repo_analysis.inspect /path/to/site --mode static \
  --site-url https://example.com --max-runtime-pages 100 --runtime-timeout 12 \
  > /tmp/exploration.json
```

Routes should already contain their public base paths. The configured origin
defines internal scope; domain substrings are not used. Meaningful query variants,
path case and raw link occurrences are preserved. Fragments share a fetch, and
source `<base>` declarations are considered for source-link resolution. Local
sitemap URLs join the frontier when they match the configured origin. External
destinations and remote child sitemaps are not crawled; no recursive crawl is added.

Each eligible concrete URL is attempted once within the request budget or remains
pending. Results distinguish success, timeout, failure, access blocks, non-HTML,
HTTP errors and redirect outcomes. Fresh isolated workers enforce a per-URL wall
timeout and do not inherit credentials/cookies. Responses retain selected headers,
redirect chains, initial HTML (512 KB cap) and parsed initial-response evidence,
separate from source declarations. Metrics are request-to-header/first-body-byte
and bounded body-read timings, not browser load times or Web Vitals. No LCP, CLS,
FCP, TBT, INP, Lighthouse score, field data or device emulation is fabricated.
Build mode/deployment identity and origin/CDN caches remain unverified.

Initial-response records retain text boundaries, headings, metadata, media and
JSON-LD independently of the source reports. `runtime.link_occurrences` and
`runtime.destinations` preserve response links resolved against the final URL and
first `<base href>`. New internal destinations appear in
`coverage.initial_response_extraction.new_internal_destinations_not_scheduled`;
they are outside the measured source frontier, and are not silently fetched.
`runtime.source_comparisons` compares source title declaration sets with observed
initial-response title sets. Inherited defaults may explain differences; these
comparisons are review evidence, not proof of a defect. Partial bodies, HTTP
errors, missing source rows or missing titles produce `not-comparable` states.

## Resume and invalidation

Completed category results can be reused with `--resume /tmp/previous.json` on an
unchanged capture, mode, extraction code/skills, dependency versions and model
selection. Runtime observations are always refreshed. Partial source captures
cannot be reused conservatively. Shared-source changes invalidate the whole source
result instead of attempting dependency-aware incremental invalidation.

```sh
python -m seo_agents.exploration.repo_analysis.inspect /path/to/site --mode static \
  --resume /tmp/exploration.json > /tmp/exploration-resumed.json
```

Python callers may pass `on_checkpoint(state)` to retain each completed step.
`ExplorationIncomplete.checkpoint` exposes completed work after failures or handled
interruptions. The CLI emits a partial bundle and exits with status 2 after these
failures. It does not automatically write checkpoints or survive a hard process
kill; a caller must persist callbacks if that behavior is needed.

The existing GitHub publisher still consumes only the seven category reports.
The richer source bundle is returned/exported explicitly; it is not added to PRs.
The existing `seo-agents` command retains its GitHub pipeline behavior.

## Report contract

Every report contains `schema_version`, `category`, `extraction`, and `pages`.
Each page carries its route, template, dynamic-route flag and extraction coverage.
Source evidence includes `file`, `line`, and `source_id` so agents can join content
references with links and media. No machine-specific checkout paths are saved.

| File | Contents |
| --- | --- |
| `content.json` | Full observed literal text fragments, UI labels, word counts, link/media references |
| `page_structure.json` | Heading text and levels, semantic landmarks |
| `internal_links.json` | Page-specific anchor text, original href, normalized destination, rel and target |
| `metadata.json` | HTML metadata values and literal Next.js title/description declarations, including layouts |
| `media.json` | Sources, alt, responsive attributes, declared dimensions, local source bytes, readable intrinsic dimensions, priority/preload and loading behavior |
| `urls.json` | Route templates, dynamic patterns, depth and query links |
| `eeat.json` | Visible candidate evidence for authorship, experience, expertise, trust, reviews and dates, policy links and possible citations |

Artifacts contain observations rather than embedded LLM judgments. The category
contract is validated both during extraction and before the PR publishes files.
Internal relative paths remain intact in `href`; `target_url` resolves them against
the page route. Absolute links are preserved without guessing the production origin.

## Analyze the saved JSON without a repository

```sh
python -m seo_agents.exploration.repo_analysis.json_analysis path/to/seo_reports/onpage
```

The analyzer reads only the seven JSON files. It emits evidence-linked review
findings for content coverage and duplication, headings, anchors and unmatched
destinations, metadata coverage/length/duplication, media alt/dimensions/bytes, and
E-E-A-T evidence gaps. Findings are review candidates, not verified runtime failures.
It does not call a model or fetch a website. Old report schemas must be regenerated.

## Legacy static mode coverage and media measurements

The legacy extractor follows Vite module-script mount entries before examining the React
app. Tree-sitter parses JavaScript/JSX and TypeScript/TSX without executing them.
Supported semantics include same-file/imported components, props and children,
JSON-backed maps and destructuring, literal pathname dispatch, basic React Router
routes, template strings and allowlisted pure data transformations. Local public-file
fetches are resolved without network requests. Object-valued click selections produce
separately labelled interaction states rather than being mixed into initial content.

The Next.js file-route fallback still inventories layouts and literal declarations;
its runtime props and data remain unresolved. Arbitrary router configurations,
unsupported expressions, browser-only state, API/CMS responses and CSS visibility
remain explicit gaps. A module entry that cannot resolve its root component fails
rather than publishing an empty HTML-shell-only report. Complete browser coverage
is not claimed.

Content fragments carry section IDs/headings, source locations, composition instance
IDs and JSON-pointer provenance. Content sections are listed separately for agents
to reconstruct hierarchy. Resource links such as the resume PDF are distinguished
from internal page links. E-E-A-T candidates use visible section context; they do not
verify credentials, employment, firsthand experience or reputation.

Media bytes describe local source assets, not optimized transfer bytes. PNG, GIF,
JPEG, WebP and SVG dimensions are read when supported. If `ffprobe` is installed,
local video dimensions and duration are measured with a timeout and network protocols
disabled. Otherwise these measurements remain unknown. Remote covers retain unknown
bytes/dimensions. `media.json` also contains a local asset manifest, including assets
not found in page-media records, so a favicon or unused asset is not silently lost.
A 200 KiB image-source budget is a review heuristic. Image undersizing requires its
display size. Declared loading and supported framework defaults are recorded.

## In-memory workflow

Analysis reads the target checkout and returns Python dictionaries in graph state.
Agent mode also retains its source snapshot and verified scripts in that state.
Reports and findings are not saved in this agent repository. No client snapshots,
audit files or generated artifacts are retained here. Docker transfers and video
probe output are streamed through memory.

After analysis completes, the PR publisher validates and serializes the in-memory
reports, then writes the seven JSON files into the target checkout inside the
sandbox for the PR commit. No report files are reloaded from local output folders.
A complete report payload is required before publication starts. Synthetic tests
use temporary directories outside this codebase and keep generated reports in memory.

## Local checks

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python scripts/benchmark_exploration.py
```

See [the implementation review](docs/exploration-improvements.md) for the
current-state map, suggestion decisions, benchmark evidence and remaining gaps.
