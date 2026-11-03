"""Regression tests for verified framework-detection, route-discovery and snapshot bugs (audit §5-§7)."""

import json
import tempfile
import unittest
from pathlib import Path

from seo_agents.exploration.repo_analysis.tools import adapters
from seo_agents.exploration.repo_analysis.tools.framework import detect_frameworks
from seo_agents.exploration.repo_analysis.tools.head_recipes import head_recipe, head_scope
from seo_agents.exploration.repo_analysis.tools.react_source import discover_app
from seo_agents.exploration.repo_analysis.tools.repository_snapshot import snapshot_repository
from seo_agents.exploration.repo_analysis.tools.routes import normalize_route, parse_object_routes
from seo_agents.exploration.repo_analysis.url_manifest import build_manifest, catch_all_rewrites, merge_suggested_instances

FIXTURES = Path(__file__).parent / 'fixtures' / 'sites'
REACT_PKG = json.dumps({'dependencies': {'react': '18.3.1', 'react-dom': '18.3.1'}, 'devDependencies': {'vite': '5.4.0'}})


def fingerprint(files):
    return detect_frameworks({'files': files, 'inventory': [], 'assets': {}})


def urls(rows):
    return sorted(r['url'] for r in rows if r['kind'] not in {'gap', 'not-found'})


class FrameworkDetectionTests(unittest.TestCase):
    def test_root_config_json_is_not_hugo(self):
        result = fingerprint({'package.json': REACT_PKG, 'config.json': '{"theme": "dark"}', 'index.html': '<div id="root"></div>'})
        self.assertEqual(result['primary'], 'react-spa')
        self.assertNotIn('hugo', [c['name'] for c in result['candidates']])

    def test_real_hugo_site_is_still_hugo(self):
        result = fingerprint({'config.toml': 'baseURL = "https://x.test/"\ntheme = "ananke"\n', 'content/_index.md': '# Hi'})
        self.assertEqual(result['primary'], 'hugo')

    def test_scripts_urls_py_is_not_django(self):
        result = fingerprint({'package.json': REACT_PKG, 'scripts/urls.py': 'URLS = ["https://a.test"]'})
        self.assertNotIn('django', [c['name'] for c in result['candidates']])
        self.assertEqual(fingerprint({'manage.py': '', 'site/urls.py': 'urlpatterns = []'})['primary'], 'django')

    def test_theme_json_alone_is_not_a_wordpress_block_theme(self):
        result = fingerprint({'package.json': REACT_PKG, 'src/theme.json': '{"colors": {}}'})
        self.assertNotIn('wordpress-block-theme', [c['name'] for c in result['candidates']])

    def test_monorepo_lists_every_site(self):
        result = fingerprint({'package.json': '{"workspaces": ["apps/*"]}',
                              'apps/web/package.json': json.dumps({'dependencies': {'next': '14.2.0'}}),
                              'apps/web/app/page.tsx': 'export default function P(){return <h1>Web</h1>}',
                              'apps/docs/package.json': json.dumps({'dependencies': {'@docusaurus/core': '3.0.0'}}),
                              'apps/docs/docs/intro.md': '# Intro'})
        self.assertEqual(result['primary'], 'next')
        self.assertEqual({(s['root'], s['primary']) for s in result['sites']}, {('apps/web', 'next'), ('apps/docs', 'docusaurus')})
        rows, name = adapters.adapter_routes({**{'apps/web/app/page.tsx': '', 'apps/docs/docs/intro.md': '# Intro'}}, result)
        self.assertIn('/', urls(rows))
        self.assertIn('/intro', urls(rows))
        self.assertIn('@apps/docs', name)


