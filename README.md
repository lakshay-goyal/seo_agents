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
python -m seo_agents.repo_analysis /path/to/site [--site-url URL] [--render] [--build]
```

Requires Docker and `GITHUB_TOKEN` (classic PAT, `repo` scope) for the PR flow. Only
plain `https://github.com/<owner>/<repo>` URLs are accepted (anything else exits with code 2).
`--render`/`--build` (or `SEO_RENDER=1`/`SEO_BUILD=1`) provision node, Playwright
Chromium and Lighthouse in the sandbox on a best-effort basis; failures only reduce the
available lenses, and the reason is recorded. See `.env.example` for every setting.

Security: each run uses a fresh, uniquely named `--rm` container with CPU/memory/pid limits
and a fresh clone keyed by `owner/repo`; the analysed commit SHA is recorded and used as the
PR base. Clone + analysis run **without** `GITHUB_TOKEN`; only the PR phase gets it, and it
runs no repository code. `npm/yarn/pnpm install`, build and preview get an allowlisted
environment (no tokens or API keys), run as an unprivileged user in their own process
group, and are killed as a group on timeout. Secrets reach `docker exec` through a 0600
`--env-file`, never `-e KEY=VALUE`.

Each run pushes its own branch (`seo-agents/<date>-<sha7>`, no force-push). The PR commits
only the compact planner files (`seo_reports/exploration/manifest.json`, `summary.json`)
and on-page proposals within a 50 KB budget (`SEO_PR_COMMIT_BUDGET`; `SEO_PR_COMMIT_FULL=1`
commits everything), and puts the summary in the PR body.

## Code layout

```
src/seo_agents/
├── main.py                    `seo-agents` CLI: validate URL -> DockerSandbox -> SandboxPipeline -> reports PR
├── lib/                       everything you tune by hand
│   ├── models.py              model tier per agent (AGENT_MODELS), tier defaults, token caps, prices
│   ├── tool_mapping.py        agent / stage -> tools it uses; LangChain tools of the legacy extractors
│   ├── prompts/               every prompt sent to a model (SKILL.md files + system_prompts.py)
│   └── signal_profiles/       regex profiles used by the deterministic agents (+ their docs)
├── llm/                       shared model layer: ChatModelFactory, tiers, prompt caching, errors, usage/cost
├── utils/                     helpers used in several places (logger, LangGraph reducers, JSON parsing, env, prompt files)
├── repo_analysis/             stage 1: explore the repository (no model except gap workers)
│   ├── repo_analysis.py       RepoAnalysisWorkflow: the sequential LangGraph that calls every agent below
│   ├── agents/                one class per agent (codebase, crawling_indexing, rendering_performance, security,
│   │                          entity_signals, gap_workers, legacy_llm_extractors)
│   ├── tools/                 one class per tool (repository, parsing, routing, content, signal_profiles, http)
│   ├── measurement/           runtime lenses: SiteServer, PageRenderer, HtmlSummarizer, LighthouseAuditor, LinkChecker
│   ├── site_model/            SiteModelWorkflow (map-reduce LangGraph, one class per stage) + its building blocks
│   ├── repo_context/          RepoContextBuilder -> seo_reports/repo_context.json
│   ├── evidence/              legacy evidence bundle + inspect CLI
│   └── output/                sharded seo_reports/exploration/ writer, issue ranking, JSON schemas
├── keyword_research/          stage 2: keyword research (providers + LLM ideas + Jev judge) -> seo_reports/keywords
│   ├── keyword_research.py    KeywordResearchWorkflow: load -> profile -> competitors -> page loop -> map -> write
│   ├── contracts.py           shared dataclasses + Protocols (ResearchToolkit, KeywordGenerator, KeywordJudge)
│   ├── providers/ generation/ judge/ mapping/ output/
├── onpage/                    stage 3: on-page proposals from the exploration JSON (+ keyword_map.json)
│   ├── onpage_analysis.py     OnpageAnalysisWorkflow: load -> competitors -> 21 checks -> site -> articles -> write
│   ├── checks/                one class per checklist item + checklist.py (execution order)
│   ├── content/               content studio: competitor research + new blog articles (see below)
│   ├── writer/                ContentWriter (skill prompt -> model -> validator -> repair loop)
│   ├── reports/ page_context/ changes/ validation/ output/
└── sandbox/                   Docker sandbox, container entrypoint, clone + PR
```

