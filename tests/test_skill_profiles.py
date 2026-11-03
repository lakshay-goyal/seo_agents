"""Deterministic skill profiles: schema, per-signal examples, scoping, truncation and adapters."""

from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import re
import tempfile
import unittest

from seo_agents.repo_analysis.repo_context.repo_context_builder import build_repo_context
from seo_agents.repo_analysis.repo_analysis import explore_codebase
from seo_agents.repo_analysis.tools.signal_profiles import signal_profile_runner as skill_runner
from seo_agents.repo_analysis.tools.content.page_inventory import analyze_page_inventory
from seo_agents.repo_analysis.tools.repository.repository_snapshot import snapshot_repository
from seo_agents.repo_analysis.tools.routing.route_discovery import discover_routes
from seo_agents.repo_analysis.tools.parsing.safe_xml_parser import XMLParseError, safe_xml_fromstring
from seo_agents.repo_analysis.tools.signal_profiles.signal_profile_runner import (
    PROFILES_DIR, ProfileError, load_signal_profiles, run_signal_profile, validate_profile)
from seo_agents.repo_analysis.tools.repository.source_index import SourceIndex


def compile_signal(signal):
    return re.compile(signal['regex'], re.M | (re.I if signal.get('ignore_case') else 0))


class ProfileSchemaTests(unittest.TestCase):
    def test_every_profile_file_validates_and_loads(self):
        names = {p.stem for p in PROFILES_DIR.glob('*.json')} - {'schema'}
        self.assertEqual(names, set(load_signal_profiles()))
        self.assertGreaterEqual(len(names), 18)
        schema = json.loads((PROFILES_DIR / 'schema.json').read_text())
        self.assertIn('signal', schema['$defs'])

    def test_every_signal_has_scope_precision_and_passing_examples(self):
        failures = []
        for name, entry in load_signal_profiles().items():
            for signal in entry['profile']['signals']:
                self.assertIn(signal['precision'], {'high', 'medium', 'low'})
                self.assertTrue(signal.get('files_only') or signal.get('glob') or signal.get('languages'), name + '.' + signal['id'])
                regex = compile_signal(signal)
                for example in signal['examples']['match']:
                    if not regex.search(example):
                        failures.append(name + '.' + signal['id'] + ' should match ' + repr(example))
                for example in signal['examples']['no_match']:
                    if regex.search(example):
                        failures.append(name + '.' + signal['id'] + ' should not match ' + repr(example))
        self.assertEqual(failures, [])

    def test_execution_guidance_is_not_exploration_output(self):
        for entry in load_signal_profiles().values():
            self.assertNotIn('edit_guidance', entry['profile'])

    def test_validator_rejects_unscoped_or_unknown_gap_signals(self):
        profile = copy.deepcopy(load_signal_profiles()['mobile-parity']['profile'])
        profile['signals'][0].pop('languages', None)
        profile['signals'][0].pop('glob', None)
        with self.assertRaises(ProfileError):
            validate_profile(profile)
        profile = copy.deepcopy(load_signal_profiles()['mobile-parity']['profile'])
        profile['gaps'][0]['when_none_of'] = ['does-not-exist']
        with self.assertRaises(ProfileError):
            validate_profile(profile)
        profile = copy.deepcopy(load_signal_profiles()['mobile-parity']['profile'])
        profile['signals'][0]['precision'] = 'certain'
        with self.assertRaises(ProfileError):
            validate_profile(profile)

    def test_skill_docs_no_longer_embed_runtime_profiles(self):
        for doc in (PROFILES_DIR.parent / 'skills').glob('*/SKILL.md'):
            self.assertNotIn('```json profile', doc.read_text(), doc)


class RepoTestCase(unittest.TestCase):
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


