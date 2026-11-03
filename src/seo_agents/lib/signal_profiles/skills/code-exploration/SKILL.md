---
name: explore-code-structure
description: Profile any repository - manifests, dependencies, languages, entry points, parsed hosting config and framework markers.
---

Start from what the repository declares: dependency manifests (package.json, requirements/pyproject,
composer.json, Gemfile, go.mod, Cargo.toml), build config (`vite.config.*`, `next.config.*`,
`react-router.config.ts`, Hugo `config/_default/`), entry points and hosting config. Tooling-only files
(tsconfig, babel, postcss, tailwind) are not recorded: they carry no SEO signal.

`deployment_config` is the parsed hosting layer `{host, redirects, rewrites, headers}` from
`vercel.json`, `netlify.toml`, `_headers`, `_redirects`, `firebase.json`, `wrangler.toml`,
`staticwebapp.config.json` and nginx/Apache config. Framework labels in `framework_markers` are
descriptive only; every other agent works from code patterns.

Runtime profile: [`profiles/code-exploration.json`](../../profiles/code-exploration.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). Python post-processing: `agents/code_exploration.py`. Every backticked token in this file is either in the profile or listed in `COVERED_BY_PYTHON` in `tests/test_skill_docs.py`. This file is human documentation only.
