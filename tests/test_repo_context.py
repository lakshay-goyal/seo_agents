"""Framework-agnostic repository context: references plus exact source content."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from seo_agents.repo_analysis.tools.repository.repository_snapshot import snapshot_repository
from seo_agents.repo_analysis.repo_context.repo_context_builder import build_repo_context
from seo_agents.repo_analysis.repo_analysis import explore_codebase
from seo_agents.repo_analysis.tools.repository.source_index import SourceIndex
from seo_agents.repo_analysis.tools.routing.route_discovery import normalize_route
from seo_agents.repo_analysis.tools.signal_profiles.signal_profile_runner import load_signal_profiles
from seo_agents.repo_analysis.repo_analysis import repo_analysis


class RepoContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def build(self, include_content='none'):
        with redirect_stdout(io.StringIO()):
            index = SourceIndex(snapshot_repository(self.repo))
            return build_repo_context(index, explore_codebase(index), repo_path=self.repo, include_content=include_content)

    def test_every_skill_profile_loads(self):
        skills = load_signal_profiles()
        for name in ('routes', 'metadata', 'content', 'keywords', 'rendering-strategy', 'performance-assets',
                     'sitemap-robots', 'structured-data', 'ai-search-readiness'):
            self.assertIn(name, skills)

    def test_route_normalisation(self):
        self.assertEqual(normalize_route('(marketing)/about/'), '/about')
        self.assertEqual(normalize_route('blog/index'), '/blog')
        self.assertEqual(normalize_route('blog.$slug', dot_segments=True), '/blog/:slug')
        self.assertEqual(normalize_route('_index', dot_segments=True), '/')

    def test_app_router_records_carry_reference_and_code(self):
        self.write('package.json', '{"dependencies": {"next": "14", "react": "18"}}')
        self.write('app/layout.tsx', "export const metadata = {\n  title: 'Tea Shop',\n  description: 'Organic jasmine tea.',\n}\n"
                   'export default function L({children}) { return <html lang="en"><body>{children}</body></html> }\n')
        self.write('app/blog/[slug]/page.tsx', "export async function generateMetadata({ params }) {\n"
                   "  return { title: 'Post' }\n}\nexport default function P() {\n"
                   '  return <main><h1>Jasmine tea guide</h1><img src="/hero.png" /></main>\n}\n')
        context = self.build('referenced')
        routes = {(r['route'], r['kind']) for r in context['agents']['routes']}
        self.assertIn(('/blog/[slug]', 'page'), routes)
        self.assertIn(('/', 'layout'), routes)
        generator = context['agents']['page_inventory']['metadata']['apis']['signals']['metadata-generator']['records'][0]
        self.assertEqual(generator['location']['file'], 'app/blog/[slug]/page.tsx')
        self.assertIn("return { title: 'Post' }", generator['code'])  # function body, not only params
        heading = context['agents']['page_inventory']['headings'][0]
        self.assertEqual(heading['text'], 'Jasmine tea guide')
        self.assertIn('<h1>Jasmine tea guide</h1>', heading['code'])
        file_ref = heading['location']['file_ref']
        self.assertEqual(file_ref, '/files/app~1blog~1[slug]~1page.tsx')
        self.assertEqual(context['files']['app/blog/[slug]/page.tsx']['content'],
                         (self.repo / 'app/blog/[slug]/page.tsx').read_text())
        image = context['agents']['page_inventory']['media'][0]
        self.assertIn('missing-alt', image['issues'])
        self.assertIn('missing-dimensions (CLS)', image['issues'])
        page = next(p for p in context['pages'] if p['route'] == '/blog/[slug]')
        self.assertEqual(page['inherited_from_layouts'][0]['declares']['title'], ['Tea Shop'])
        self.assertIn('Next.js', context['summary']['framework_hints'])

    def test_non_javascript_codebase(self):
        self.write('requirements.txt', 'django==5.0\n')
        self.write('shop/urls.py', "urlpatterns = [path('products/<slug:slug>/', views.product)]\n")
        self.write('shop/templates/base.html', '<html lang="fr"><head><title>{% block title %}Shop{% endblock %}</title>'
                   '</head><body><h1>Fromage</h1></body></html>')
        self.write('public/robots.txt', 'User-agent: GPTBot\nDisallow: /\n')
        context = self.build()
        self.assertIn('/products/<slug:slug>/', [r['route'] for r in context['agents']['routes']])
        robots = context['agents']['sitemap_robots']['robots_files'][0]
        self.assertEqual(robots['groups'][0]['user_agents'], ['GPTBot'])
        self.assertTrue(context['agents']['ai_search_readiness']['signals']['ai-crawler-rule']['count'])
        self.assertEqual(context['agents']['hreflang_i18n']['html_lang_values'], ['fr'])
        self.assertIn('no-sitemap', [g['id'] for g in context['gaps']])

    def test_keywords_and_structured_data(self):
        self.write('index.html', '<html><head><title>Jasmine green tea</title>'
                   '<meta name="description" content="Fresh jasmine green tea from Fujian.">'
                   '<script type="application/ld+json">{"@context":"https://schema.org","@type":"FAQPage"}</script>'
                   '</head><body><h1>Jasmine green tea</h1><p>Our jasmine green tea is rolled by hand.</p></body></html>')
        context = self.build()
        self.assertIn('jasmine green tea', context['agents']['keywords']['primary_candidates'])
        example = context['agents']['keywords']['trigrams'][0]['examples'][0]
        self.assertEqual(example, 'index.html:1')
        self.assertNotIn('occurrences', context['agents']['keywords']['trigrams'][0])
        sd = context['agents']['structured_data']
        self.assertEqual(sd['schema_types'], {'FAQPage': 1})
        self.assertEqual(sd['json_ld_blocks'][0]['parse_status'], 'valid-json')

    def test_repo_analysis_writes_json_without_model(self):
        self.write('index.html', '<title>Home</title><h1>Hello world</h1>')
        output = self.repo.parent / (self.repo.name + '-context.json')
        self.addCleanup(lambda: output.unlink(missing_ok=True))
        with redirect_stdout(io.StringIO()):
            state = repo_analysis(repo_path=self.repo, output_path=output, run_agents=False)
        saved = json.loads(output.read_text())
        self.assertEqual(saved['schema_version'], 'repo-context/1.0')
        self.assertEqual(state['repo_context_path'], str(output))
        self.assertIn('index.html', saved['files'])

    def test_evidence_ids_are_unique(self):
        self.write('a.html', '<h1>One</h1><h1>Two</h1>')
        index = SourceIndex(snapshot_repository(self.repo))
        first = index.evidence('heading', 'a.html', 0, 5)
        second = index.evidence('heading', 'a.html', 12, 17)
        self.assertNotEqual(first['id'], second['id'])


if __name__ == '__main__':
    unittest.main()