To move an agent to a cheaper or stronger model, edit `lib/models.py`. To see which tools an agent gets, read
`lib/tool_mapping.py` (a test keeps it in sync with the code). Prompts are only in `lib/prompts/`.

## How exploration works

Deterministic code produces the facts; cheap models resolve only what code cannot. Works on
any stack: the rendered site (when available) is ground truth, framework adapters are seeds.

| Stage | What happens | Module |
| --- | --- | --- |
| A acquire | one snapshot (source dirs first; assets have their own budget; build output skipped; README/docs tagged `role: doc` and never treated as page content) → framework fingerprint (every framework above threshold, per package root for monorepos) → unified element index (JSX/TSX via tree-sitter; Vue/Svelte/Astro; HTML and every server template language; Markdown; JSON/YAML/TOML/PO data and i18n catalogs; composition edges). Every value has an exact span (`line, col, end_line, end_col`), content-anchored ids, same-file constant / prop / namespaced-i18n data flow, loop context (`in_loop`, `template_n`, `data_source` JSON pointers) and `shared`/`used_by` flags | `tools/repository/framework_detection.py`, `tools/parsing/element_index.py` |
| B build/serve | `site_url`, else opt-in install+build+serve (per-tool port flags, yarn berry/corepack, workspace-root installs, readiness = 2xx), else pre-built HTML (flagged possibly stale), else source-only (degraded-mode banner in `manifest.json`). `SEO_INSTRUMENT=1` stamps `data-seo-src="file:line:col"` on rendered Vite/React elements for exact attribution | `measurement/site_server.py` |
| C URL manifest | adapters for every detected framework (file routes, router configs incl. route constants, pathname-switch SPAs, nav arrays, Next intercepting/parallel/`[locale]`, Remix v1, Gatsby `createPage`, Shopify, WordPress, Hugo/Jekyll/11ty front matter incl. TOML/aliases, Django/Laravel/Rails/Flask/Express/Razor) ∪ static params ∪ content collections ∪ sitemaps ∪ build output ∪ crawl ∪ gap-worker suggestions; every row has `discovered_by[]`; catch-all rewrites / client not-found views recorded as soft-404 risks | `tools/routing/framework_route_adapters.py`, `site_model/url_manifest.py`, `tools/routing/head_recipes.py` |
| D measure | canonical host resolved first (apex→www, http→https chain), then per URL in parallel: raw HTML + headers, rendered DOM desktop+mobile (pooled Chromium), Lighthouse (parallel, partial runs kept), link status (`ok/broken/blocked/error`), served probes on the final host (robots/sitemap incl. `spa-shell-fallback`, soft 404, trailing slash on a real route, HSTS, X-Robots-Tag) | `measurement/*`, `site_model/site_model_workflow.py` |
| E attribute | served value → file:line:col: `data-seo-src` hint → lens agreement → exact literal → data/i18n → composed; links match href + anchor + DOM order; no `line:1` placeholders (unresolved → gap) | `site_model/source_lens.py`, `site_model/value_attribution.py` |
| F gap workers | typed small-model tasks with JSON-schema structured output, closed-set candidate ids, edit kind derived in code; transport retries separate from validation retries; small → haiku → mid only after 2 validation failures; caches keyed by repo + snapshot + code version | `agents/gap_workers/`, `lib/prompts/gap_workers/` |
| G assemble + verify | grounding validator (`verify: grounded/heuristic/failed` on every EDIT), scope pass (`scope: shell/layout/template/data/literal`, `shared_by`, `template_n`, per-route `head_scope`), coverage ledger reflecting failures, JSON Schema + referential-integrity validation | `site_model/site_model_builder.py`, `output/` |

### Output: `seo_reports/exploration/` (sharded, the execution-stage contract)

