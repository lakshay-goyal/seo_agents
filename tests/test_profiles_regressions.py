"""Regression fixtures for every verified category-agent bug in docs/seo-agents-analysis.html §6-7."""

from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest

from seo_agents.exploration.repo_analysis.agents.ai_search_readiness import analyze_ai_search_readiness
from seo_agents.exploration.repo_analysis.agents.brand_entity_signals import analyze_brand_entity_signals
from seo_agents.exploration.repo_analysis.agents.canonicals_redirects import analyze_canonicals_redirects
from seo_agents.exploration.repo_analysis.agents.code_exploration import explore_code
from seo_agents.exploration.repo_analysis.agents.hreflang_i18n import analyze_hreflang_i18n
from seo_agents.exploration.repo_analysis.agents.https_security_headers import analyze_https_security_headers
from seo_agents.exploration.repo_analysis.agents.local_business_presence import analyze_local_business_presence
from seo_agents.exploration.repo_analysis.agents.mobile_parity import analyze_mobile_parity
from seo_agents.exploration.repo_analysis.agents.performance_assets import analyze_performance_assets
from seo_agents.exploration.repo_analysis.agents.react_diagnostics import diagnose_react
from seo_agents.exploration.repo_analysis.agents.rendering_strategy import analyze_rendering_strategy
from seo_agents.exploration.repo_analysis.agents.sitemap_robots import check_sitemap_and_robots
from seo_agents.exploration.repo_analysis.agents.structured_data import analyze_structured_data
from seo_agents.exploration.repo_analysis.tools.keywords import extract_keywords
from seo_agents.exploration.repo_analysis.tools.page_inventory import analyze_page_inventory
from seo_agents.exploration.repo_analysis.tools.repository_snapshot import snapshot_repository
from seo_agents.exploration.repo_analysis.tools.skill_runner import in_comment, run_skill
from seo_agents.exploration.repo_analysis.tools.source_index import SourceIndex


