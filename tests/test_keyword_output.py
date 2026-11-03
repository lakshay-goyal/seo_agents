"""KeywordOutputWriter: CSV rows for every record (unknown = blank, reasons kept, quoting), keyword_map.json shape."""

import csv
import json
from pathlib import Path
import tempfile
import unittest

from seo_agents.keyword_research.contracts import (SCHEMA_VERSION, BusinessProfile, Decision, Intent, JudgeVerdict,
                                                   KeywordCandidate, KeywordEvidence, KeywordKind, KeywordRecord, Market,
                                                   Metric, PageBrief, PageKeywordPlan, Provenance, RejectionCode,
                                                   ResearchTargets, SerpSnapshot, Source, Tail)
from seo_agents.keyword_research.mapping.keyword_mapper import MappingResult
from seo_agents.keyword_research.output.keyword_output_writer import KeywordOutputWriter

PROV = Provenance('dataforseo', 'keywords_data', '2026-11-01T10:00:00+00:00')
ADS = Provenance('google_ads', 'ideas', '2026-11-02T10:00:00+00:00')


def rec(keyword, verdict, metrics=None, status='rejected', serp=None, mapped=None):
    candidate = KeywordCandidate(keyword=keyword, page_id='p1', kind=KeywordKind.SECONDARY, source=Source.LLM_SEED,
                                 tail=Tail.LONG, rationale='fits "pricing", page')
    evidence = KeywordEvidence(keyword=keyword, metrics=metrics or {}, serp=serp)
    return KeywordRecord(candidate=candidate, evidence=evidence, verdict=verdict, final_status=status, mapped_page_id=mapped)


class WriterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        accepted = rec('crm pricing for small teams',
                       JudgeVerdict('crm pricing for small teams', 'p1', Decision.ACCEPT, 72.5, {'volume': 0.5, 'intent': 1.0},
                                    {'user_intent': 'commercial', 'serp_intent': {'value': 'commercial'}}),
                       {'search_volume': Metric(0, PROV), 'keyword_difficulty': Metric(None, PROV),
                        'search_volume_ads': Metric(40, ADS), 'seasonal_peak_months': Metric([1, 12], PROV)},
                       status='secondary', mapped='p1',
                       serp=SerpSnapshot('crm pricing for small teams', [], ['featured_snippet', 'people_also_ask'],
                                         ['How much does a CRM cost?'], PROV))
        rejected = rec('crm, ü free', JudgeVerdict('crm, ü free', 'p1', Decision.REJECT, None, {'volume': None}, {},
                                                    [RejectionCode.LOW_VOLUME, RejectionCode.HIGH_DIFFICULTY],
                                                    ['volume 5 < 10', 'difficulty 70 > 40, "too hard"']))
        pending = KeywordRecord(candidate=KeywordCandidate('crm import guide', 'p1', KeywordKind.QUESTION,
                                                           Source.QUESTION_EXPANSION, Tail.LONG),
                                evidence=None, verdict=None, final_status='unevaluated')
        plan = PageKeywordPlan(page_id='p1', url='/pricing', intent=Intent.COMMERCIAL, journey_stage='consideration',
                               primary='crm pricing', secondary=['crm pricing for small teams'],
                               cluster={'role': 'standalone', 'pillar_url': None, 'related_urls': []})
        self.state = {
            'market': Market(), 'targets': ResearchTargets(),
            'business': BusinessProfile('Acme', 'saas', 'CRM for small teams'),
            'pages': {'p1': PageBrief('p1', '/pricing', None, None, 'Pricing', None, [], [], [])},
            'mapping': MappingResult(plans={'p1': plan}, records=[accepted, rejected, pending], filters={'x': 1}),
            'providers': ['dataforseo', 'google_ads'], 'jev_mode': 'jev', 'page_results': {'p1': {'stop_reason': 'quota_met'}},
        }
        self.written = KeywordOutputWriter().write(self.out, self.state)

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self):
        with open(self.out / 'keyword_research.csv', encoding='utf-8', newline='') as stream:
            return list(csv.DictReader(stream))

    def test_csv_has_every_record_and_blank_unknowns(self):
        rows = self.rows()
        self.assertEqual([r['keyword'] for r in rows], ['crm pricing for small teams', 'crm, ü free', 'crm import guide'])
        good, bad, pending = rows
        self.assertEqual(good['search_volume'], '0')            # a real zero stays zero
        self.assertEqual(good['keyword_difficulty'], '')        # unknown stays blank
        self.assertEqual(good['cpc'], '')
        self.assertEqual(good['search_volume_ads'], '40')
        self.assertEqual(good['seasonal_peak_months'], '1;12')
        self.assertEqual(good['serp_features'], 'featured_snippet;people_also_ask')
        self.assertEqual((good['judged_user_intent'], good['serp_intent']), ('commercial', 'commercial'))
        self.assertEqual((good['composite_score'], good['decision'], good['final_status']), ('72.5', 'accept', 'secondary'))
        self.assertEqual((good['rejection_codes'], good['filter_stage'], good['mapped_page_url']), ('', '', '/pricing'))
        self.assertEqual(good['providers'], 'dataforseo;google_ads')
        self.assertEqual(good['retrieved_at'], '2026-11-02T10:00:00+00:00')
        self.assertEqual(good['market'], 'US/en/2840')
        self.assertEqual(good['rationale'], 'fits "pricing", page')
        self.assertEqual((good['factor_volume'], good['factor_intent']), ('0.5', '1'))
        self.assertEqual(bad['rejection_codes'], 'low_volume;high_difficulty')
        self.assertEqual(bad['rejection_reasons'], 'volume 5 < 10;difficulty 70 > 40, "too hard"')
        self.assertEqual((bad['filter_stage'], bad['factor_volume'], bad['composite_score']), ('gate', '', ''))
        self.assertEqual((pending['final_status'], pending['filter_stage'], pending['decision']),
                         ('unevaluated', 'not_evaluated', ''))
        self.assertIn('not evaluated', pending['rejection_reasons'])

    def test_keyword_map_shape(self):
        data = json.loads((self.out / 'keyword_map.json').read_text(encoding='utf-8'))
        self.assertEqual(data['schema'], SCHEMA_VERSION)
        self.assertEqual(set(data), {'schema', 'market', 'business', 'generated_at', 'filters', 'pages'})
        self.assertEqual(data['pages']['p1']['intent'], 'commercial')
        self.assertEqual(data['pages']['p1']['primary'], 'crm pricing')
        self.assertEqual(data['filters']['mapping'], {'x': 1})
        self.assertEqual(data['filters']['targets']['min_volume'], 10)
        self.assertEqual(data['business']['brand'], 'Acme')

    def test_page_audit_and_summary(self):
        audit = json.loads((self.out / 'pages' / 'p1.json').read_text())
        self.assertEqual(len(audit['records']), 3)
        self.assertEqual(audit['loop']['stop_reason'], 'quota_met')
        summary = self.written['summary']
        self.assertEqual(summary['final_status'], {'secondary': 1, 'rejected': 1, 'unevaluated': 1})
        self.assertEqual(summary['rejection_codes'], {'low_volume': 1, 'high_difficulty': 1})
        self.assertFalse(summary['degraded'])
        self.assertEqual(summary['jev_mode'], 'jev')
        self.assertIn('Jev mode: jev', (self.out / 'summary.md').read_text())


if __name__ == '__main__':
    unittest.main()