`manifest.json` (snapshot, frameworks, hosting, canonical host, lenses, coverage, cost) and
`summary.json` (≤4k tokens, ranked `issues[]` with fact refs and fix target) are the planner
input. Workers load one shard: `site.json`, `entity.json`, `routes.json`, `pages/<route_id>.json`,
`images.json`, `links.json`, `metadata.json`, `structured_data.json`, `performance.json`,
`keywords.json`; `evidence/<ev_id>.json` is lazy. JSON Schemas live in `schemas/`. An EDIT is
applied only when `verify == "grounded"`; check `scope`/`shared_by` before editing a shared
file. Contract: [`docs/site-model.md`](docs/site-model.md). The older
`seo_reports/site_model/` files are still written for back-compat.

Accuracy is measured, not assumed: `scripts/score_fixtures.py` (hermetic: no model, no cache)
scores 19 golden fixture repos (`tests/fixtures/sites/`, incl. a pathname-switch SPA mirroring
the PR #8 portfolio, Remix, Gatsby, Eleventy, Express, Angular) for URL recall/precision,
head-field accuracy, heading/link/image precision+recall and edit-attribution accuracy.

```sh
PYTHONPATH=src .venv/bin/python scripts/score_fixtures.py --misses
```

## On-page SEO stage (`seo_agents.onpage`)

Reads only the exploration JSON and writes proposals to `seo_reports/onpage/`. Client source is
never read or edited; a human reviews and applies the approved ones.

```sh
python -m seo_agents.onpage path/to/seo_reports [--site-url https://www.example.com] [--publish]
```

Each page gets 4 model calls (intent + keyword map on small, one page plan on mid with a cached
system prompt and an explicit fact→slot assignment, alts + anchors on small, optional
E-E-A-T/AEO advice); everything else is deterministic (accessible names, image technical issues,
social tags, canonical, robots, viewport, redirects, sitemap/robots/llms.txt, FAQ↔JSON-LD,
dedup, JSON-LD `@id` graph). A **scope gate** blocks head changes that would land in a shared
shell or layout (e.g. a Vite SPA `index.html`) behind a site-level `chg_prerender` /
`chg_per_route_head`, retargeted to the framework's per-route head location (Next metadata,
Nuxt `useSeoMeta`, `<svelte:head>`, Remix `meta`, Astro layout props, Gatsby Head, front
matter, Liquid/WordPress/server templates).

Per page: `changes` (≤15 atomic ops: `set_text | set_attr | set_json | insert_node | remove |
platform_config`, one per template pointer, with `dedup_key`, `target.scope/shared_by`,
`depends_on`, `status`), `changes_more`, `advice`, `client_inputs` (also `client_inputs.json`).
Validators: grounding against served text + data JSON only (numbers incl. spelled-out), entity
repetition cap, no synonym swaps of the client's own terms, FAQ ∩ H2 = ∅, no repeated facts.
Settings: `SEO_ONPAGE_TIER`, `SEO_ONPAGE_ATTEMPTS`, `SEO_ONPAGE_ADVICE`,
`SEO_ONPAGE_REASONING_EFFORT`, `SEO_ONPAGE_CONCURRENCY`, `SEO_ONPAGE_MAX_PAGES`.

### Competitor research and new articles (`onpage/content/`)

The only on-page step that reads the web (never the client source):

1. **competitors** (before the checks): for every page, fetch the pages that rank for its keywords (keyword
   research `competitor_urls` + live SERP top results via DataForSEO when configured). robots.txt is respected,
   HTML only, cached 7 days. Extract title, H1, H2/H3 outline, word count, lists/tables/images/video/FAQ, alt
   coverage, dates, JSON-LD. Find the shared patterns (depth, formats, common sub-topics, title and URL habits,
   freshness). `page_plan` gets them as `competitors` (structure only, never competitor text); `content_quality`
   flags depth and missing formats vs the median ranking page.
