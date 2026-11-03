# Site model contract (`seo-site-model/1.0`) and sharded exploration output (`seo-exploration/2.0`)

Written to `seo_reports/site_model/` by `site_model.explore_site()` (also via
`repo_analysis()` and the PR flow). Designed for an SEO execution agent that must act
from JSON alone: plan from `index.json`, read `site.json` once, load one
`pages/<id>.json` at a time.

## Files

| File | Purpose | Size |
| --- | --- | --- |
| `index.json` | Planning view: framework, coverage counts, one row per URL (id, url, template, status, title, title edit kind, LCP, issue hint) | small (target < 20k tokens) |
| `site.json` | Site-level facts: framework fingerprint, serve mode, URL manifest notes, served behaviour (robots.txt, soft 404, trailing slash, http→https, security headers), hreflang reciprocity, near-duplicate clusters, broken/blocked/redirected links, crawl errors with referrers, click depth, orphan candidates, deterministic skill signals, gap-worker report, validation issues, coverage ledger, model usage and cost, limitations | medium |
| `pages/<id>.json` | Everything about one URL | per page |

## Page record

```jsonc
{
  "id": "p_7c1e…", "url": "/pricing", "route": "/pricing", "template": "app/pricing/page.tsx",
  "kind": "page", "is_dynamic": false, "instance_status": "static|concrete|template",
  "discovered_by": ["adapter:next_routes", "sitemap:public/sitemap.xml", "crawl", "build-output"],
  "composition": ["app/layout.tsx", "app/pricing/page.tsx", "components/Hero.tsx"],
  "lens": "rendered|raw-html|source",
  "rendering": {"raw_html_words": 12, "rendered_words": 640, "js_dependent": true, "title_requires_js": false,
                "links_only_after_js": 19, "method": "measured|source-only"},
  "http": {"http_status": 200, "final_url": "…", "redirect_chain": [], "headers": {"x-robots-tag": null, "…": "…"}},
  "head": {
    "title": FIELD, "description": FIELD, "canonical": FIELD, "robots": FIELD, "lang": FIELD, "viewport": FIELD,
    "og": {"title": FIELD, "image": FIELD}, "twitter": {"card": FIELD},
    "hreflang": [{"hreflang": "fr", "href": "…", "edit": EDIT}],
    "json_ld": [{"types": ["Organization"], "valid": true, "edit": EDIT, "value": {"@context": "https://schema.org", "@type": "Organization"}}],
    "add_target": EDIT            // where to ADD any missing head tag on this stack
  },
  "headings": [{"level": 1, "text": "Simple pricing", "el": "e_19a…", "method": "…", "edit": EDIT, "confidence": 0.97, "path": "main > h1"}],
  "content": {"main_words": 610, "blocks": [{"tag": "p", "text": "…", "section": "main", "edit": EDIT}]},
  "links": {"out": [{"href": "/contact", "anchor": "Talk to sales", "rel": null, "in_main": true, "kind": "internal",
                      "status": 200, "edit": EDIT, "anchor_edit": EDIT}],
            "in_count": 9, "click_depth": 1, "orphan_candidate": false},
  "images": [{"src": "/hero.png", "alt": "", "alt_missing": false, "natural": [2400, 1600], "rendered": [1200, 800],
              "oversized_ratio": 2.0, "transfer_bytes": 412331, "format": "png", "loading": "eager", "above_fold": true,
              "edit": EDIT, "alt_edit": EDIT, "asset_file": "public/hero.png", "size_bytes": 412331}],
  "performance": {"mobile": {"metrics": {"lcp_ms": 2310, "cls": 0.02, "tbt_ms": 180}, "scores": {"performance": 0.81},
                             "lcp_element": {"selector": "…"}, "opportunities": [{"id": "uses-optimized-images", "savings_ms": 900}],
                             "runs": 3, "method": "lab: lighthouse CLI median-by-LCP of 3 runs"},
                  "desktop": {"…": "…"}},
  "mobile_parity": {"headings_missing_on_mobile": [], "links_missing_on_mobile": []},
  "eeat": [{"signal": "authorship", "status": "observed|not_observed", "method": "deterministic", "evidence": []}],
  "eeat_assessment": {"authorship": "present|absent|unclear", "method": "llm:small", "agreement": 1.0},
  "keywords": {"top_terms": ["…"], "topic": "acme pricing plans", "intent": "commercial", "target_queries": ["…"]},
  "limitations": ["…"]
}
```

