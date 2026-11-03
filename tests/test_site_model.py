"""Site model: element index, adapters, manifest, lenses, attribution, gap workers, validators."""

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from seo_agents.repo_analysis.agents.gap_workers import (gap_answer_validation, gap_result_applier, gap_task_builder, gap_task_prompts, gap_task_solver, route_adapter_learner)
from seo_agents.repo_analysis.site_model.site_model_builder import explore_site, grounding_issue
from seo_agents.repo_analysis.tools.routing.framework_route_adapters import spa_routes, wordpress_routes, shopify_routes, rails_routes
from seo_agents.repo_analysis.tools.parsing.element_index import build_element_index, page_composition, resolve_module, load_aliases
from seo_agents.repo_analysis.tools.repository.framework_detection import detect_frameworks
from seo_agents.repo_analysis.site_model.url_manifest import build_manifest, normalize_path, pattern_regex

ENV = {'SEO_GAP_WORKERS': '0', 'SEO_LIGHTHOUSE': '0', 'SEO_RENDER': '0', 'SEO_BUILD': '0', 'SEO_SITE_URL': '',
       'SEO_GAP_CACHE': '0', 'OPENROUTER_API_KEY': ''}


def snap(files):
    return {'files': files, 'assets': {}, 'inventory': [{'path': p, 'kind': 'file'} for p in files], 'limitations': [],
            'sha256': 'test', 'coverage': {}}


class ElementIndexTests(unittest.TestCase):
    def test_template_directives_are_masked_with_exact_positions(self):
        files = {
            'sections/hero.liquid': '<section>\n  <h1 class="x">{{ "hero.title" | t }}</h1>\n  <a href="/shop">Shop {{ shop.name }}</a>\n</section>',
            'single.php': "<article>\n<h2><?php esc_html_e( 'Latest news', 'td' ); ?></h2>\n</article>",
            'templates/page.html': '{% extends "base.html" %}\n{% block content %}<p>{% trans "Hello" %} there</p>{% endblock %}',
            'resources/views/a.blade.php': "@extends('layouts.app')\n@section('content')\n<h3>Team</h3>\n@endsection",
            'locales/en.default.json': '{\n  "hero": {\n    "title": "Big sale"\n  }\n}',
        }
        index = build_element_index(snap(files))
        by = {(e['file'], e['tag']): e for e in index['elements']}
        h1 = by[('sections/hero.liquid', 'h1')]
        self.assertEqual((h1['line'], h1['col'], h1['heading_level']), (2, 2, 1))
        self.assertEqual(h1['text'][0]['i18n_keys'], ['hero.title'])
        link = by[('sections/hero.liquid', 'a')]
        self.assertEqual(link['attrs']['href']['literal'], '/shop')
        self.assertEqual(link['text'][0]['literal'], 'Shop')
        shop = link['text'][0]  # exact value span of the text run
        self.assertEqual(files['sections/hero.liquid'].splitlines()[shop['line'] - 1][shop['col']:shop['end_col']], 'Shop')
        self.assertEqual(by[('single.php', 'h2')]['text'][0]['i18n_keys'], ['Latest news'])
        self.assertEqual(by[('templates/page.html', 'p')]['text'][0]['i18n_keys'], ['Hello'])
        self.assertEqual(by[('resources/views/a.blade.php', 'h3')]['line'], 3)
        self.assertEqual(index['i18n']['hero.title'][0]['line'], 3)

    def test_jsx_props_components_and_aliases_resolve(self):
        files = {
            'tsconfig.json': '{ // comment\n "compilerOptions": {"baseUrl": ".", "paths": {"@ui/*": ["src/ui/*"]}} }',
            'src/ui/Title.tsx': 'export const Title = ({text}) => <Heading level={2}>{text}</Heading>;',
            'app/page.tsx': 'import { Title } from "@ui/Title";\nexport default () => <main><Title text="Plans" /><Typography variant="h1">Hi</Typography></main>;',
        }
        index = build_element_index(snap(files))
        title = next(e for e in index['elements'] if e['tag'] == 'Title')
        self.assertEqual(title['component_ref'], 'src/ui/Title.tsx')
        typography = next(e for e in index['elements'] if e['tag'] == 'Typography')
        self.assertEqual(typography['heading_level'], 1)
        heading = next(e for e in index['elements'] if e['tag'] == 'Heading')
        self.assertEqual(heading['heading_level'], 2)
        self.assertEqual(page_composition(index, 'app/page.tsx'), ['app/page.tsx', 'src/ui/Title.tsx'])
        self.assertEqual(resolve_module('app/page.tsx', '@ui/Title', files, load_aliases(files)), 'src/ui/Title.tsx')


