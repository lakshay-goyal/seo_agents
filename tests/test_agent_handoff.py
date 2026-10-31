"""Task-specific prior context stays optional, source-backed and recoverable."""

import json
import unittest

from seo_agents.exploration.checks.agents.handoff import PRIOR_WORK, build_handoff, handoff_summary
from seo_agents.exploration.checks.agents.retrieval import OUTPUT_BYTES, encoded_size, repository_tools
from seo_agents.exploration.checks.graph import CHECKS


class AgentHandoffTests(unittest.TestCase):
    def setUp(self):
        self.reports = {key: {'pages': [{'url': '/', 'template': 'index.html', 'is_dynamic': False,
            'links': [{'file': 'index.html', 'line': 2, 'source_id': 'index.html:2:0:a', 'href': '/about'}]}]}
            for key in CHECKS}
        self.runs = {key: {'framework': 'html', 'notes': 'Entry and shared layout. ' * 4000,
            'inspected_files': ['index.html'], 'script': 'NEVER SHARE THIS CODE', 'previous_errors': ['old error']}
            for key in CHECKS}

    def tools(self, category):
        handoff = build_handoff(category, self.reports, self.runs)
        return {tool.name: tool for tool in repository_tools({'files': {}, 'assets': {}}, handoff=handoff)}

    def test_context_only_includes_relevant_completed_predecessors_and_no_scripts(self):
        for index, category in enumerate(CHECKS):
            with self.subTest(category=category):
                self.assertTrue(set(PRIOR_WORK[category]) <= set(CHECKS[:index]))
                selected = build_handoff(category, self.reports, self.runs)
                self.assertEqual(set(selected), set(PRIOR_WORK[category]))
                self.assertNotIn('NEVER SHARE THIS CODE', json.dumps(selected))
                self.assertNotIn('old error', json.dumps(selected))
                summary = handoff_summary(selected)
                self.assertLess(encoded_size(summary), OUTPUT_BYTES)
                self.assertNotIn('index.html:2:0:a', json.dumps(summary))
        metadata = build_handoff('metadata', self.reports, self.runs)
        self.assertNotIn('media', metadata)
        self.assertNotIn('internal_links', metadata)
        self.assertEqual(build_handoff('content', {}, {}), {})

    def test_prior_report_queries_filter_page_and_field_and_block_irrelevant_reports(self):
        self.reports['internal_links']['pages'].append({'url': '/other', 'template': 'other.html',
            'links': [{'source_id': 'other.html:1:0:a', 'href': '/'}]})
        tools = self.tools('content')
        query = tools['query_prior_report']
        result = query.invoke({'category': 'internal_links', 'url': '/', 'template': 'index.html', 'field': 'links'})
        self.assertEqual(result['total'], 1)
        row = result['records'][0]
        self.assertEqual(row['observation']['source_id'], 'index.html:2:0:a')
        recovered = tools['read_prior_report'].invoke({'category': 'internal_links', 'pointer': row['pointer']})
        self.assertEqual(json.loads(recovered['json_text']), row['observation'])
        self.assertIn('error', query.invoke({'category': 'eeat'}))
        self.assertIn('error', tools['read_prior_report'].invoke({'category': 'eeat'}))
        summaries = query.invoke({'category': 'internal_links'})
        self.assertEqual(summaries['records'][0]['fields']['links'], 1)
        self.assertNotIn('observation', summaries['records'][0])

    def test_oversized_evidence_and_notes_are_recoverable_without_prompt_dumping(self):
        observation = self.reports['internal_links']['pages'][0]['links'][0]
        observation['anchor_text'] = '長い本文' * 10_000
        tools = self.tools('content')
        result = tools['query_prior_report'].invoke({'category': 'internal_links', 'field': 'links'})
        self.assertLessEqual(encoded_size(result), OUTPUT_BYTES)
        row = result['records'][0]
        self.assertTrue(row['oversized_record'])
        for pointer, expected in [(row['pointer'], observation), ('/notes', self.runs['internal_links']['notes'])]:
            parts, offset = [], 0
            for unused in range(100):
                chunk = tools['read_prior_report'].invoke({'category': 'internal_links', 'pointer': pointer, 'offset': offset})
                self.assertLessEqual(encoded_size(chunk), OUTPUT_BYTES)
                parts.append(chunk['json_text'])
                if chunk['next_offset'] is None:
                    break
                offset = chunk['next_offset']
            else:
                self.fail('Prior context did not finish within bounded continuation calls')
            self.assertEqual(json.loads(''.join(parts)), expected)

    def test_prior_report_pagination_and_invalid_pointers(self):
        links = self.reports['internal_links']['pages'][0]['links']
        links.extend({'source_id': 'index.html:' + str(index + 3) + ':0:a'} for index in range(80))
        tools = self.tools('content')
        rows, offset = [], 0
        while True:
            result = tools['query_prior_report'].invoke({'category': 'internal_links', 'field': 'links', 'offset': offset})
            rows.extend(result['records'])
            if result['next_offset'] is None:
                break
            self.assertGreater(result['next_offset'], offset)
            offset = result['next_offset']
        self.assertEqual(len(rows), len(links))
        for pointer in ('report', '/report/pages/-1', '/report/pages/00', '/missing', '/notes/~2'):
            self.assertIn('error', tools['read_prior_report'].invoke({'category': 'internal_links', 'pointer': pointer}))


if __name__ == '__main__':
    unittest.main()