class SignalBehaviourTests(RepoTestCase):
    def test_noise_from_appendix_is_gone(self):
        self.write('src/store.ts', 'source$.subscribe(v => v)\nconst priority = 1\nprovide(this.x)\n'
                   'const v = "1.2.3456.7890"\nonClick={() => track(Date.now())}\n')
        self.write('README.md', 'Set noindex on staging. <details>FAQ-ish</details>\n')
        self.write('src/Faq.vue', '<template><details><summary>More</summary></details></template>')
        index = self.index()
        for skill_name, signal in (('shareability-link-assets', 'newsletter'), ('performance-assets', 'priority-image'),
                                   ('local-business-presence', 'phone'),
                                   ('sitemap-robots', 'noindex'),
                                   ('ai-search-readiness', 'faq-section')):
            self.assertEqual(run_signal_profile(index, skill_name)['signals'][signal]['count'], 0, skill_name + '.' + signal)

    def test_comments_and_package_manifests_do_not_count_as_social_profiles(self):
        self.write('package.json', '{"homepage": "https://github.com/acme/site"}')
        self.write('src/Footer.tsx', '// see https://twitter.com/old\nexport const F = () => <a href="https://twitter.com/acme">X</a>\n')
        records = run_signal_profile(self.index(), 'brand-entity-signals')['signals']['social-profile']['records']
        self.assertEqual([r['matched_text'] for r in records], ['https://twitter.com/acme'])

    def test_truncation_is_reported_when_caps_hit(self):
        self.write('index.html', '<h2>What is green tea?</h2>\n' * 40)
        signal = run_signal_profile(self.index(), 'ai-search-readiness')['signals']['question-heading']
        self.assertEqual(signal['count'], skill_runner.MAX_RECORDS_PER_FILE)
        self.assertEqual(signal['total_matches'], 40)
        self.assertTrue(signal['truncated'])
        self.assertIn(signal['precision'], {'high', 'medium'})

    def test_signals_cover_non_javascript_stacks(self):
        self.write('layout/theme.liquid', '<html lang="{{ request.locale.iso_code }}"><head><title>{{ page_title }}</title>'
                   '{{ content_for_header }}</head><body>{{ product | structured_data }}</body></html>')
        self.write('wp/header.php', "<html <?php language_attributes(); ?>><head><?php wp_head(); ?></head>"
                   "<p><?php _e('Hello', 'td'); ?></p><?php the_post_thumbnail('large'); ?>")
        self.write('layouts/_default/baseof.html', '<head>{{ partial "head.html" . }}</head><main>{{ .Content }}</main>')
        index = self.index()
        metadata = run_signal_profile(index, 'metadata')['signals']['template-block']['files']
        self.assertTrue({'layout/theme.liquid', 'wp/header.php', 'layouts/_default/baseof.html'} <= set(metadata))
        self.assertEqual(len(run_signal_profile(index, 'hreflang-i18n')['signals']['html-lang']['files']), 2)
        self.assertIn('wp/header.php', run_signal_profile(index, 'performance-assets')['signals']['image-optimizer']['files'])
        self.assertIn('layout/theme.liquid', run_signal_profile(index, 'structured-data')['signals']['json-ld-helper']['files'])
        content = run_signal_profile(index, 'content')['signals']['platform-content']['files']
        self.assertIn('layouts/_default/baseof.html', content)

    def test_xml_entities_are_rejected(self):
        with self.assertRaises(XMLParseError):
            safe_xml_fromstring('<!DOCTYPE x [<!ENTITY a "aaaa">]><x>&a;</x>')
        self.assertEqual(safe_xml_fromstring('<urlset><url><loc>/a</loc></url></urlset>').tag, 'urlset')


