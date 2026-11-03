"""SKILL.md vs profile drift (docs/seo-agents-analysis.html §8).

Every backticked token in `repo_analysis/skills/<name>/SKILL.md` must appear in `profiles/<name>.json`
(signal ids, regexes, examples) or be listed in COVERED_BY_PYTHON for that skill. Allowlisted output keys
(snake_case identifiers) must also appear in the skill's Python modules, so a documented field cannot
silently disappear from the code.
"""

import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).parents[1] / 'src' / 'seo_agents' / 'exploration' / 'repo_analysis'
TOKEN = re.compile(r'`([^`\n]+)`')
IGNORED_PREFIXES = ('profiles/', 'tests/', 'agents/')
IGNORED = {'COVERED_BY_PYTHON'}

PYTHON = {
    'ai-search-readiness': ['agents/ai_search_readiness.py', 'agents/sitemap_robots.py'],
    'brand-entity-signals': ['agents/brand_entity_signals.py'],
    'canonicals-redirects': ['agents/canonicals_redirects.py'],
    'client-diagnostics': ['agents/rendering_strategy.py'],
    'code-exploration': ['agents/code_exploration.py', 'tools/project.py'],
    'content': ['tools/page_inventory.py'],
    'hreflang-i18n': ['agents/hreflang_i18n.py'],
    'https-security-headers': ['agents/https_security_headers.py'],
    'keywords': ['tools/keywords.py', 'tools/source_index.py'],
    'local-business-presence': ['agents/local_business_presence.py'],
    'metadata': ['tools/page_inventory.py'],
    'mobile-parity': ['agents/mobile_parity.py'],
    'performance-assets': ['agents/performance_assets.py'],
    'rendering-strategy': ['agents/rendering_strategy.py'],
    'routes': ['tools/routes.py', 'tools/adapters.py'],
    'shareability-link-assets': [],
    'sitemap-robots': ['agents/sitemap_robots.py'],
    'structured-data': ['agents/structured_data.py'],
}

COVERED_BY_PYTHON = {
    'ai-search-readiness': {'ai_crawler_policy', 'robots.txt', 'structure_by_file'},
    'brand-entity-signals': {'profiles_missing_from_same_as'},
    'canonicals-redirects': {
        '<link rel="canonical">', 'alternates.canonical', 'value_origin: expression',
        '{from, to, status, kind: redirect|rewrite, file, line}', 'redirect_rules', 'netlify.toml', 'vercel.json',
        'firebase.json', 'staticwebapp.config.json', '[R]', 'return 301', 'next.config', 'rewrites()', 'routeRules',
        'redirects:', 'createRedirect', 'aliases', 'redirect_from', 'soft_404_risks', '/(.*)', '/index.html', 'error.vue'},
    'client-diagnostics': {'#/', 'client_hazards_by_file'},
    'code-exploration': {'{host, redirects, rewrites, headers}', 'netlify.toml', 'firebase.json', 'staticwebapp.config.json'},
    'content': {'<h6>', '<a href>', '<Link to|href>', '<NuxtLink>', '<RouterLink>', '<img>', '<Image>', '<picture>/<source>',
                '<video>', '<iframe>', '{expressions}'},
    'hreflang-i18n': {'html_lang', 'lang={expr}', 'value_origin: expression', '<Html lang>', '_document', 'i18n_config',
                      '{locales, default, strategy}', 'next.config', 'defineRouting'},
    'https-security-headers': {'<meta http-equiv>'},
    'keywords': {'docs/', 'file:line', 'by_file', '<meta name="keywords">', 'tags'},
    'local-business-presence': {'status: not-applicable'},
    'metadata': {'<meta name|property>', '<link rel>', '<html lang>', "@section('title')", 'content_for :title', 'title:',
                 'description:'},
    'mobile-parity': set(),
    'performance-assets': {"import('x')", 'large_assets', 'legacy_format_assets', 'heavy_client_dependencies'},
    'rendering-strategy': {"output: 'export'", 'route_modes', '{mode, source_chain}', 'csr_only', 'config_mode'},
    'routes': {'is_dynamic', '[...slug]', 'layout/theme.liquid', 'templates/product.json', 'page.<variant>', 'customers/login',
               'functions.php', 'theme.json', 'page-{slug}', 'single-{cpt}', 'archive-{cpt}', '_index', '(group)', '@slot',
               '[param]', ':param', '$param', '{param}', 'blog.$slug', '/blog/:slug', '<Route>', '{ path, children: [...] }',
               'include()', 'namespace', 'scope', 'Route::get', 'basePath', 'i18n.locales', 'next.config.*', 'file_route',
               'locale_variants'},
    'shareability-link-assets': {'opengraph-image.*', 'twitter-image.*'},
    'sitemap-robots': {'sitemap*.xml', 'generated: template', 'lastmod', 'express.static({index: false})'},
    'structured-data': {'dangerouslySetInnerHTML={{__html: JSON.stringify(obj)}}', 'set:html', 'next-seo', 'define*()', '| json',
                        '@graph', 'schema_types', 'nested_schema_types'},
}


class SkillDocDriftTests(unittest.TestCase):
    def docs(self):
        return {p.parent.name: p.read_text() for p in sorted((ROOT / 'skills').glob('*/SKILL.md'))}

    def test_every_documented_token_is_in_profile_or_python(self):
        failures = []
        for name, text in self.docs().items():
            profile = (ROOT / 'profiles' / (name + '.json')).read_text()
            allowed = COVERED_BY_PYTHON.get(name, set())
            for token in dict.fromkeys(TOKEN.findall(text)):
                if token in IGNORED or token.startswith(IGNORED_PREFIXES):
                    continue
                if token in profile or json.dumps(token)[1:-1] in profile or token in allowed:
                    continue
                failures.append(f'{name}: `{token}` is neither in profiles/{name}.json nor in COVERED_BY_PYTHON')
        self.assertEqual(failures, [])

    def test_allowlisted_output_keys_exist_in_python(self):
        failures = []
        for name, tokens in COVERED_BY_PYTHON.items():
            source = ''.join((ROOT / path).read_text() for path in PYTHON[name] if (ROOT / path).exists())
            for token in tokens:
                if re.fullmatch(r'[a-z][a-z0-9]*(?:_[a-z0-9]+)+', token) and token not in source:
                    failures.append(f'{name}: documented key `{token}` not found in {PYTHON[name]}')
        self.assertEqual(failures, [])

    def test_allowlist_has_no_stale_entries(self):
        docs = self.docs()
        stale = [f'{name}: {token}' for name, tokens in COVERED_BY_PYTHON.items() for token in tokens
                 if '`' + token + '`' not in docs.get(name, '')]
        self.assertEqual(stale, [])

    def test_every_skill_doc_has_a_profile(self):
        for name in self.docs():
            self.assertTrue((ROOT / 'profiles' / (name + '.json')).exists(), name)


if __name__ == '__main__':
    unittest.main()