2. **articles** (after `site`): `ArticleStudio` plans `SEO_ONPAGE_ARTICLES` (default 3) new blog articles from user
   demand (researched questions, content gaps, People Also Ask, competitor sub-topics, business seed topics), then for
   each: live SERP for its primary keyword -> competitor fetch + patterns + title inspiration + dated fact passages ->
   `article-writer` (mid) -> deterministic validator with repair loop -> fact check -> Markdown + JSON-LD.

Enforced on every article (`content/article_rules.py`, `content/article_writer.py`):

| Area | Rule |
| --- | --- |
| Intent | primary keyword + questions come from the topic pool; every question gets its own H2/H3 (or FAQ) with a direct 40-70 word answer; answer-first intro |
| Depth | body ≥ 80% of the competitor median word count (900-2500 floor), 4-12 H2s, no 10-word phrase copied from a competitor |
| Title / H1 | title 30-60 chars with the primary keyword and a CTR hook (number, current year, brackets, question, colon benefit, power word) learned from ranking titles; no other year; differs from competitor titles |
| URL | `/<blog folder>/<slug>`: 2-6 lowercase hyphenated words, no repeated word, no folder word repeat, no year, no ids, names the topic, unique |
| Visuals | at least one table, one list, one Mermaid diagram and one image (client asset or `image_brief` to create) |
| Image SEO | unique descriptive alt (5-125 chars, 3+ words, no "image of" / file names / stuffing), descriptive image file names |
| Bullets | plain items (no typed markers), 2-10 per list, 1-40 words, consistent end punctuation, no duplicates; rendered as `- ` / `1. ` |
| Facts / freshness | every number is a small count, the run year, client content, or cited `[S#]` to a source dated within 2 years whose passage contains it; `Last updated` = run date; `fact_check` verdict + review-by date |
| Internal links | ≥ 3 inline links to real site pages incl. the pillar; `link_from` = existing pages that should link to the article; 3-6 tags + category |

Outputs: `onpage/competitors/index.json`, `onpage/articles/index.json`, `articles/<slug>.md` (ready-to-publish draft),
`articles/<slug>.json` (article data, `fact_check`, `json_ld`, research). Settings: `SEO_ONPAGE_ARTICLES` (0 = off),
`SEO_CONTENT_COMPETITORS` (pages per topic, default 5), `SEO_CONTENT_FETCH` (0 = no network),
`SEO_CONTENT_CACHE_DIR`, `SEO_CONTENT_CACHE_TTL_DAYS`, `SEO_ARTICLE_MAX_TOKENS`.

## Keyword research stage (`seo_agents.keyword_research`)

Runs between the exploration and the on-page stage. Reads only the exploration JSON, finds keywords per page with
real third-party data, judges each one, maps them site-wide and writes `seo_reports/keywords/`. The LLM proposes
keywords and hypotheses only; volumes, difficulty, SERP and trends come from providers, and Jev (TypeSafe) answers
atomic judgments that code turns into accept / reject / review.

```sh
python -m seo_agents.keyword_research path/to/seo_reports [--country US --location-code 2840 --language en] \
    [--competitor rival.com ...] [--pages ID|URL ...] [--max-iterations N] [--out DIR] [--site-url URL]
```

In the sandbox, `SEO_KEYWORD_RESEARCH=1` runs it right after the exploration and commits `seo_reports/keywords/`
on the same PR (`SEO_KEYWORD_*`, `DATAFORSEO_*`, `GOOGLE_ADS_*`, `GSC_*`, `TYPESAFE_*` are forwarded).

```
START -> load -> business_profile -> competitor_discovery -> research_loop -> map -> write -> END
                                                                  |
          per page, in parallel (SEO_KEYWORD_CONCURRENCY), a compiled LangGraph subgraph:
          START -> generate -> expand -> enrich -> judge --should_continue--> generate
                                                         \--> finish -> END   (quota met | max iterations | nothing new)
```

