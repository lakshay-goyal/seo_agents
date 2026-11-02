---
name: explore-code-structure
description: Profile any repository - manifests, dependencies, languages, entry points, hosting config and framework markers - without assuming a framework.
---

Start from what the repository declares: dependency manifests (package.json,
requirements/pyproject, composer.json, Gemfile, go.mod, Cargo.toml), build
scripts, entry points (index.html, main/app/server files) and hosting config
(vercel.json, netlify.toml, _redirects, .htaccess, nginx, Dockerfile). Framework
labels below are descriptive only; every other agent works from code patterns, so
an unlisted or custom framework is still analyzed.

`framework_markers`: label → dependency names or marker file globs. Any hit adds
the label to `project.framework_hints` with its evidence.

Runtime profile: [`profiles/code-exploration.json`](../../profiles/code-exploration.json) (regex signals with scope, precision and match/no-match examples; validated by `profiles/schema.json` and `tests/test_skill_profiles.py`). This file is human documentation only.
