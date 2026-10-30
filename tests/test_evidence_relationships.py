"""Ground truth for recoverable source lineage and independent HTTP evidence."""

import json
import unittest

from seo_agents.exploration.checks.agents.context import SourceHTML
from seo_agents.exploration.checks.evidence import query_evidence, source_relationships
from seo_agents.exploration.checks.technical.runtime import observe_runtime


class EvidenceRelationshipsTests(unittest.TestCase):
    def test_literal_html_preserves_headings_links_media_and_text_boundaries(self):
        parser = SourceHTML('index.html')
        parser.feed('<head><title>Tea &amp; coffee</title><link rel="canonical" href="/tea"></head>'
                    '<main><h1>Fresh <em>tea</em></h1><p>First <strong>crop</strong>.</p>'
                    '<a href="../shop?Sort=A#green" rel="ugc nofollow"><img src="tea.png" alt="Tea tin">'
                    'Buy <span>tea</span></a><script type>hidden()</script></main>')
        parser.close()
        kinds = [record['kind'] for record in parser.records]
        self.assertEqual(kinds.count('navigation-link'), 1)  # canonical is a different relationship
        heading = next(r for r in parser.records if r['kind'] == 'heading')
        self.assertEqual((heading['text'], heading['level']), ('Fresh tea', 1))
        link = next(r for r in parser.records if r['kind'] == 'navigation-link')
        self.assertEqual(link['raw_value'], '../shop?Sort=A#green')
        self.assertEqual(link['anchor_text'], 'Buy tea')
        self.assertEqual(link['image_alternatives'], ['Tea tin'])
        self.assertTrue(link['element_closed'])
        self.assertEqual(link['section'], 'main')
        texts = [r['text'] for r in parser.records if r['kind'] == 'literal-text']
        self.assertIn('First ', texts)
        self.assertIn('crop', texts)
        self.assertNotIn('hidden()', texts)
        media = next(r for r in parser.records if r['kind'] == 'media-declaration')
        self.assertEqual(media['attributes']['alt'], 'Tea tin')
        self.assertEqual(media['file'], 'index.html')

    def test_unclosed_anchor_retains_partial_evidence(self):
        parser = SourceHTML('broken.html')
        parser.feed('<a href="/next">Partial <b>copy')
        parser.close()
        record = next(r for r in parser.records if r['kind'] == 'navigation-link')
        self.assertEqual(record['anchor_text'], 'Partial copy')
        self.assertFalse(record['element_closed'])

    def test_inline_whitespace_and_empty_href_remain_observable(self):
        bundle = self.runtime_bundle()
        observe_runtime(bundle, site_url='https://example.test', fetcher=lambda *a, **kw: {
            'status': 'success', 'http_status': 200,
            'initial_html': '<title>Source title</title><a href>A<span> </span>B</a>'})
        summary = bundle['runtime']['responses'][0]['initial_html_summary']
        self.assertEqual(summary['titles'][0]['value'], 'Source title')
        link = summary['link_occurrences'][0]
        self.assertEqual(link['raw_value'], None)
        self.assertEqual(link['destination'], 'https://example.test/old')
        self.assertEqual(link['anchor_text'], 'A B')

    def test_reverse_lineage_keeps_observed_candidate_and_unassociated_distinct(self):
        files = {'pages/a.tsx': 'a', 'pages/b.tsx': 'b', 'shared/menu.json': '{}',
                 'unused~file/name.md': 'not proven unused'}
        snapshot = {'files': files, 'inventory': [{'path': f, 'kind': 'file', 'status': 'captured', 'roles': []} for f in files]}
        pages = [{'page_id': 'a', 'observed_source_files': ['pages/a.tsx', 'shared/menu.json'],
                  'dependency_candidate_files': []},
                 {'page_id': 'b', 'observed_source_files': ['pages/b.tsx'],
                  'dependency_candidate_files': ['shared/menu.json']}]
        relationships = source_relationships(snapshot, pages)
        shared = relationships['shared/menu.json']
        self.assertEqual(shared['directly_observed_page_ids'], ['a'])
        self.assertEqual(shared['dependency_candidate_page_ids'], ['b'])
        self.assertEqual(relationships['unused~file/name.md']['attribution'], 'not-associated-with-enumerated-pages')
        restored = query_evidence({'source_corpus': files}, relationships['unused~file/name.md']['corpus_ref'])
        self.assertEqual(json.loads(restored['json_text']), 'not proven unused')

    def runtime_bundle(self):
        return {'concrete_urls': [{'url': 'https://example.test/old', 'fetch': 'pending'}],
                'unresolved_route_families': [], 'coverage': {},
                'reports': {'metadata': {'pages': [{'url': '/old', 'template': 'old.html',
                                                   'declarations': [{'title': 'Source title'}]}]}}}

    def test_runtime_resolves_against_final_url_and_base_without_expanding_frontier(self):
        bundle = self.runtime_bundle()
        result = {'status': 'success', 'http_status': 200, 'final_url': 'https://example.test/catalog/new',
                  'initial_html': '<head><title>Runtime title</title><base href="/Store/">'
                      '<link rel="canonical" href="/canonical"></head><nav>Shared chrome</nav>'
                      '<main><h1>Tea</h1><p>Fresh tea.</p><a href="Item?Color=Green#info">Details</a>'
                      '<a href="Item?Color=Green#reviews">Reviews</a><a href="https://example.test.evil.test/">External</a>'
                      '<a href="http://[broken">Bad URI</a><img src="tea.jpg" alt="Tea">'
                      '<script type="application/ld+json">invalid</script></main>'}
        calls = []
        def fetcher(url, **kwargs):
            calls.append(url)
            return result
        observe_runtime(bundle, site_url='https://example.test', fetcher=fetcher)
        self.assertEqual(calls, ['https://example.test/old'])
        runtime = bundle['runtime']
        links = runtime['link_occurrences']
        self.assertEqual(len(links), 4)
        self.assertEqual(links[0]['destination'], 'https://example.test/Store/Item?Color=Green')
        self.assertEqual(links[0]['fragment'], 'info')
        self.assertEqual(links[2]['kind'], 'external')
        self.assertEqual(links[3]['kind'], 'unresolved')
        self.assertEqual(len(runtime['destinations']), 2)
        self.assertEqual(runtime['destinations'][0]['occurrence_indices'], [0, 1])
        self.assertEqual(runtime['source_comparisons'][0]['status'], 'observed-set-differs')
        self.assertEqual(runtime['source_comparisons'][0]['source_report_refs'], ['/reports/metadata/pages/0'])
        summary = runtime['responses'][0]['initial_html_summary']
        self.assertEqual(summary['non_chrome_word_count'], 8)  # excludes title, navigation and JSON-LD
        self.assertEqual(summary['record_counts']['structured-data'], 1)
        self.assertEqual(bundle['coverage']['initial_response_extraction']['new_internal_destinations_not_scheduled'],
                         ['https://example.test/Store/Item?Color=Green'])
        self.assertEqual(len(bundle['concrete_urls']), 1)
        self.assertIn('initial_html_evidence', runtime['responses'][0])

    def test_truncated_and_error_html_remain_noncomparable_with_content_retained(self):
        for status, truncated in [('success', True), ('http-error', False)]:
            with self.subTest(status=status):
                bundle = self.runtime_bundle()
                observe_runtime(bundle, site_url='https://example.test', fetcher=lambda *a, **kw: {
                    'status': status, 'http_status': 200 if status == 'success' else 500,
                    'body_truncated': truncated, 'initial_html': '<title>Partial title</title><p>Error or partial text'})
                response = bundle['runtime']['responses'][0]
                self.assertTrue(response['initial_html_evidence'])
                self.assertEqual(bundle['runtime']['source_comparisons'][0]['status'], 'not-comparable')
                self.assertIn('truncated-response-body' if truncated else 'unsuccessful-http-response',
                              bundle['runtime']['source_comparisons'][0]['limitations'])
                self.assertEqual(bundle['coverage']['initial_response_extraction']['partial_bodies'], int(truncated))


if __name__ == '__main__':
    unittest.main()
