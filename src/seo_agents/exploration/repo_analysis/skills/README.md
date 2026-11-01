# Repository-analysis agent skills

One skill per analysis agent. Skills are **framework-agnostic**: each lists the
patterns for its concern across every ecosystem at once (React/Next, Vue/Nuxt,
Svelte, Astro, Angular, static site generators, plain HTML, Django/Flask, Rails,
Laravel/PHP, Express, ...). An agent never needs to know which framework a
repository uses; whatever matches is recorded with its file reference and the
exact source code.

Each `<agent>/SKILL.md` has prose guidance (read by LLM agents) and one fenced
` ```json profile ` block (applied by `seo_agents.exploration.repo_analysis.tools.skill_runner`).

Profile fields:

| Field | Meaning |
| --- | --- |
| `name` | Agent name. |
| `signals` | `{id, label, regex, glob?, block?, ignore_case?, value_group?, files_only?}`. `regex` runs in multiline mode over captured source. `block: true` captures the whole balanced `{...}`/`(...)` construct after the match. `value_group` extracts a value. `files_only` matches the regex against file paths instead of content and records the whole file. |
| `gaps` | `{id, label, when_none_of: [signal ids]}`: recorded when none of those signals matched anywhere. A gap is a lead, not proof of absence (runtime/CMS code may still provide it). |
| `edit_guidance` | Where an agent should make a change for this concern, by pattern of code found. |

Agent-specific profile keys (`conventions`, `declarations`, `framework_markers`,
`weights`) are documented in the corresponding skill.

Add a pattern by editing JSON; no Python change is needed.