class AdapterTests(unittest.TestCase):
    def test_nested_router_children_keep_parent_prefix(self):
        files = {'src/router.jsx': '''import Shell from "./Shell"; import Docs from "./Docs"; import Page from "./Page";
const router = createBrowserRouter([{ path: "/docs", element: <Shell />, children: [
  { index: true, element: <Docs /> }, { path: ":slug", element: <Page /> } ] }]);''',
                 'src/Shell.jsx': '', 'src/Docs.jsx': '', 'src/Page.jsx': ''}
        rows = {(r['url'], r['template']) for r in spa_routes(files)}
        self.assertIn(('/docs', 'src/Shell.jsx'), rows)
        self.assertIn(('/docs/:slug', 'src/Page.jsx'), rows)
        self.assertIn(('/docs', 'src/Docs.jsx'), rows)

    def test_wordpress_shopify_rails_conventions(self):
        wp = {r['url'] for r in wordpress_routes({'style.css': '/* Theme Name: T */', 'index.php': '', 'page-contact.php': '',
                                                  'single-recipe.php': '', 'archive-recipe.php': '', 'category.php': ''})}
        self.assertTrue({'/', '/contact', '/recipe/:slug', '/recipe', '/category/:category', '/:post', '/:page'} <= wp)
        shop = {(r['url'], r['kind']) for r in shopify_routes({'templates/product.alt.json': '{}', 'templates/cart.liquid': '',
                                                                'templates/page.json': '{}', 'templates/page.liquid': ''})}
        self.assertIn(('/products/:handle?view=alt', 'alternate-template'), shop)
        self.assertIn(('/pages/:handle', 'page'), shop)
        rails = {(r['url'], r['template']) for r in rails_routes({'config/routes.rb': "root 'home#index'\nresources :posts, only: [:index, :show]",
                                                                  'app/views/home/index.html.erb': '', 'app/views/posts/show.html.erb': '',
                                                                  'app/views/posts/index.html.erb': ''})}
        self.assertEqual(rails, {('/', 'app/views/home/index.html.erb'), ('/posts', 'app/views/posts/index.html.erb'),
                                 ('/posts/:id', 'app/views/posts/show.html.erb')})


class ManifestTests(unittest.TestCase):
    def test_union_keeps_provenance_and_maps_instances_to_patterns(self):
        files = {'next.config.js': '', 'package.json': '{"dependencies": {"next": "15.0.0"}}',
                 'app/page.tsx': 'export default () => <h1>Home</h1>',
                 'app/blog/[slug]/page.tsx': 'export default () => <h1>Post</h1>',
                 'content/blog/first.md': '---\ntitle: First\n---\nBody',
                 'public/sitemap.xml': '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://x.test/blog/second</loc></url>'
                                       '<url><loc>https://x.test/</loc></url><url><loc>https://x.test/legacy</loc></url></urlset>'}
        snapshot = snap(files)
        pages, notes = build_manifest(snapshot, detect_frameworks(snapshot))
        by = {p['url']: p for p in pages}
        self.assertIn('sitemap:public/sitemap.xml', by['/']['discovered_by'])
        self.assertEqual(by['/blog/first']['discovered_by'], ['content-collection'])
        self.assertEqual((by['/blog/second']['route'], by['/blog/second']['instance_status']), ('/blog/[slug]', 'concrete'))
        self.assertEqual(by['/legacy']['kind'], 'discovered')
        self.assertEqual(notes['unmatched_urls'][0]['url'], '/legacy')

    def test_normalisation_and_patterns(self):
        self.assertEqual(normalize_path('https://x.test/a/index.html'), '/a')
        self.assertEqual(normalize_path('/about.html'), '/about')
        self.assertIsNone(normalize_path('mailto:a@b.c'))
        self.assertTrue(pattern_regex('/blog/[...slug]').match('/blog/a/b'))
        self.assertTrue(pattern_regex('/dishes/<slug:slug>').match('/dishes/olive'))
        self.assertFalse(pattern_regex('/posts/{post}').match('/posts/a/b'))


