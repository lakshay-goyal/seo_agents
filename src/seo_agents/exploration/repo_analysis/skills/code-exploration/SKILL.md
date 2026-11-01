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

```json profile
{
  "name": "code-exploration",
  "framework_markers": {
    "Next.js": {"dependencies": ["next"], "files": ["next.config.*"]},
    "React": {"dependencies": ["react", "react-dom"]},
    "Remix / React Router": {"dependencies": ["@remix-run/react", "@react-router/dev"]},
    "Gatsby": {"dependencies": ["gatsby"], "files": ["gatsby-config.*"]},
    "Vue": {"dependencies": ["vue"]},
    "Nuxt": {"dependencies": ["nuxt"], "files": ["nuxt.config.*"]},
    "Svelte / SvelteKit": {"dependencies": ["svelte", "@sveltejs/kit"], "files": ["svelte.config.*"]},
    "Astro": {"dependencies": ["astro"], "files": ["astro.config.*"]},
    "Angular": {"dependencies": ["@angular/core"], "files": ["angular.json"]},
    "Solid": {"dependencies": ["solid-js", "@solidjs/start"]},
    "Qwik": {"dependencies": ["@builder.io/qwik"]},
    "Eleventy": {"dependencies": ["@11ty/eleventy"], "files": [".eleventy.js", "eleventy.config.*"]},
    "Hugo": {"files": ["hugo.toml", "hugo.yaml"]},
    "Jekyll": {"dependencies": ["jekyll", "github-pages"], "files": ["_config.yml"]},
    "Docusaurus": {"dependencies": ["@docusaurus/core"]},
    "VitePress / VuePress": {"dependencies": ["vitepress", "vuepress"]},
    "Vite": {"dependencies": ["vite"], "files": ["vite.config.*"]},
    "Express / Node server": {"dependencies": ["express", "koa", "fastify", "hono", "@nestjs/core"]},
    "Django": {"dependencies": ["django"], "files": ["manage.py"]},
    "Flask / FastAPI": {"dependencies": ["flask", "fastapi", "starlette"]},
    "Rails": {"dependencies": ["rails"], "files": ["config/routes.rb"]},
    "Laravel": {"dependencies": ["laravel/framework"], "files": ["artisan"]},
    "WordPress": {"files": ["wp-config.php", "functions.php"]},
    "Static HTML": {"files": ["index.html"]}
  },
  "signals": [
    {"id": "build-config", "label": "Build/bundler configuration file", "regex": "(?:^|/)(?:vite|webpack|rollup|next|nuxt|astro|svelte|gatsby-config|angular|tsconfig|babel|postcss|tailwind)[\\w.-]*\\.(?:js|cjs|mjs|ts|json)$", "files_only": true}
  ],
  "gaps": [],
  "edit_guidance": "Use manifests to know which libraries are available before adding new ones; edit the hosting config listed in deployment_config for server-level behaviour (redirects, headers, caching)."
}
```
