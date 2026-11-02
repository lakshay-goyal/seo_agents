---
name: route-adapter
description: Write declarative URL rules for an unrecognised framework from its file tree (cached per framework).
tier: mid
---
## Output (JSON only)
{"framework": "<name>", "rules": [{"path_regex": "<python regex with a named group 'route'>", "url_template": "/{route}", "index_names": ["index"], "dynamic_syntax": "[x]|:x|{x}|none"}], "reason": "<=300 chars"}

## Rules
1. Rules map template file paths to public URLs; each path_regex must match at least one listed file.
2. Exclude partials, components, layouts, tests, config and assets.
3. Keep the framework's own dynamic placeholder syntax in URLs.
4. 1–8 rules. Do not invent files.

## Inputs
- `files`: up to 400 relative paths; `manifests`: package/composer/gem dependency names; `candidates`: framework guesses

## Done when
- every rule matches ≥1 file; the union yields ≥1 page; no rule matches files under tests/ or node_modules/.