| Node | What happens |
| --- | --- |
| load | `PageSelector` (reuses `ExplorationReportsLoader` + `PageContextBuilder`) -> one `PageBrief` per real page, own domain, market + targets, available providers |
| business_profile | generator profiles the business once (brand, offering, audience, competitors); `--competitor` domains merged in |
| competitor_discovery | seed queries (title, H1, exploration terms, seed topics) -> competitor URLs from the SERP -> **reverse research**: keywords each competitor URL ranks for; Search Console queries the page already gets; the most relevant are enriched up front |
| generate | needs = targets − accepted per slot; the generator gets the needs, competitor keywords + metrics, accepted keywords (this page and every other page, against cannibalization) and the rejected ones with their codes and reasons |
| expand | provider related keywords on last iteration's accepts (or the seeds) and questions (autocomplete / People-Also-Ask) while questions are needed; they join a per-page pool with the competitor/GSC keywords, and the most relevant fill the batch |
| enrich | volume, difficulty, CPC/bids, intent, SERP snapshot, GSC, content alignment; Google Trends compared in groups of 5 (direction, seasonality, regions) |
| judge | hard gates -> Jev judgments -> decision table -> composite score -> quota fill; a judge failure is `review` + `judge_error`, never a silent "no" |
| map | `KeywordMapper`: same keyword / token overlap / shared SERP URLs across pages -> `judge.same_intent`; the page with the best `composite_score × content_alignment` keeps it, the other gets `REJECT cannibalization` naming the winner; one primary per page; topic clusters (pillar = broadest primary head in a shared-token group) |
| write | `KeywordOutputWriter` |

Targets per page (`contracts.ResearchTargets`): 1 primary, 4 secondary, 3 questions (AEO), 2 conversational (GEO),
0 short-tail for now (long tail first), max 4 iterations, 25 candidates per iteration.

**Data sources**: DataForSEO (volume, keyword difficulty, SERP + features + People-Also-Ask, related and
competitor-ranked keywords), Google Ads Keyword Planner (Ads volume, bids, ideas), Google Search Console (existing
impressions / position), Google Trends (interest over time, regions), autocomplete (questions). Every metric carries
provenance; a metric a provider did not return is unknown (an empty cell), never 0. With no provider configured the
stage still runs, flags a **DEGRADED** banner in the log and `summary.md`, and every keyword that needs data becomes
`review`. Without `TYPESAFE_API_KEY` the judge runs heuristic-only (gate-passing keywords become `review`). Without
`OPENROUTER_API_KEY` the stage refuses to run.

**Output** `seo_reports/keywords/`:

- `keyword_research.csv` — every candidate ever generated or expanded, one row each (accepted, rejected, review and
  `unevaluated` pool leftovers). Columns: `page_id, page_url, keyword, kind, tail, source, competitor_url, iteration,
  hypothesized_intent, judged_user_intent, serp_intent, journey_stage, search_volume, search_volume_ads,
  keyword_difficulty, cpc, bid_low, bid_high, ads_competition, ads_competition_level, provider_intent,
  trend_direction, seasonal_peak_months, top_regions, serp_features, people_also_ask, click_opportunity,
  serp_authority_share, gsc_impressions, gsc_position, content_alignment, factor_<name>…, composite_score, decision,
  final_status, mapped_page_url, rejection_codes, rejection_reasons, filter_stage (gate | jev | quota | mapping |
  not_evaluated), providers, retrieved_at, market, rationale`. Lists are `;`-joined; `rejection_reasons` explains how
  and why a keyword was not used.
- `keyword_map.json` — `{schema: "keyword-research/1", market, business, generated_at, filters, pages: {page_id:
  PageKeywordPlan}}`; each plan has `primary, secondary, questions, conversational, short_tail, intent,
  journey_stage, cluster {role, pillar_url, related_urls}, competitor_urls, content_gaps, iterations, status
  (complete | partial | failed)`.
- `pages/<page_id>.json` — brief, plan, competitor URLs, cannibalization moves and every record with full evidence
  and verdict (audit); `jev/` holds the logged Jev request payloads.
- `summary.json` / `summary.md` — counts per decision, rejection code and filter stage, complete vs partial pages,
  providers, Jev mode, generator failures, cost estimates when the components report them.