class RouteAdapterTests(RepoTestCase):
    def routes(self):
        with redirect_stdout(io.StringIO()):
            return {(r['route'], r['kind'], r['adapter']) for r in discover_routes(self.index())}

    def test_shopify_theme_templates_map_to_storefront_urls(self):
        for name in ('layout/theme.liquid', 'templates/index.json', 'templates/product.json', 'templates/page.contact.json',
                     'templates/customers/login.liquid', 'templates/404.json'):
            self.write(name, '{}')
        routes = self.routes()
        self.assertIn(('/products/:handle', 'page', 'shopify-theme'), routes)
        self.assertIn(('/pages/:handle', 'page', 'shopify-theme'), routes)
        self.assertIn(('/account/login', 'page', 'shopify-theme'), routes)
        self.assertIn(('/404', 'not-found', 'shopify-theme'), routes)

    def test_wordpress_template_hierarchy(self):
        for name in ('functions.php', 'index.php', 'single.php', 'page-about.php', 'archive-product.php', 'header.php',
                     'template-parts/content.php'):
            self.write(name, '<?php ?>')
        routes = self.routes()
        self.assertIn(('/:post', 'page', 'wordpress-template-hierarchy'), routes)
        self.assertIn(('/about', 'page', 'wordpress-template-hierarchy'), routes)
        self.assertIn(('/product', 'page', 'wordpress-template-hierarchy'), routes)
        self.assertFalse(any('template-parts' in route for route, _, _ in routes))

    def test_nested_router_children_keep_parent_prefix(self):
        self.write('src/App.tsx', 'import {Routes, Route} from "react-router-dom";\nexport default () => <Routes>'
                   '<Route path="/" element={<Layout />}><Route path="blog" element={<B />}>'
                   '<Route path=":slug" element={<P />} /></Route></Route></Routes>')
        self.write('src/app/app.routes.ts', "export const routes: Routes = [{ path: 'shop', children: [{ path: ':id', component: Item }] }];")
        routes = {route for route, _, _ in self.routes()}
        self.assertTrue({'/blog', '/blog/:slug', '/shop', '/shop/:id'} <= routes)

    def test_django_include_prefixes_and_rails_resources(self):
        self.write('mysite/urls.py', "urlpatterns = [path('blog/', include('blog.urls'))]")
        self.write('blog/urls.py', "urlpatterns = [path('<slug:slug>/', views.post)]")
        self.write('config/routes.rb', "Rails.application.routes.draw do\n  root 'home#index'\n  namespace :admin do\n"
                   "    resources :users, only: [:index]\n  end\nend\n")
        routes = {route for route, _, _ in self.routes()}
        self.assertTrue({'/blog/<slug:slug>/', '/', '/admin/users'} <= routes)

    def test_next_base_path_and_locales(self):
        self.write('next.config.js', "module.exports = { basePath: '/docs', i18n: { locales: ['en', 'fr'], defaultLocale: 'en' } }")
        self.write('app/about/page.tsx', 'export default () => null')
        with redirect_stdout(io.StringIO()):
            row = next(r for r in discover_routes(self.index()) if r['source']['location']['file'] == 'app/about/page.tsx')
        self.assertEqual((row['route'], row['file_route'], row['locale_variants']), ('/docs/about', '/about', ['/docs/fr/about']))


class InventoryAndContextTests(RepoTestCase):
    def test_text_with_expressions_and_heading_components_are_kept(self):
        self.write('src/Hero.tsx', 'export const H = () => <section><Heading level={1}>Simple pricing</Heading>'
                   '<Typography variant="h2">Plans</Typography><p>Hello {name}, welcome back</p><a href="/x">Pricing</a></section>')
        with redirect_stdout(io.StringIO()):
            inventory = analyze_page_inventory(self.index())
        levels = {(h['level'], h['text']) for h in inventory['headings']}
        self.assertTrue({(1, 'Simple pricing'), (2, 'Plans')} <= levels)
        texts = {b['text']: b for b in inventory['text_blocks']}
        self.assertTrue(texts['Hello , welcome back']['has_expressions'])
        self.assertIn('Pricing', texts)  # one-word link label

    def test_context_defaults_to_references_and_honest_rendering_verdict(self):
        self.write('index.html', '<html><head><title>Home</title></head><body><h1>Hello world</h1>\n' + '<p>line</p>\n' * 30 + '</body></html>')
        with redirect_stdout(io.StringIO()):
            index = self.index()
            context = build_repo_context(index, explore_codebase(index), repo_path=self.repo)
        self.assertEqual(context['content_mode'], 'none')
        self.assertNotIn('content', context['files']['index.html'])
        self.assertNotIn('edit_guide', context)
        self.assertNotIn('skills', context)
        route = context['agents']['routes'][0]
        # Compact reference form: location + value only; no code, hashes, pointers or ids.
        self.assertEqual(route['source']['file'], 'index.html')
        for key in ('context', 'code', 'code_sha256', 'file_ref', 'id', 'location'):
            self.assertNotIn(key, route['source'])
        self.assertIn('coverage', context)
        self.assertIn('ai_search_readiness.llms-txt', context['coverage']['absent'])
        self.assertNotIn('root', context['repository'])
        self.assertNotIn(str(self.repo), json.dumps(context))
        self.assertIsNone(context['agents']['rendering_strategy'].get('initial_html_contains_content'))  # null is omitted
        self.assertEqual(context['agents']['rendering_strategy']['per_route_verdict'], 'requires-measurement')


if __name__ == '__main__':
    unittest.main()
