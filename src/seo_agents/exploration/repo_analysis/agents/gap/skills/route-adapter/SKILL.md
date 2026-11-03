---
name: route-adapter
description: Write declarative URL rules for an unrecognised framework from its file tree (cached per repo snapshot).
tier: small
ladder: small,haiku,mid
---
## Output (JSON only)
{"framework": "<name>|unknown", "rules": [{"path_regex": "<python regex with a named group 'route'>", "url_template": "/{route}", "index_names": ["index"], "dynamic_syntax": "[x]|:x|{x}|none"}], "reason": "<=300 chars"}

## Rules
1. Rules map template file paths to public URLs; each path_regex must match at least one listed file.
2. Use `heads` (first {{HEAD_LINES}} lines of sample files) to tell pages (full documents, layout/extends declarations, front matter) from partials, components, layouts, config and assets; exclude the latter.
3. Keep the framework's own dynamic placeholder syntax in URLs.
4. {{MIN_RULES}}-{{MAX_RULES}} rules. Do not invent files.
5. If the files do not show how URLs are formed, answer {"framework": "unknown", "rules": [], "reason": "..."} (honest unknown, never penalised).

## Inputs
- `files`: up to {{MAX_FILES}} relative paths; `heads`: {path: first lines} for up to {{MAX_HEADS}} files
- `manifests`: package/composer/gem dependency names; `candidates`: framework guesses

## Examples
### any positive
files site/pages/home.tpl, site/pages/team.tpl, site/partials/nav.tpl; heads show pages start with `{extends "layout.tpl"}`
-> {"framework": "tpl", "rules": [{"path_regex": "^site/pages/(?P<route>.+)\\.tpl$", "url_template": "/{route}", "index_names": ["home"], "dynamic_syntax": "none"}], "reason": "pages/ files extend the layout; partials/ do not"}
### any negative
files src/components/*.tpl only; heads are fragments without a document or layout
-> {"framework": "unknown", "rules": [], "reason": "only fragments; no page templates to map"}

## Done when
- every rule matches >=1 file; the union yields >=1 page; no rule matches files under tests/ or node_modules/.