@patch.dict(os.environ, ENV)
class SiteModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'site'
        self.repo.mkdir()

    def write(self, path, text):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def explore(self, **kwargs):
        with redirect_stdout(io.StringIO()):
            return explore_site(self.repo, **kwargs)

    def test_measured_raw_lens_attributes_crawls_and_checks_links(self):
        self.write('index.html', '<html lang="en"><head><title>Home</title><meta name="description" content="Welcome"></head>'
                                 '<body><nav><a href="/about.html">About</a><a href="/missing">Gone</a></nav><main><h1>Hello</h1></main></body></html>')
        self.write('about.html', '<html><head><title>About us</title></head><body><h1>About</h1><a href="/">Home</a></body></html>')
        output = Path(self.temp.name) / 'model'
        model = self.explore(measure=True, render=False, lighthouse=False, output_dir=output)
        home = next(p for p in model['pages'].values() if p['url'] == '/')
        self.assertEqual(home['lens'], 'raw-html')
        self.assertEqual(home['head']['title']['value'], 'Home')
        self.assertTrue(home['head']['title']['lens_agreement'])
        edit = home['head']['description']['edit']
        self.assertEqual({k: edit.get(k) for k in ('kind', 'file', 'line', 'col', 'attr')},
                         {'kind': 'literal', 'file': 'index.html', 'line': 1, 'col': 75, 'attr': 'content'})
        self.assertEqual(edit.get('end_col'), 82)  # value span of content="Welcome", not the <meta> tag start
        self.assertEqual(edit['verify'], 'grounded')
        self.assertEqual(home['links']['click_depth'], 0)
        about = next(p for p in model['pages'].values() if p['url'] == '/about')
        self.assertEqual(about['links']['click_depth'], 1)
        self.assertTrue(any(url.endswith('/missing') for url in model['site']['broken_links']))
        self.assertFalse(model['site']['served']['soft_404']['soft_404'])
        self.assertTrue((output / 'index.json').exists() and (output / 'site.json').exists())
        self.assertEqual(len(list((output / 'pages').glob('*.json'))), 2)
        self.assertEqual(model['site']['validation']['issue_count'], 0)

    def test_source_only_resolves_props_blocks_front_matter_and_missing_values(self):
        self.write('package.json', '{"dependencies": {"astro": "4.0.0"}}')
        self.write('astro.config.mjs', 'export default {}')
        self.write('src/layouts/Base.astro', '---\nconst { title } = Astro.props;\n---\n<html><head><title>{title}</title></head><body><slot /></body></html>')
        self.write('src/pages/index.astro', '---\nimport Base from "../layouts/Base.astro";\n---\n<Base title="Stars">\n<h1>See the stars</h1>\n</Base>')
        model = self.explore()
        page = next(iter(model['pages'].values()))
        self.assertEqual(page['head']['title']['value'], 'Stars')
        self.assertEqual(page['head']['title']['edit']['kind'], 'prop')
        self.assertEqual(page['head']['title']['edit']['file'], 'src/pages/index.astro')
        self.assertEqual(page['head']['description']['edit']['kind'], 'add')
        self.assertEqual(model['site']['framework']['primary'], 'astro')
        self.assertEqual(page['rendering']['method'], 'source-only')

    def test_component_rendered_twice_yields_one_heading_per_prop_usage(self):
        self.write('package.json', '{"dependencies": {"next": "15.0.0"}}')
        self.write('next.config.js', 'module.exports = {}')
        self.write('components/Hero.tsx', 'export default function Hero({title}) { return <h2>{title}</h2>; }')
        self.write('app/page.tsx', 'import Hero from "../components/Hero";\nexport default () => <><Hero title="One" />\n<Hero title="Two" /></>;')
        page = next(iter(self.explore()['pages'].values()))
        self.assertEqual([(h['text'], h['edit']['kind'], h['edit']['line']) for h in page['headings']], [('One', 'prop', 2), ('Two', 'prop', 3)])

    def test_ambiguous_attribution_is_resolved_by_validated_gap_worker_with_retry(self):
        calls = []

        class Model:
            def __init__(self, tier):
                self.tier = tier

            def invoke(self, messages):
                calls.append((self.tier, 'rejected' in messages[1].content))
                if len(calls) == 1:
                    return SimpleNamespace(content='{"candidate": "c9", "edit_kind": "prop"}')  # invalid id -> fresh retry
                return SimpleNamespace(content='{"candidate": "c2", "edit_kind": "literal", "reason": "page template"}')
        page = {'id': 'p1', 'url': '/', 'template': 'a.html', 'kind': 'page', 'instance_status': 'static', 'eeat': [],
                'head': {'title': {'value': 'Welcome', 'edit': {'kind': 'literal', 'file': 'b.html', 'line': 1}, 'confidence': 0.45,
                                   'candidates': [], 'gap': 'attribution-ambiguous'},
                         'description': {'value': None, 'edit': None}},
                'headings': [], 'content': {'blocks': []}, 'keywords': {}}
        gap = {'task': 'attribute_value', 'page_id': 'p1', 'url': '/', 'template': 'a.html', 'field': '/head/title', 'observed': 'Welcome',
               'candidates': [{'id': 'c1', 'kind': 'literal', 'file': 'b.html', 'line': 1, 'snippet': '<title>Welcome</title>'},
                              {'id': 'c2', 'kind': 'literal', 'file': 'a.html', 'line': 3, 'snippet': '<title>Welcome</title>'}]}
        ctx = SimpleNamespace(files={'a.html': '', 'b.html': ''}, index={'refs': [], 'data': []})
        with patch.object(gap_task_solver, 'default_factory', lambda tier, temperature=0, label=None: Model(tier)), \
                patch.dict(os.environ, {'SEO_TOPIC_MAX_PAGES': '0', 'SEO_EEAT_MAX_PAGES': '0'}):
            tasks = gap_task_builder.build_tasks([gap], [page], ctx, {'family': 'html'})
            self.assertEqual([t['task'] for t in tasks], ['attribute_value'])
            result = gap_task_solver.solve(tasks[0], 'html', model_factory=lambda tier, temperature=0, label=None: Model(tier))
        self.assertTrue(gap_result_applier.apply(tasks[0], result, page, ctx))
        self.assertEqual(calls, [('small', False), ('small', True)])
        edit = page['head']['title']['edit']
        self.assertEqual((edit['file'], edit['line'], edit['resolved_by']), ('a.html', 3, 'llm:small'))
        self.assertNotIn('candidates', page['head']['title'])

    def test_unknown_framework_learns_cached_declarative_adapter(self):
        self.write('site/pages/home.tpl', '<h1>Home</h1>')
        self.write('site/pages/team.tpl', '<h1>Team</h1>')
        self.write('site/partials/nav.tpl', '<nav></nav>')

        class Mid:
            def invoke(self, messages):
                return SimpleNamespace(content=json.dumps({'framework': 'tpl', 'rules': [
                    {'path_regex': r'^site/pages/(?P<route>.+)\.tpl$', 'url_template': '/{route}', 'index_names': ['home']}]}))
        with patch.object(route_adapter_learner, 'CACHE_DIR', Path(self.temp.name) / 'cache'), \
                patch.object(gap_task_solver, 'default_factory', lambda tier, temperature=0, label=None: Mid()):
            model = self.explore(run_gap_workers=True)
        urls = sorted(p['url'] for p in model['pages'].values())
        self.assertEqual(urls, ['/', '/team'])
        self.assertTrue(list((Path(self.temp.name) / 'cache').glob('*.json')))

    def test_grounding_flags_wrong_lines(self):
        files = {'a.html': '<title>One</title>\n<h1>Two</h1>'}
        self.assertIsNone(grounding_issue(files, {'kind': 'literal', 'file': 'a.html', 'line': 2}, 'Two'))
        self.assertEqual(grounding_issue(files, {'kind': 'literal', 'file': 'b.html', 'line': 1}, 'x'), 'edit file not captured')
        self.assertEqual(grounding_issue(files, {'kind': 'literal', 'file': 'a.html', 'line': 9}, 'x'), 'edit line out of range')
        self.assertEqual(grounding_issue(files, {'kind': 'weird', 'file': 'a.html', 'line': 1}, 'x'), 'unknown edit kind')


