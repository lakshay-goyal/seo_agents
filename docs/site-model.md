# Site model contract (`seo-site-model/1.0`)

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
    "json_ld": [{"types": ["Organization"], "valid": true, "edit": EDIT}],
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
- Every `edit` with a file/line is checked by the grounding validator; failures are
  listed in `site.json.validation.issues` and lower `confidence`, never hidden.
- `site.json.coverage.ledger` states, per URL and category, `measured | source-only | gap`.
- Unresolvable items stay explicit (`candidates`, `limitations`, `gap_workers.unresolved`).
