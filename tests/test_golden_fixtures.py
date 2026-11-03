"""Golden fixtures: score the source-only site model on one tiny repo per framework."""

import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('score_fixtures', ROOT / 'scripts' / 'score_fixtures.py')
scorer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scorer)

try:
    from seo_agents.repo_analysis.site_model.site_model_builder import explore_site
except (ImportError, AttributeError):  # the site model is optional until it lands
    explore_site = None


class ScorerNormalisationTests(unittest.TestCase):
    def test_route_placeholders_and_suffixes_are_equivalent(self):
        for value in ('/blog/[slug]', '/blog/:slug', '/blog/{slug}', '/blog/<slug:slug>', '/blog/[...slug]/', '/blog/<slug>'):
            self.assertEqual(scorer.norm_url(value), '/blog/:param', value)
        self.assertEqual(scorer.norm_url('/about.html'), '/about')
        self.assertEqual(scorer.norm_url('/fr/index.html'), '/fr')
        self.assertEqual(scorer.norm_url('https://x.test/menu/?a=1#top'), '/menu')
        self.assertEqual(scorer.norm_url('/'), '/')
        self.assertEqual(scorer.norm_url(''), '/')

    def test_text_and_href_normalisation(self):
        self.assertEqual(scorer.norm_text('  Fresh&nbsp;coffee,\n delivered '), 'fresh coffee, delivered')
        self.assertIsNone(scorer.norm_text(None))
        self.assertEqual(scorer.norm_href('https://acme.test/'), 'https://acme.test')
        self.assertEqual(scorer.norm_href('/about/'), '/about')
        self.assertEqual(scorer.norm_href('mailto:a@b.c'), 'mailto:a@b.c')

    def test_every_fixture_has_valid_expectations(self):
        names = scorer.fixture_names()
        self.assertGreaterEqual(len(names), 12)
        for name in names:
            expected = json.loads((scorer.FIXTURES / name / 'expected.json').read_text())
            self.assertTrue(expected['framework'])
            self.assertTrue(expected['pages'])
            for page in expected['pages']:
                self.assertTrue(page['url'].startswith('/'))
                for target in page.get('attribution', {}).values():
                    path = scorer.FIXTURES / name / target['file']
                    self.assertTrue(path.is_file(), path)
                    self.assertLessEqual(target['line'], len(path.read_text().splitlines()))

    def test_scorer_handles_a_failing_explorer(self):
        def broken(path, render=False):
            raise RuntimeError('boom')
        result = scorer.score_fixture('static-html-i18n', broken)
        self.assertIn('boom', result['error'])
        self.assertEqual(result['scores']['urls']['recall'], 0)
        self.assertFalse(result['framework_match'])

    def test_scorer_rewards_a_perfect_model(self):
        expected = json.loads((scorer.FIXTURES / 'static-html-i18n' / 'expected.json').read_text())

        def perfect(path, render=False):
            pages = {}
            for index, row in enumerate(expected['pages']):
                fields = row['fields']
                attribution = row.get('attribution', {})
                head = {key: {'value': fields.get(key), 'edit': attribution.get(key)} for key in ('title', 'description', 'canonical')}
                heads = [{'level': 1, 'text': t, 'edit': attribution.get('h1')} for t in fields.get('h1', [])]
                heads += [{'level': l, 'text': t} for l, t in fields.get('headings', []) if l != 1]
                pages['p' + str(index)] = {'url': row['url'], 'head': head, 'headings': heads,
                                           'links': {'out': [{'href': h, 'anchor': a} for h, a in fields.get('links', [])]},
                                           'images': [{'src': s, 'alt': a} for s, a in fields.get('images', [])]}
            return {'site': {'framework': {'primary': expected['framework']}}, 'pages': pages}
        result = scorer.score_fixture('static-html-i18n', perfect)
        for key in ('urls', 'head', 'headings', 'links', 'images', 'attribution'):
            self.assertEqual(result['scores'][key]['recall'], 1.0, key)
        self.assertTrue(result['framework_match'])


@unittest.skipIf(explore_site is None, 'site_model.explore_site not available yet')
class GoldenFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results, cls.summary = scorer.run(explore=explore_site)

    def test_no_fixture_crashes(self):
        errors = {r['fixture']: r['error'] for r in self.results if r['error']}
        self.assertEqual(errors, {})

    def test_framework_fingerprint_matches_every_fixture(self):
        mismatches = {r['fixture']: (r['expected_framework'], r['framework']) for r in self.results if not r['framework_match']}
        self.assertEqual(mismatches, {})

    def test_overall_url_recall(self):
        self.assertGreaterEqual(self.summary['urls']['recall'], 0.98, json.dumps(
            {r['fixture']: [m for m in r['misses'] if m.get('kind') == 'missing-page'] for r in self.results}))

    def test_doc_accuracy_targets(self):
        # docs/seo-agents-analysis.html §15: >=98% URL recall, >=97% head fields, >=90% edit attribution.
        failing = {key: target for key, target in self.summary['targets'].items() if not target['pass']}
        self.assertEqual(failing, {}, json.dumps({r['fixture']: r['misses'] for r in self.results if r['misses']})[:4000])

    def test_structure_precision_and_recall(self):
        for key in ('headings', 'links', 'images'):
            self.assertGreaterEqual(self.summary[key]['recall'], 0.95, key)
            self.assertGreaterEqual(self.summary[key]['precision'], 0.95, key)
        self.assertGreaterEqual(self.summary['urls']['precision'], 0.95)


if __name__ == '__main__':
    unittest.main()