class Repo(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def index(self):
        return SourceIndex(snapshot_repository(self.repo))


class CommentDetection(Repo):
    def test_svelte_block_and_glob_string_do_not_open_comments(self):
        svelte = '{#if open}\n<a href="https://twitter.com/acme">x</a>\n{/if}'
        self.assertFalse(in_comment(svelte, svelte.index('https'), 'svelte'))
        js = "const g = 'src/**/*.js'\nconst u = 'https://x.com/acme'"
        self.assertFalse(in_comment(js, js.index('https'), 'javascript'))
        self.assertTrue(in_comment('/* https://x */', 4, 'javascript'))
        self.assertTrue(in_comment('{# x #}', 3, 'twig'))

    def test_svelte_if_block_keeps_social_profile(self):
        self.write('src/routes/+page.svelte', '{#if a}\n<a href="https://github.com/acme">gh</a>\n{/if}\n')
        self.assertEqual(analyze_brand_entity_signals(self.index())['social_profiles'], ['https://github.com/acme'])


class SitemapRobots(Repo):
    def test_nofollow_and_static_index_false_are_not_noindex(self):
        self.write('src/A.jsx', '<a rel="nofollow" href="/x">x</a>')
        self.write('server.js', "app.use(express.static(dir, { index: false }))")
        self.write('index.html', '<meta name="robots" content="noindex, follow">')
        result = check_sitemap_and_robots(self.index())
        self.assertEqual(result['signals']['noindex']['files'], ['index.html'])
        self.assertEqual([(d['source'], d['value'], d['noindex']) for d in result['robots_directives']],
                         [('meta-robots', 'noindex, follow', True)])

    def test_template_sitemap_is_generated_not_parse_error(self):
        self.write('layouts/sitemap.xml', '{{ printf "<?xml" }}<urlset>{{ range .Pages }}<url><loc>{{ .Permalink }}</loc></url>{{ end }}</urlset>')
        self.write('public/sitemap.xml', '<sitemapindex><sitemap><loc>https://a.com/s-0.xml</loc></sitemap></sitemapindex>')
        result = check_sitemap_and_robots(self.index())
        files = {s['file']: s for s in result['sitemap_files']}
        self.assertEqual(files['layouts/sitemap.xml']['generated'], 'template')
        self.assertEqual(files['public/sitemap.xml']['children'], ['https://a.com/s-0.xml'])
        self.assertNotIn('no-sitemap', [g['id'] for g in result['gaps']])


class CanonicalsRedirects(Repo):
    def test_canonical_value_comes_from_the_tag_not_neighbours(self):
        self.write('index.html', '<link rel="icon" href="/favicon.ico">\n<link rel="canonical" href="https://a.com/">\n'
                   '<a href="https://twitter.com/intent/tweet">t</a>\n')
        self.write('layouts/head.html', '<link rel="canonical" href="{{ .Permalink }}">')
        result = analyze_canonicals_redirects(self.index())
        self.assertEqual(result['canonical_values'], ['https://a.com/'])
        self.assertIn('expression', {c['value_origin'] for c in result['canonical_declarations']})

    def test_vercel_rewrite_is_not_a_redirect_and_flags_soft_404(self):
        self.write('vercel.json', '{"rewrites": [{"source": "/(.*)", "destination": "/index.html"}],'
                   ' "redirects": [{"source": "/old", "destination": "/new"}]}')
        result = analyze_canonicals_redirects(self.index())
        self.assertEqual([(r['from'], r['status']) for r in result['redirects']], [('/old', 308)])
        self.assertEqual(result['rewrites'][0]['kind'], 'rewrite')
        self.assertEqual(result['soft_404_risks'][0]['kind'], 'host-catch-all-rewrite')
        self.assertEqual(len(result['redirect_rules']), 2)

    def test_netlify_toml_htaccess_and_nginx(self):
        self.write('netlify.toml', '[[redirects]]\nfrom = "/a"\nto = "/b"\n\n[[redirects]]\nfrom = "/*"\nto = "/index.html"\nstatus = 200\n')
        self.write('.htaccess', 'RewriteRule ^x$ /y [L]\nRewriteRule ^old$ /new [R=301,L]\nRedirect /p /q\n')
        self.write('nginx.conf', 'location /blog {\n  return 301 https://a.com/news;\n}\nrewrite ^/a$ /b last;\n')
        rules = {(r['file'], r['from']): (r['kind'], r['status']) for r in analyze_canonicals_redirects(self.index())['redirect_rules']}
        self.assertEqual(rules[('netlify.toml', '/a')], ('redirect', 301))
        self.assertEqual(rules[('netlify.toml', '/*')], ('rewrite', 200))
        self.assertEqual(rules[('.htaccess', '^x$')], ('rewrite', 200))
        self.assertEqual(rules[('.htaccess', '^old$')], ('redirect', 301))
        self.assertEqual(rules[('.htaccess', '/p')], ('redirect', 302))
        self.assertEqual(rules[('nginx.conf', '/blog')], ('redirect', 301))
        self.assertEqual(rules[('nginx.conf', '^/a$')], ('rewrite', 200))

    def test_framework_redirects_and_aliases(self):
        self.write('next.config.js', "module.exports = { async redirects() { return [{ source: '/a', destination: '/b', permanent: false }] } }")
        self.write('astro.config.mjs', "export default { redirects: { '/old': '/new' } }")
        self.write('content/post.md', '---\ntitle: P\naliases: ["/legacy"]\n---\nBody')
        self.write('src/App.jsx', '<Route path="*" element={<NotFound />} />')
        result = analyze_canonicals_redirects(self.index())
        got = {(r['from'], r['status']) for r in result['redirects']}
        self.assertTrue({('/a', 307), ('/old', 301), ('/legacy', None)} <= got)
        self.assertIn('router-catch-all', {r['kind'] for r in result['soft_404_risks']})


class Hreflang(Repo):
    def test_expression_lang_is_not_a_value_and_case_insensitive_html(self):
        self.write('app/[locale]/layout.tsx', 'export default ({params}) => <html lang={params.locale}><body /></html>')
        self.write('pages/_document.tsx', '<Html lang="en"><Head /></Html>')
        self.write('next.config.js', "module.exports = { i18n: { locales: ['en', 'fr'], defaultLocale: 'en' } }")
        result = analyze_hreflang_i18n(self.index())
        self.assertEqual(result['html_lang_values'], ['en'])
        self.assertIn('expression', {d['value_origin'] for d in result['html_lang']})
        self.assertNotIn('no-html-lang', [g['id'] for g in result['gaps']])
        self.assertEqual(result['i18n_config'][0]['locales'], ['en', 'fr'])
        self.assertEqual(result['i18n_config'][0]['default'], 'en')


class StructuredData(Repo):
    def test_json_stringify_object_is_resolved(self):
        self.write('src/Seo.jsx', "const schema = {\n  '@context': 'https://schema.org',\n  '@type': ['Person'],\n"
                   "  name: 'Ada',\n  worksFor: { '@type': 'Organization', name: 'X' },\n};\n"
                   'export const S = () => <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(schema) }} />\n')
        result = analyze_structured_data(self.index())
        self.assertEqual(result['schema_types'], {'Person': 1})
        self.assertEqual(result['nested_schema_types'], {'Organization': 1})
        self.assertEqual(result['json_ld_blocks'][0]['parse_status'], 'resolved-object')

    def test_self_closing_script_does_not_swallow_following_markup(self):
        self.write('a.html', '<script type="application/ld+json" src="x" />\n<p>hi</p>\n'
                   '<script type="application/ld+json">{"@type":"WebSite","name":"a"}</script>')
        blocks = analyze_structured_data(self.index())['json_ld_blocks']
        self.assertEqual([b['top_level_types'] for b in blocks], [['WebSite']])

    def test_next_seo_component(self):
        self.write('pages/a.jsx', 'import { ArticleJsonLd } from "next-seo"\nexport default () => <ArticleJsonLd title="t" />')
        self.assertIn('Article', analyze_structured_data(self.index())['schema_types'])


class BrandAndLocal(Repo):
    def test_share_intents_and_repo_blobs_are_not_profiles(self):
        self.write('src/F.jsx', '<a href="https://twitter.com/intent/tweet?text=x">s</a><a href="https://github.com/a/b/blob/main/README.md">r</a>'
                   '<a href="https://github.com/acme">gh</a>')
        self.write('src/schema.js', 'export const s = {\n  sameAs: [\n    "https://github.com/acme",\n    "https://x.com/acme",\n'
                   '    "https://www.linkedin.com/in/acme",\n  ],\n}\n')
        result = analyze_brand_entity_signals(self.index())
        self.assertEqual(result['social_profiles'], ['https://github.com/acme', 'https://www.linkedin.com/in/acme', 'https://x.com/acme'])
        self.assertEqual(len(result['same_as_urls']), 3)  # whole multi-line array, not the first line
        self.assertEqual(result['profiles_missing_from_same_as'], [])

    def test_store_dirs_and_data_are_not_location_pages_and_gate(self):
        self.write('src/stores/cart.ts', 'export const useCart = defineStore("cart", {})')
        self.write('src/store/data/contact.json', '{"phone": "+1 555 123 4567"}')
        self.write('src/components/team/Avatar.tsx', 'export default () => null')
        index = self.index()
        local = analyze_local_business_presence(index)
        self.assertEqual(local['status'], 'not-applicable')
        self.assertEqual(run_skill(index, 'local-business-presence')['signals']['contact-or-location-page']['count'], 0)
        self.assertEqual(run_skill(index, 'brand-entity-signals')['signals']['about-page']['count'], 0)

    def test_local_business_telephone_and_email_values(self):
        self.write('index.html', '<script type="application/ld+json">{"@type": "Restaurant", "telephone": "+1 555 123 4567",'
                   ' "email": "hi@cafe.com", "address": {"@type": "PostalAddress"}}</script>')
        index = self.index()
        local = analyze_local_business_presence(index, analyze_structured_data(index))
        self.assertEqual(local['status'], 'applicable')
        self.assertIn('+1 555 123 4567', local['phone_values'])
        self.assertEqual(local['email_values'], ['hi@cafe.com'])


class SecurityHeaders(Repo):
    def test_meta_hsts_ignored_schema_context_not_insecure_and_vercel_hsts(self):
        self.write('index.html', '<meta http-equiv="Strict-Transport-Security" content="max-age=1">'
                   '<script type="application/ld+json">{"@context": "http://schema.org"}</script>')
        result = analyze_https_security_headers(self.index())
        self.assertEqual(result['signals']['hsts']['count'], 0)
        self.assertEqual(result['signals']['insecure-url']['count'], 0)
        self.assertIn('no-hsts', [g['id'] for g in result['gaps']])
        self.assertNotIn('no-csp', [g['id'] for g in result['gaps']])
        self.write('vercel.json', '{}')
        self.assertNotIn('no-hsts', [g['id'] for g in analyze_https_security_headers(self.index())['gaps']])


class PerformanceAndMobile(Repo):
    def test_jsdoc_import_is_not_code_splitting_and_heavy_webgl_dependency(self):
        self.write('tailwind.config.js', "/** @type {import('tailwindcss').Config} */\nexport default {}")
        self.write('package.json', '{"dependencies": {"react": "18", "@paper-design/shaders-react": "0.0.81"}}')
        self.write('src/Contact.jsx', "import { LiquidMetal } from '@paper-design/shaders-react'")
        self.write('public/old.png', 'x')
        self.write('public/used.png', 'x')
        self.write('src/Hero.jsx', '<img src="/used.png" />')
        index = self.index()
        project = explore_code(index)
        with redirect_stdout(io.StringIO()):
            result = analyze_performance_assets(index, analyze_page_inventory(index), project)
        self.assertEqual(result['signals']['code-splitting']['count'], 0)
        self.assertEqual(result['heavy_client_dependencies'], [{'name': '@paper-design/shaders-react', 'kind': 'webgl'}])
        self.assertEqual([a['path'] for a in result['legacy_format_assets']], ['public/used.png'])

    def test_no_viewport_gap_not_reported_on_next_and_favicon_signal(self):
        self.write('package.json', '{"dependencies": {"next": "14"}}')
        self.write('app/layout.tsx', "export const metadata = { icons: { icon: '/i.png' } }")
        index = self.index()
        result = analyze_mobile_parity(index, explore_code(index))
        self.assertNotIn('no-viewport', [g['id'] for g in result['gaps']])
        self.assertNotIn('no-favicon', [g['id'] for g in result['gaps']])


class RenderingAndAi(Repo):
    def test_cascade_config_layout_page(self):
        self.write('next.config.js', "module.exports = { output: 'export' }")
        self.write('app/page.tsx', 'export default () => <h1>x</h1>')
        index = self.index()
        routes = [{'route': '/', 'kind': 'page', 'source': {'location': {'file': 'app/page.tsx'}}}]
        result = analyze_rendering_strategy(index, routes, diagnose_react(index))
        row = result['route_modes'][0]
        self.assertEqual((row['route'], row['mode']), ('/', 'SSG'))
        self.assertEqual(row['source_chain'][0]['level'], 'config')

    def test_vite_spa_shell_is_csr_and_hash_router_folded(self):
        self.write('index.html', '<div id="root"></div><script type="module" src="/src/main.jsx"></script>')
        self.write('vite.config.js', 'export default {}')
        self.write('src/main.jsx', "createRoot(document.getElementById('root')).render(<HashRouter><a href=\"#\">x</a></HashRouter>)")
        index = self.index()
        result = analyze_rendering_strategy(index, [], diagnose_react(index))
        self.assertTrue(result['csr_only'])
        self.assertEqual(result['config_mode']['mode'], 'CSR')
        self.assertIn('hash-router', {h['signal'] for h in result['client_hazards_by_file']['src/main.jsx']})

    def test_ai_crawler_policy_from_robots_groups_not_ua_lists(self):
        self.write('src/bots.js', "export const BOTS = ['GPTBot', 'ClaudeBot']")
        index = self.index()
        result = analyze_ai_search_readiness(index, None, check_sitemap_and_robots(index))
        self.assertEqual(result['ai_crawler_policy'], [])
        self.assertIn('no-ai-crawler-policy', [g['id'] for g in result['gaps']])
        self.write('public/robots.txt', 'User-agent: GPTBot\nDisallow: /\n\nUser-agent: *\nAllow: /\n')
        index = self.index()
        policy = {p['bot']: p['policy'] for p in analyze_ai_search_readiness(index, None, check_sitemap_and_robots(index))['ai_crawler_policy']}
        self.assertEqual((policy['GPTBot'], policy['ClaudeBot']), ('disallow', 'allow'))


class ContentAndConfig(Repo):
    def test_readme_is_not_page_content_or_keywords(self):
        self.write('README.md', '# Deploying your site\nHey i\'m deploying your site with vercel today.\n')
        self.write('index.html', '<title>Green tea</title><h1>Green tea shop</h1>')
        index = self.index()
        with redirect_stdout(io.StringIO()):
            inventory = analyze_page_inventory(index)
            keywords = extract_keywords(inventory, [])
        self.assertNotIn('README.md', {h['location']['file'] for h in inventory['headings']})
        self.assertNotIn('deploying', ' '.join(r['keyword'] for r in keywords['unigrams']))

    def test_deployment_config_is_parsed(self):
        self.write('vercel.json', '{"headers": [{"source": "/(.*)", "headers": [{"key": "X-Robots-Tag", "value": "noindex"}]}],'
                   ' "rewrites": [{"source": "/(.*)", "destination": "/index.html"}]}')
        config = explore_code(self.index())['deployment_config']
        self.assertEqual(config['host'], 'vercel')
        self.assertEqual(config['headers'][0]['name'], 'X-Robots-Tag')
        self.assertEqual(config['rewrites'][0]['to'], '/index.html')


if __name__ == '__main__':
    unittest.main()