class RouteDiscoveryTests(unittest.TestCase):
    def test_pathname_switch_spa_finds_every_route(self):
        snapshot = snapshot_repository(FIXTURES / 'pathname-spa')
        pages, notes = build_manifest(snapshot, detect_frameworks(snapshot))
        found = {p['url'] for p in pages}
        self.assertTrue({'/', '/about', '/bookshelf', '/blog', '/blog/:slug', '/blog/deploying-with-vercel'} <= found, found)
        self.assertTrue(any(r['kind'] == 'host-rewrite' for r in notes['soft_404_risk']))
        self.assertTrue(any(r['kind'] == 'client-catch-all' for r in notes['soft_404_risk']))
        about = next(p for p in pages if p['url'] == '/about')
        self.assertIn('nav-literal', about['discovered_by'])

    def test_readme_is_documentation_not_content(self):
        snapshot = snapshot_repository(FIXTURES / 'pathname-spa')
        self.assertIn('README.md', snapshot['doc_files'])
        row = next(r for r in snapshot['inventory'] if r['path'] == 'README.md')
        self.assertIn('doc', row['roles'])

    def test_next_intercepting_and_parallel_routes_are_not_urls(self):
        rows = adapters.next_routes({'app/(.)photo/[id]/page.tsx': '', 'app/photo/[id]/page.tsx': '', 'app/@modal/page.tsx': '',
                                     'app/(marketing)/about/page.tsx': ''})
        self.assertEqual(urls(rows), ['/about', '/photo/[id]'])
        self.assertIsNone(normalize_route('(.)photo/[id]/page'))

    def test_next_locale_param_is_marked_and_expanded(self):
        files = {'app/[locale]/about/page.tsx': '', 'middleware.ts': "createMiddleware({locales: ['en', 'de'], defaultLocale: 'en'})"}
        rows = adapters.next_routes(files)
        self.assertEqual(rows[0]['i18n_param'], 'locale')
        pages, _ = build_manifest({'files': files, 'inventory': [], 'assets': {}}, {'primary': 'next'})
        self.assertTrue({'/en/about', '/de/about'} <= {p['url'] for p in pages})

    def test_api_path_object_is_not_a_route(self):
        files = {'src/config.js': "export const endpoints = [{ path: '/api/v1' }, { path: '/api/v2', method: 'GET' }]",
                 'src/router.jsx': "import Home from './Home'\nconst router = createBrowserRouter([{ path: '/', element: <Home /> }])",
                 'src/Home.jsx': 'export default function Home(){return <h1>Home</h1>}'}
        self.assertEqual(urls(adapters.spa_routes(files)), ['/'])
        self.assertEqual(parse_object_routes(files['src/config.js']), [])
        self.assertEqual([r[2] for r in parse_object_routes("[{ path: '/', element: <Home/> }]")], ['/'])

    def test_route_constants_resolve_and_unresolved_paths_are_gaps(self):
        files = {'src/paths.js': "export const P = { ABOUT: '/about', BLOG: '/blog' };",
                 'src/router.jsx': ("import { P } from './paths'\nimport About from './About'\nimport Blog from './Blog'\n"
                                    "createBrowserRouter([{ path: P.ABOUT, element: <About /> }, { path: `${P.BLOG}/:slug`, element: <Blog /> },"
                                    " { path: routes[key], element: <About /> }])"),
                 'src/About.jsx': 'export default function About(){return null}', 'src/Blog.jsx': 'export default function Blog(){return null}'}
        rows = adapters.spa_routes(files)
        self.assertEqual(urls(rows), ['/about', '/blog/:slug'])
        self.assertTrue(any(r['kind'] == 'gap' and r['gap'] == 'unresolved-route-path' for r in rows))

    def test_literal_slug_outside_generator_is_not_a_page(self):
        source = ("const featured = { slug: 'not-a-page' };\n"
                  "export async function generateStaticParams() { return [{ slug: 'hello' }, { slug: 'world' }]; }")
        self.assertEqual(adapters.static_params({'app/blog/[slug]/page.tsx': source}, 'app/blog/[slug]/page.tsx', '/blog/[slug]'),
                         ['hello', 'world'])

    def test_static_params_map_over_imported_json(self):
        files = {'app/blog/[slug]/page.tsx': ("import posts from '../../../data/posts.json'\n"
                                              "export function generateStaticParams() { return posts.map(p => ({ slug: p.slug })) }"),
                 'data/posts.json': json.dumps([{'slug': 'a'}, {'slug': 'b'}])}
        self.assertEqual(adapters.static_params(files, 'app/blog/[slug]/page.tsx', '/blog/[slug]'), ['a', 'b'])

    def test_gatsby_create_page_with_nested_context(self):
        snapshot = snapshot_repository(FIXTURES / 'gatsby')
        rows = adapters.gatsby_routes(snapshot['files'])
        created = next(r for r in rows if r['adapter'] == 'gatsby-create-page')
        self.assertEqual((created['url'], created['template']), ('/posts/:param', 'src/templates/post.js'))

    def test_remix_v1_nested_and_v2_folder_routes(self):
        rows = adapters.remix_routes({'app/routes/blog/$slug.tsx': '', 'app/routes/blog/index.tsx': '', 'app/routes/_index.tsx': '',
                                      'app/routes/posts.$id/route.tsx': '', 'app/routes/posts.$id/byline.tsx': '',
                                      'app/routes/__auth/login.tsx': '', 'app/routes/api.health.ts': ''})
        self.assertEqual(urls(rows), ['/', '/blog', '/blog/:slug', '/login', '/posts/:id'])

    def test_front_matter_yaml_lists_folded_toml_and_json(self):
        fields = adapters.front_matter('---\ntitle: "A: B"\naliases:\n  - /old\n  - /older\ndescription: >\n  one\n  two\n---\nbody')
        self.assertEqual(fields, {'title': 'A: B', 'aliases': ['/old', '/older'], 'description': 'one two'})
        self.assertEqual(adapters.front_matter('+++\ntitle = "Hugo"\nslug = "x"\n+++\n')['slug'], 'x')
        self.assertEqual(adapters.front_matter('{"title": "J", "permalink": "/j/"}\nbody')['permalink'], '/j/')

    def test_hugo_aliases_become_declared_redirects(self):
        files = {'config.toml': 'baseURL = "https://x.test/"', 'content/post.md': '---\ntitle: P\naliases:\n  - /old-post\n---\n'}
        pages, notes = build_manifest({'files': files, 'inventory': [], 'assets': {}}, {'primary': 'hugo'})
        self.assertEqual(notes['declared_redirects'][0]['from'], '/old-post')

    def test_laravel_prefix_groups_resources_and_views(self):
        source = ("<?php\nRoute::view('/about', 'about');\nRoute::prefix('blog')->group(function () {\n"
                  "    Route::get('/{post}', [PostController::class, 'show']);\n});\nRoute::resource('photos', PhotoController::class);\n")
        rows = adapters.laravel_routes({'routes/web.php': source, 'routes/api.php': "Route::get('/users', fn() => []);",
                                        'resources/views/about.blade.php': ''})
        self.assertEqual(urls(rows), ['/about', '/blog/{post}', '/photos', '/photos/{photo}'])

    def test_express_mounts_and_far_render_calls(self):
        snapshot = snapshot_repository(FIXTURES / 'express')
        rows = adapters.express_routes(snapshot['files'])
        self.assertEqual(urls(rows), ['/', '/menu', '/menu/:dish'])

    def test_rails_nested_resources(self):
        source = ('Rails.application.routes.draw do\n  root "pages#home"\n  resources :articles do\n'
                  '    resources :comments, only: [:index]\n  end\nend\n')
        self.assertIn('/articles/:article_id/comments', urls(adapters.rails_routes({'config/routes.rb': source})))

    def test_gap_suggested_instances_feed_back_into_the_manifest(self):
        files = {'app/blog/[slug]/page.tsx': 'export default function P(){}'}
        pages, _ = build_manifest({'files': files, 'inventory': [], 'assets': {}}, {'primary': 'next'})
        merged, added = merge_suggested_instances(pages, ['/blog/new-post', {'url': '/unknown/x'}])
        self.assertEqual([p['url'] for p in added], ['/blog/new-post'])
        self.assertEqual(added[0]['discovered_by'], ['gap-suggested'])
        self.assertEqual(added[0]['route'], '/blog/[slug]')

    def test_catch_all_rewrite_is_a_soft_404_risk(self):
        found = catch_all_rewrites({'vercel.json': '{"rewrites": [{"source": "/(.*)", "destination": "/index.html"}]}',
                                    'public/_redirects': '/*    /index.html   200'})
        self.assertEqual({f['host'] for f in found}, {'vercel', 'netlify'})


