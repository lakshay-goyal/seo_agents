# SEO Agents

Analyzes a GitHub frontend repository and opens a reports PR through the existing
sandbox pipeline. Reports live in `seo_reports/onpage/` in the target repository.
Seven independent agents inspect the actual framework and generate extraction
scripts rather than relying on one fixed React/Next.js parser.

## Skill-guided exploration (default)

Each category has its own LangChain agent and packaged
[`SKILL.md`](src/seo_agents/exploration/checks/agents/skills): URLs, content, page
structure, internal links, metadata, media and E-E-A-T. The URL agent runs first;
its verified route/template catalog is shared with the other agents so all reports
cover the same pages. Each remaining agent has a fresh conversation, its own domain
skill and read-only listing, source-reading and search tools. There is no shared
conversation or reuse of another category's extraction code.

The workflow is:

1. Capture one bounded in-memory source/asset snapshot, excluding dependencies,
   generated reports, hidden files, common credential files and symlinks.
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
The source snapshot is capped at 2,000 files, 500 KB per file and 20 MB total;
omissions/truncation are reported in extraction coverage. Source-backed extraction
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
python -m seo_agents.exploration.checks.json_analysis path/to/seo_reports/onpage
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
```
