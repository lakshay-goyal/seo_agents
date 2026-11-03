"""Viewport, zoom restrictions, responsive images, favicon/manifest and mobile-hidden content."""

from seo_agents.exploration.repo_analysis.tools.skill_runner import evaluate_gaps, run_skill

# These frameworks inject `<meta name="viewport" content="width=device-width, initial-scale=1">` by default.
DEFAULT_VIEWPORT = {'next': 'Next.js', 'nuxt': 'Nuxt', '@sveltejs/kit': 'SvelteKit', 'gatsby': 'Gatsby',
                    '@remix-run/react': None, '@docusaurus/core': 'Docusaurus', 'vitepress': 'VitePress'}


def framework_viewport(project):
    deps = {(d if isinstance(d, str) else d.get('name') or '').lower() for d in (project or {}).get('dependencies', []) or []}
    return next((label for dep, label in DEFAULT_VIEWPORT.items() if dep in deps and label), None)


def analyze_mobile_parity(index, project=None):
    result = run_skill(index, 'mobile-parity')
    queries = result['signals'].get('media-queries')
    if queries:
        result['media_query_count'] = queries['total_matches']
        queries['records'] = []  # a count, not one record per CSS rule
    injected = framework_viewport(project)
    if injected:
        result['viewport_default'] = injected + ' injects a default viewport meta tag'
        return evaluate_gaps(result, ['viewport'])
    return result