class StaticAppTests(unittest.TestCase):
    def repo(self, files):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for name, content in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(content)
        return root

    def test_static_site_with_script_src_does_not_crash(self):
        root = self.repo({'index.html': '<h1>Hi</h1><script src="/js/app.js"></script>',
                          'js/app.js': 'document.querySelector("h1").textContent = "Hello";'})
        self.assertIsNone(discover_app(root))

    def test_vue_vite_app_does_not_crash(self):
        root = self.repo({'index.html': '<div id="app"></div><script type="module" src="/src/main.js"></script>',
                          'src/main.js': "import { createApp } from 'vue'\nimport App from './App.vue'\ncreateApp(App).mount('#app')",
                          'src/App.vue': '<template><h1>Vue</h1></template>'})
        self.assertIsNone(discover_app(root))


class SnapshotTests(unittest.TestCase):
    def repo(self, files):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for name, content in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_bytes(content if isinstance(content, bytes) else content.encode())
        return root

    def test_assets_do_not_starve_source(self):
        files = {f'public/img/{i:03d}.ico': b'\x00\x01' for i in range(30)}
        files.update({'src/App.jsx': 'export default 1', 'src/main.jsx': 'x'})
        snapshot = snapshot_repository(self.repo(files), max_files=2)
        self.assertEqual(set(snapshot['files']), {'src/App.jsx', 'src/main.jsx'})
        self.assertEqual(len(snapshot['assets']), 30)

    def test_build_output_is_skipped_and_hugo_public_only_for_hugo(self):
        snapshot = snapshot_repository(self.repo({'out/index.html': '<h1>built</h1>', '_site/a.html': 'x', 'public/robots.txt': 'User-agent: *',
                                                  'index.html': '<h1>src</h1>'}))
        self.assertEqual(set(snapshot['files']), {'index.html', 'public/robots.txt'})
        hugo = snapshot_repository(self.repo({'config.toml': 'baseURL = "https://x.test/"', 'public/index.html': 'built',
                                              'content/_index.md': '# Home'}))
        self.assertNotIn('public/index.html', hugo['files'])

    def test_env_files_stay_excluded_but_public_url_hints_survive(self):
        snapshot = snapshot_repository(self.repo({'.env.production': 'VITE_SITE_URL=https://www.example.com\nAPI_KEY=sk-secret\n'
                                                                     'DATABASE_URL=https://user:pw@db.example.com/x\n',
                                                  'index.html': '<h1>x</h1>'}))
        self.assertNotIn('.env.production', snapshot['files'])
        self.assertEqual(snapshot['env_hints'], {'.env.production': 'VITE_SITE_URL=https://www.example.com\n'})
        self.assertNotIn('sk-secret', json.dumps(snapshot))


class HeadRecipeTests(unittest.TestCase):
    def test_spa_shell_is_shared_and_needs_prerender(self):
        recipe = head_recipe({'primary': 'react-spa', 'build': {'scripts': ['build', 'dev']}})
        self.assertEqual(recipe['scope'], 'shared-shell')
        self.assertIn('prerender', recipe['precondition'])
        self.assertEqual(head_scope({'primary': 'react-spa', 'build': {'scripts': ['build', 'prerender']}}), 'per-route')

    def test_meta_frameworks_are_per_route(self):
        self.assertEqual(head_scope({'primary': 'next', 'router': 'app'}), 'per-route')
        self.assertIn('generateMetadata', head_recipe({'primary': 'next', 'router': 'app'})['mechanism'])
        self.assertIn('next/head', head_recipe({'primary': 'next', 'router': 'pages'})['mechanism'])
        self.assertEqual(head_scope('wordpress-theme'), 'mixed')


if __name__ == '__main__':
    unittest.main()