`FIELD = {"value": str|null, "method": M, "edit": EDIT|null, "confidence": 0..1, ...}` where
`M` is `rendered | raw-html | source-literal | source-expression | source-i18n | front-matter | none`.
Optional keys: `lens_agreement` (served value equals the attributed source value),
`candidates` + `gap` (ambiguous; a gap worker may resolve it), `composed` (built from
several data fields), `source_declared` (declared in source but absent when served),
`grounding: "failed: …"` (validator could not find the value near the cited line).

`EDIT = {"kind": K, "file": str, "line": int, "col"?: int, "attr"?: str, "pointer"?: str,
"i18n_key"?: str, "prop"?: str, "via"?: str, "template"?: {file, line, col},
"composed_from"?: [pointer], "resolved_by"?: "llm:small|llm:mid"}`

| `kind` | Meaning for the execution agent |
| --- | --- |
| `literal` | Change the literal at file:line:col (`attr` names the attribute) |
| `prop` | A parent passes it to a component; change the prop at the usage site |
| `json` / `yaml` / `toml` / `markdown` | Change the data value at `pointer` (front matter for markdown) |
| `i18n` | Change the translation `i18n_key` (catalog file/line given; gettext msgid when inline) |
| `computed` | Produced by code/expressions; `via` and `template` point at the logic |
| `cms` | Runtime/CMS data (Shopify admin, WordPress post, API); code edits alone will not change it |
| `add` | Missing: add it at `file` / `anchor` for this stack |

## Guarantees and limits

- Values with `method: rendered|raw-html` were observed on the served page; `source-*`
  values are what the source declares (source-only mode when nothing could be served).
- Every `edit` is classified by the grounding validator into `verify: grounded | heuristic | failed`.
  Line-1 placeholders, "no source location matched" computed fallbacks and values absent near the
  cited line fail; `add` edits are moved to the real insertion point (anchor line or `</head>`).
  Failures are listed in `site.json.validation.issues`, lower `confidence`, and are never hidden.
- Every `edit` also carries `scope` (`shell | layout | template | data | literal`), `shared_by`
  (page ids rendering the same file:line:pointer) and, for templates, `template_n`. Each page has
  `head_scope` (`shared-shell | shared-layout | per-route`).
- `site.json.coverage.ledger` states, per URL and field, `measured | source-only | gap | failed | partial | unavailable`
  (a failed Lighthouse run is `failed`, not `measured`).
- Served facts (`site.json.served`) are measured on the canonical host after following apex→www and
  http→https redirects; each is `{value, method: "raw@<host>", status: measured|failed|not-run}`.
- Unresolvable items stay explicit (`candidates`, `limitations`, `gap_workers.unresolved`).

## Sharded exploration output (`seo-exploration/2.0`, `seo_reports/exploration/`)

Written next to `site_model/` by `output.write_exploration(model, dir)` (load with
`output.load_exploration(dir, include=[...])`). The site_model files stay for back-compat;
new consumers should read this layout. A cheap worker never loads more than it needs:
the planner reads `manifest.json` + `summary.json` (≈6k tokens), each worker loads one shard
(1–3k tokens), and evidence is opened only when an edit is not `grounded`.