**How on-page uses it**: `keyword_map.json` is the keyword contract for `seo_agents.onpage`: for page `page_id` it
should use `primary` for the title / H1 / description, work `secondary` into headings and copy, answer `questions` as
FAQ / direct answers (AEO), cover `conversational` prompts in the direct answer (GEO), match `intent` /
`journey_stage`, link cluster pages to `cluster.pillar_url` (and the pillar to `related_urls`), and treat
`content_gaps` as sections to consider. Pages with `status: partial` have fewer keywords than targeted;
`review` rows in the CSV need a human decision before they are used.

Settings: `SEO_KEYWORD_COUNTRY`, `SEO_KEYWORD_LOCATION_CODE`, `SEO_KEYWORD_LANGUAGE`, `SEO_KEYWORD_MAX_ITERATIONS`,
`SEO_KEYWORD_CONCURRENCY`, `SEO_KEYWORD_MAX_PAGES`, `SEO_KEYWORD_TIER`, `SEO_KEYWORD_REASONING_EFFORT`,
`SEO_KEYWORD_CACHE*`, `SEO_KEYWORD_AUTOCOMPLETE*`, `SEO_KEYWORD_ENRICH_SERP`, `SEO_KEYWORD_MAX_SPEND`,
`SEO_KEYWORD_COMPETITOR_*`, `SEO_KEYWORD_SAME_INTENT`, `KEYWORD_SITE_AUTHORITY` (see `.env.example`).

## Model tiers

`small` (`OPENROUTER_SMALL_MODEL`), `haiku` (`OPENROUTER_HAIKU_MODEL`), `mid`
(`OPENROUTER_MID_MODEL`), `large` (`OPENROUTER_LARGE_MODEL`, offline only with
`SEO_ALLOW_LARGE`). Skills choose a tier through `tier:` frontmatter; per-tier reasoning via
`OPENROUTER_<TIER>_REASONING_EFFORT` (escalations always reason at least `low`). Anthropic
models get `cache_control` on static system blocks; cost accounting uses provider cache
read/write multipliers.

## Repository context (`repo_context.json`)

The deterministic skill runners (`repo_analysis.explore_codebase()`, profiles in
[`lib/signal_profiles/profiles/`](src/seo_agents/lib/signal_profiles/profiles)) still write
`seo_reports/repo_context.json`: per-concern signals (sitemap/robots, canonicals/redirects,
rendering, client diagnostics, state, Core Web Vitals causes, security headers,
structured data, mobile, i18n, shareability, brand/entity, local business, AI-search
readiness) with precision labels, truncation flags and file references + content hashes
(no file copies by default). Its summaries also feed `site.json`.

## Legacy LLM category extractors (deprecated, opt-in: `--llm-agents` / `SEO_RUN_LLM_AGENTS=1`)

Deprecated: the site model covers this data more cheaply. A legacy-agent failure no longer
blocks publishing the site model or repo_context.

Each category has its own LangChain agent and packaged
[`SKILL.md`](src/seo_agents/lib/prompts/legacy_extractors): URLs, content, page
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
python -m seo_agents.repo_analysis.evidence.inspect_evidence_cli /path/to/site --mode agent > /tmp/exploration.json
# Offline legacy extraction plus the same corpus/evidence views:
python -m seo_agents.repo_analysis.evidence.inspect_evidence_cli /path/to/site --mode static > /tmp/exploration.json
# Query the saved evidence without source, models or network access:
python -m seo_agents.repo_analysis.evidence.inspect_evidence_cli /tmp/exploration.json --query /pages
python -m seo_agents.repo_analysis.evidence.inspect_evidence_cli /tmp/exploration.json --query /reports/content/pages/0
# Recover source/page relationships for src/layout.tsx (JSON-pointer escaping):
python -m seo_agents.repo_analysis.evidence.inspect_evidence_cli /tmp/exploration.json --query /source_relationships/src~1layout.tsx
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
python -m seo_agents.repo_analysis.evidence.inspect_evidence_cli /path/to/site --mode static \
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
python -m seo_agents.repo_analysis.evidence.inspect_evidence_cli /path/to/site --mode static \
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
