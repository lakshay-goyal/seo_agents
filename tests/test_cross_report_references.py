"""Reference integrity is checked per page and never repaired by fuzzy matching."""

import copy
import json
import unittest

from seo_agents.exploration.checks.agents.references import CrossReportReferenceError, validate_content_references


class CrossReportReferenceTests(unittest.TestCase):
    def setUp(self):
        self.content = {'pages': [{'url': '/', 'template': 'src/App.jsx',
            'link_refs': [{'source_id': 'src/components/PageRail.jsx:2:19:a', 'report': 'internal_links.json'}],
            'media_refs': []}]}
        self.reports = {'internal_links': {'pages': [{'url': '/', 'template': 'src/App.jsx',
            'links': [{'source_id': 'src/components/PageRail.jsx:14:12:a'}]}]},
            'media': {'pages': [{'url': '/', 'template': 'src/App.jsx', 'items': []}]}}

    def test_data_row_reference_is_rejected_with_element_candidates_without_remapping(self):
        original = copy.deepcopy(self.content)
        with self.assertRaises(CrossReportReferenceError) as caught:
            validate_content_references(self.content, self.reports)
        issue = caught.exception.issues[0]
        self.assertEqual(issue['pointer'], '/pages/0/link_refs/0')
        self.assertEqual(issue['url'], '/')
        self.assertEqual(issue['candidate_source_ids'], ['src/components/PageRail.jsx:14:12:a'])
        self.assertEqual(issue['expected_report'], 'internal_links.json')
        self.assertEqual(self.content, original)
        self.assertIn('preserve the content blocks', str(caught.exception))

    def test_source_id_on_another_page_or_template_does_not_resolve_the_reference(self):
        self.reports['internal_links']['pages'][0]['links'][0]['source_id'] = self.content['pages'][0]['link_refs'][0]['source_id']
        for field, value in [('url', '/about'), ('template', 'other/App.jsx')]:
            with self.subTest(field=field):
                reports = copy.deepcopy(self.reports)
                reports['internal_links']['pages'][0][field] = value
                with self.assertRaises(CrossReportReferenceError):
                    validate_content_references(self.content, reports)

    def test_correct_ids_and_filenames_resolve_for_links_and_media(self):
        self.content['pages'][0]['link_refs'][0]['source_id'] = 'src/components/PageRail.jsx:14:12:a'
        self.content['pages'][0]['media_refs'] = [{'source_id': 'src/App.jsx:9:0:img', 'report': 'media.json'}]
        self.reports['media']['pages'][0]['items'] = [{'source_id': 'src/App.jsx:9:0:img'}]
        validate_content_references(self.content, self.reports, require_targets=True)
        self.content['pages'][0]['media_refs'][0]['report'] = 'internal_links.json'
        with self.assertRaises(CrossReportReferenceError) as caught:
            validate_content_references(self.content, self.reports)
        self.assertEqual(caught.exception.issues[0]['pointer'], '/pages/0/media_refs/0')
        self.assertEqual(caught.exception.issues[0]['reason'], 'wrong report filename')

    def test_large_unicode_diagnostics_are_bounded_and_cannot_hide_rejection(self):
        self.content['pages'] *= 100
        for page in self.content['pages']:
            page['url'] = '/' + '茶' * 1000
            page['template'] = '茶' * 1000 + '.jsx'
            page['link_refs'][0]['source_id'] = '茶' * 1000 + ':2:19:a'
        with self.assertRaises(CrossReportReferenceError) as caught:
            validate_content_references(self.content, self.reports)
        self.assertTrue(caught.exception.issues)
        self.assertLess(len(str(caught.exception)), 2000)
        self.assertTrue(json.loads(json.dumps(caught.exception.issues)))

    def test_missing_predecessors_are_optional_only_outside_full_pipeline(self):
        validate_content_references(self.content, {})
        with self.assertRaisesRegex(ValueError, 'Missing cross-report target'):
            validate_content_references(self.content, {}, require_targets=True)
        self.content['pages'][0]['link_refs'][0]['source_id'] = ['invalid']
        with self.assertRaisesRegex(ValueError, 'source_id and report must be strings'):
            validate_content_references(self.content, self.reports)


if __name__ == '__main__':
    unittest.main()