| File | Contents |
| --- | --- |
| `manifest.json` | schema versions + `$id`s, snapshot sha, framework, hosting, canonical host, lens (`degraded` + banner when source-only), coverage per field and `absent` counts, validation (site-model issues, edit verification counts, schema and reference errors), gap-worker summary, model cost, per-node timings, output file list (sha, bytes, token estimate, role) |
| `summary.json` | **Planner input** (≤4k tokens): ranked `issues[]` (ISSUE below) |
| `site.json` | served facts on the canonical host (robots, sitemap, soft 404, trailing slash, host/scheme redirects, HSTS), redirects/rewrites from config, hosting + deployment config, rendering modes, hreflang, duplicates, link health, crawl errors, orphans |
| `entity.json` | Person/Organization candidates: name, role, sameAs (social profiles only), email, logo/portrait, og:image candidates |
| `routes.json` | `[{route_id, url, route, template, discovered_by, render, head_scope, lens, http_status, click_depth, in_count, orphan, file}]` |
| `pages/<route_id>.json` | head FACTs, `absent[]`, headings with EDITs, `h1_count`, `word_count`, content outline, `link_ids[]`, `image_ids[]`, rendering, E-E-A-T signals |
| `images.json` | deduplicated images: `img_id, src, asset_file, bytes, natural, rendered, dpr_overserve{1x,2x}, format, loading, dimensions_set, alt, alt_status (missing/empty/literal/expression), alt_edit, src_edit, lcp_candidate_on[], used_on[]` |
| `links.json` | deduplicated links: `link_id, href, anchor, kind (internal/external/anchor/js-only/contact), rel, status, in_main, section, href_edit, anchor_edit, used_on[]` |
| `metadata.json` | per route title/description/canonical/robots/lang/viewport/og/twitter FACTs + `add_target` + `head_scope` + stack `recipe` |
| `structured_data.json` | per route present JSON-LD blocks, recommended-but-absent types, insertion target |
| `performance.json` | lab runs per route (or `{status: failed}`), ledger states, large assets |
| `keywords.json` | top 20 terms per route from visible copy |
| `evidence/<ev_id>.json` | source snippet (±3 lines), file sha, alternatives for one EDIT; never loaded by default |
| `schemas/*.schema.json` | JSON Schema per file; every file is validated on write |

```jsonc
EDIT  = {"kind": "literal|json|yaml|toml|markdown|i18n|prop|computed|add|platform",
         "file": "src/components/PageRail.jsx", "line": 3, "col": 22, "end_line": 3, "end_col": 30,
         "pointer": null, "attr": "href", "scope": "literal|data|template|layout|shell", "template_n": 4,
         "shared_by": ["r_home", "r_about"], "verify": "grounded|heuristic|failed", "evidence": "ev_9f2c…"}
FACT  = {"value": "…", "method": "rendered@www.example.com|raw@www.example.com|source|llm", "conf": 0.95, "edit": EDIT}
ISSUE = {"id": "iss_soft_404", "severity": "high|medium|low|info", "scope": "site|routes|route:r_about",
         "routes": ["r_…"], "title": "…", "facts": ["site.json#/served/soft_404"],
         "fix": {"kind": "code|data|content|platform|build|none", "where": "vercel.json", "recipe": "…"},
         "status": "measured|lead"}
```

Rules: compact JSON; `null`, empty and zero-count entries are omitted (absence is recorded once,
in `pages/*.absent` and `manifest.coverage.absent`); ids are content hashes (`r_`, `img_`, `lnk_`,
`iss_<type>`, `ev_`); a link or image shared by several routes is stored once with `used_on[]`; all
paths are repo-relative. Every file is validated against its schema and cross-checked for referential
integrity (route/link/image/evidence ids); errors are listed in `manifest.validation`.

How an execution worker uses an EDIT: apply only when `verify == "grounded"`; otherwise read
`evidence/<ev>.json` or escalate. `scope` decides blast radius: `data` and `literal` are safe per
instance; `template` changes all `template_n` rendered instances (change the data, not the template,
for per-instance copy); `layout`/`shell` change every route in `shared_by`. On a `shared-shell`
`head_scope`, per-route head changes depend on pre-rendering (see `metadata.json.routes.*.recipe`).

Env: `SEO_EXPLORATION_DIR` (output dir override), `SEO_MEASURE_WORKERS` (caps graph concurrency),
`SEO_CHECKPOINT=/path.sqlite` (opt-in LangGraph resume; needs `langgraph-checkpoint-sqlite`),
`SEO_CHECKPOINT_THREAD`.