class GapValidationTests(unittest.TestCase):
    def test_validators_reject_out_of_contract_answers(self):
        task = {'task': 'topic_intent', 'packet': {}}
        with self.assertRaises(ValueError):
            gap_answer_validation.validate(task, {'primary_topic': 'x', 'intent': 'buy', 'target_queries': ['a', 'b', 'c']})
        with self.assertRaises(ValueError):
            gap_answer_validation.validate({'task': 'route_adapter', 'packet': {'files': ['tests/a.tpl']}},
                                 {'rules': [{'path_regex': r'^tests/(?P<route>.+)\.tpl$'}]})
        merged, agreement = gap_task_solver.majority([{'intent': 'local'}, {'intent': 'local'}, {'intent': 'commercial'}])
        self.assertEqual((merged['intent'], agreement), ('local', 0.67))

    def test_progressive_disclosure_keeps_family_examples(self):
        body, meta = gap_task_prompts.load_skill('attribute-value', 'liquid')
        self.assertIn('### liquid', body)
        self.assertNotIn('### jsx', body)
        self.assertEqual(meta['tier'], 'small')


if __name__ == '__main__':
    unittest.main()


class ReviewRegressionTests(unittest.TestCase):
    """Bugs found in independent review; each reproduced before the fix."""

    def test_entities_do_not_shift_directive_capture_and_columns_are_characters(self):
        files = {'a.html': '<h1>Tom &amp; Jerry {{ page.title }} more</h1>', 'b.tsx': 'const x = <div>Café — ü <a href="/x">y</a></div>'}
        index = build_element_index(snap(files))
        h1 = next(e for e in index['elements'] if e['tag'] == 'h1')
        self.assertEqual(h1['text'][1]['expr'], '{{ page.title }}')
        self.assertEqual(h1['text'][0]['literal'], 'Tom & Jerry')
        link = next(e for e in index['elements'] if e['file'] == 'b.tsx' and e['tag'] == 'a')
        self.assertEqual(link['col'], files['b.tsx'].index('<a'))

    def test_yaml_sequences_at_key_indent_and_exact_json_lines(self):
        from seo_agents.repo_analysis.tools.parsing.element_index import json_string_lines, yaml_strings
        self.assertEqual(list(yaml_strings('nav:\n- title: Home\n- title: About\n')),
                         [('/nav/0/title', 'Home', 2), ('/nav/1/title', 'About', 3)])
        self.assertEqual(json_string_lines('{"title":"Home",\n"nav":{\n"home":"Home"},\n"x":"title"}'),
                         [('/title', 'Home', 1), ('/nav/home', 'Home', 3), ('/x', 'title', 4)])

    def test_remix_optional_and_layout_opt_out_segments(self):
        from seo_agents.repo_analysis.tools.routing.framework_route_adapters import remix_routes
        rows = {(r['url'], r['is_dynamic']) for r in remix_routes({'app/routes/($lang)._index.tsx': '', 'app/routes/blog_.$slug.tsx': ''})}
        self.assertEqual(rows, {('/:lang?', True), ('/blog/:slug', True)})

    def test_manifest_specificity_substitution_and_own_hosts(self):
        from seo_agents.repo_analysis.site_model.url_manifest import specificity, substitute
        self.assertEqual(substitute('/blog/[slug]', 'a\\d'), '/blog/a\\d')
        self.assertLess(specificity('/blog/[slug]'), specificity('/[lang]/[page]'))
        self.assertLess(specificity('/[lang]/[page]'), specificity('/[...rest]'))
        self.assertEqual(normalize_path('https://example.com/blog/', 'http://127.0.0.1:5000', {'example.com'}), '/blog')
        self.assertIsNone(normalize_path('https://other.com/x', 'http://127.0.0.1:5000', {'example.com'}))

    def test_gap_answers_with_null_reason_apply_without_crashing(self):
        task = {'task': 'attribute_value', 'packet': {'candidates': [{'id': 'c1', 'file': 'a.html', 'line': 1}]}}
        answer = gap_answer_validation.validate(task, {'candidate': 'c1', 'edit_kind': 'literal', 'reason': None})
        self.assertEqual(answer['reason'], '')
        with self.assertRaises(ValueError):
            gap_answer_validation.validate(task, {'candidate': 'c1', 'edit_kind': 'literal', 'reason': 5})

    def test_unbuilt_source_shell_is_never_served(self):
        from seo_agents.repo_analysis.measurement.site_server import build_and_serve
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'public').mkdir()
            (root / 'public' / 'index.html').write_text('<link href="%PUBLIC_URL%/x.png"><div id="root"></div>')
            (root / 'package.json').write_text('{"dependencies": {"react-dom": "18"}}')
            served = build_and_serve(root, {'primary': 'react-spa', 'renderer': 'csr', 'package_json': 'package.json',
                                            'build': {'output_dirs': ['dist', 'build']}}, build=False)
            self.assertEqual(served.mode, 'source-only')
