# Repository-analysis agent skills

One skill per deterministic analysis agent. Skills are **framework-agnostic**: each
lists the patterns for its concern across every ecosystem at once (React/Next,
Vue/Nuxt, Svelte, Astro, Angular, Shopify Liquid, WordPress PHP, Hugo/Jekyll,
Ghost, Django/Jinja, Rails ERB, Laravel Blade, Express, plain HTML, ...). An agent
never needs to know which framework a repository uses.

- `skills/<agent>/SKILL.md` — human documentation only (not read at runtime).
- `profiles/<agent>.json` — the runtime configuration applied by
  `tools/skill_runner.py`, validated on load against `profiles/schema.json`.

Signal fields:

| Field | Meaning |
| --- | --- |
| `id`, `label` | Stable id and human label. |
| `regex` | Python regex, multiline mode (`ignore_case` adds re.I). |
| `precision` | `high` / `medium` / `low`: how often a match is the real thing. Weigh low-precision signals accordingly. |
| `examples` | `{match: [...], no_match: [...]}`; executed by `tests/test_skill_profiles.py`. Add a `no_match` for every false positive you fix. |
| scope | At least one of `files_only` (match paths), `glob` (list) or `languages` (names from `source_index.language_of` or groups `@markup`, `@scripts`, `@templates`, `@content`, `@data`, `@styles`, `@server`, `@config`). |
| `exclude`, `requires` | Path regex to skip; regex that must appear in a file before the signal runs on it. |
| `exclude_comments` | Skip matches inside comments. README/CHANGELOG/LICENSE files are always skipped unless `include_docs`. |
| `block`, `value_group`, `mode`, `tags` | Capture the balanced construct, extract a value, rendering mode, Web Vitals tags. |

Every signal result reports `count` (records returned), `total_matches` and
`truncated` (true when the per-file/per-signal caps dropped records).

`gaps` are `{id, label, when_none_of, only_if_any?}`: leads, not proof of absence.
`execution_hint` is guidance for the execution phase and is never emitted in
exploration output. Route discovery uses `conventions` (path regex + `adapter`,
optional `requires_path`, `route_map`, `route_template`) and `declarations`
(optional structural `parser`); see `skills/routes/SKILL.md`.

Add a pattern by editing JSON plus examples; no Python change is needed.
