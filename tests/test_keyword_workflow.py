"""Keyword research workflow: page loop stop conditions, feedback to the generator, end-to-end outputs.
Fake toolkit / generator / judge implement the contracts.py Protocols; no network, no model."""

from contextlib import redirect_stdout
import csv
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from seo_agents.keyword_research.contracts import (SCHEMA_VERSION, BusinessProfile, Decision, Intent, JudgeVerdict,
                                                   KeywordCandidate, KeywordEvidence, KeywordKind, Metric, Provenance,
                                                   RejectionCode, ResearchTargets, Source, Tail)
from seo_agents.keyword_research.keyword_research import KeywordResearchConfigError, KeywordResearchWorkflow, needs_for

FIXTURE = Path(__file__).parent / 'fixtures' / 'onpage' / 'vite-spa' / 'seo_reports'
HOME, ABOUT = 'p_42099b4af0', 'p_69bbc6be2c'
PROV = Provenance(provider='dataforseo', endpoint='fake', retrieved_at='2026-11-03T00:00:00+00:00')


def cand(keyword, kind=KeywordKind.SECONDARY, source=Source.LLM_SEED, **kw):
    tail = Tail.SHORT if len(keyword.split()) < 3 else Tail.LONG
    return KeywordCandidate(keyword=keyword, page_id='', kind=kind, source=source, tail=tail, **kw)


class FakeToolkit:
    def __init__(self, providers=('dataforseo',), competitor=True):
        self.providers, self.competitor = list(providers), competitor
        self.enriched = []

    def available_providers(self):
        return self.providers

    def enrich(self, keywords, market, *, page=None):
        self.enriched += keywords
        out = {}
        for k in keywords:
            metrics = {} if 'unknown' in k or not self.providers else {'search_volume': Metric(320, PROV),
                                                                         'keyword_difficulty': Metric(12, PROV)}
            out[k] = KeywordEvidence(keyword=k, metrics=metrics)
        return out

    def expand(self, seeds, market, *, limit=50):
        return []

    def questions(self, seeds, market, *, limit=30):
        return []

    def serp(self, keyword, market):
        return None

    def competitor_urls(self, keywords, market, *, own_domain, limit=5):
        return ['https://rival.example/portfolio'] if self.competitor else []

    def competitor_keywords(self, url, market, *, limit=100):
        return [cand('software engineer portfolio examples', source=Source.COMPETITOR_REVERSE)]

    def page_queries(self, page_url, market):
        return []

    def compare_trends(self, keywords, market):
        return {k: {'trend_direction': 'stable', 'region_interest': [{'region': 'California', 'value': 100}]} for k in keywords}


class ScriptedGenerator:
    """Returns script[iteration] (or `default(iteration)`) and records what each call received."""

    def __init__(self, script=None, default=None):
        self.script, self.default, self.calls = script or {}, default, []

    def profile_business(self, site_packet, market):
        return BusinessProfile(brand='Lakshay Goyal', site_type='personal', offering='software engineering portfolio',
                               seed_topics=['software engineer'], market=market)

    def generate(self, page, business, *, needs, competitor_keywords, accepted, rejected, iteration, limit):
        self.calls.append({'page': page.page_id, 'needs': dict(needs), 'iteration': iteration,
                           'rejected': [(r.candidate.keyword, [c.value for c in r.verdict.rejection_codes]) for r in rejected],
                           'accepted': [r.candidate.keyword for r in accepted],
                           'competitor_keywords': [c.keyword for c in competitor_keywords]})
        if iteration in self.script:
            return list(self.script[iteration])
        return list(self.default(iteration)) if self.default else []


class RuleJudge:
    """'bad' -> REJECT low_volume; '/needs-data' metrics missing -> REVIEW; else ACCEPT while the slot has room."""

    mode = 'heuristic-no-jev'

    def __init__(self, same=0.9):
        self.same, self.pairs = same, []

    def judge(self, page, business, candidates, evidence, *, accepted, targets):
        taken = {k: 0 for k in KeywordKind}
        for record in accepted:
            taken[record.verdict.assigned_kind] += 1
        limits = {KeywordKind.PRIMARY: targets.primary, KeywordKind.SECONDARY: targets.secondary,
                  KeywordKind.QUESTION: targets.question, KeywordKind.CONVERSATIONAL: targets.conversational}
        out = []
        for c in candidates:
            ev = evidence.get(c.keyword)
            if 'bad' in c.keyword:
                out.append(JudgeVerdict(c.keyword, c.page_id, Decision.REJECT, None, {'volume': 0.0}, {},
                                        [RejectionCode.LOW_VOLUME], ['search volume 0 is below the minimum of 10']))
            elif ev is None or ev.value('search_volume') is None:
                out.append(JudgeVerdict(c.keyword, c.page_id, Decision.REVIEW, None, {'volume': None}, {},
                                        [RejectionCode.MISSING_DATA], ['search volume unknown']))
            elif taken[c.kind] >= limits[c.kind]:
                out.append(JudgeVerdict(c.keyword, c.page_id, Decision.REJECT, 60.0, {'volume': 0.8}, {},
                                        [RejectionCode.QUOTA_FULL], ['page already has enough ' + c.kind.value]))
            else:
                taken[c.kind] += 1
                out.append(JudgeVerdict(c.keyword, c.page_id, Decision.ACCEPT, 70.0, {'volume': 0.8, 'relevance': 0.9},
                                        {'user_intent': {'value': 'commercial'}, 'journey_stage': 'consideration'},
                                        assigned_kind=c.kind, reasons=['fits the page']))
        return out

    def same_intent(self, pairs, evidence):
        self.pairs += pairs
        return {p: self.same for p in pairs}


SMALL = ResearchTargets(primary=1, secondary=1, question=0, conversational=0, short_tail=0, max_iterations=5,
                        candidates_per_iteration=10)


def run(generator, *, toolkit=None, judge=None, targets=SMALL, pages=(HOME,), out=None, **kw):
    workflow = KeywordResearchWorkflow(toolkit or FakeToolkit(competitor=False), generator, judge or RuleJudge(),
                                       targets=targets, concurrency=2)
    with redirect_stdout(io.StringIO()):
        return workflow.run(FIXTURE, out_dir=out, only_pages=list(pages), **kw)


class PageLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_stops_when_quota_met_and_feeds_rejections_back(self):
        generator = ScriptedGenerator({
            1: [cand('software engineer portfolio', KeywordKind.PRIMARY), cand('bad cheap portfolio template')],
            2: [cand('react developer portfolio site')],
            3: [cand('never asked keyword here')]})
        state = run(generator, out=self.out)
        result = state['page_results'][HOME]
        self.assertEqual(result['stop_reason'], 'quota_met')
        self.assertEqual(result['iterations'], 2)
        self.assertEqual(len(generator.calls), 2)
        second = generator.calls[1]
        self.assertIn(('bad cheap portfolio template', ['low_volume']), second['rejected'])
        self.assertEqual(second['needs']['primary'], 0)
        self.assertEqual(second['needs']['secondary'], 1)
        self.assertIn('software engineer portfolio', second['accepted'])
        plan = state['mapping'].plans[HOME]
        self.assertEqual(plan.primary, 'software engineer portfolio')
        self.assertEqual(plan.secondary, ['react developer portfolio site'])
        self.assertEqual(plan.status, 'complete')
        self.assertEqual(plan.intent, Intent.COMMERCIAL)
        self.assertEqual(plan.journey_stage, 'consideration')

    def test_stops_at_max_iterations(self):
        generator = ScriptedGenerator(default=lambda i: [cand(f'bad keyword number {i}')])
        targets = ResearchTargets(primary=1, secondary=1, question=0, conversational=0, max_iterations=3,
                                  candidates_per_iteration=5)
        state = run(generator, targets=targets, out=self.out)
        result = state['page_results'][HOME]
        self.assertEqual((result['stop_reason'], result['iterations']), ('max_iterations', 3))
        self.assertEqual(state['mapping'].plans[HOME].status, 'partial')
        # rejections accumulate across iterations and all reach the generator
        self.assertEqual(len(generator.calls[2]['rejected']), 2)

    def test_stops_when_nothing_new(self):
        generator = ScriptedGenerator(default=lambda i: [cand('bad repeated keyword idea')])
        state = run(generator, out=self.out)
        result = state['page_results'][HOME]
        self.assertEqual((result['stop_reason'], result['iterations']), ('no_new_candidates', 2))

    def test_needs_ignore_rejected_and_review(self):
        self.assertEqual(needs_for(SMALL, []), {'primary': 1, 'secondary': 1, 'question': 0, 'conversational': 0,
                                                'short_tail': 0})

    def test_refuses_without_openrouter_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(KeywordResearchConfigError):
                KeywordResearchWorkflow(FakeToolkit(), None, RuleJudge()).run(FIXTURE, out_dir=self.out)


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_outputs_hold_every_candidate_with_reasons(self):
        generator = ScriptedGenerator({1: [cand('software engineer portfolio', KeywordKind.PRIMARY),
                                           cand('bad cheap portfolio template'),
                                           cand('unknown metric keyword idea')]})
        state = run(generator, toolkit=FakeToolkit(competitor=True), pages=(HOME, ABOUT), out=str(self.out),
                    competitors=['https://www.rival.example/'])
        self.assertIn('rival.example', state['business'].competitors)
        with open(self.out / 'keyword_research.csv', encoding='utf-8') as stream:
            rows = list(csv.DictReader(stream))
        keywords = {(r['page_id'], r['keyword']) for r in rows}
        self.assertIn((HOME, 'bad cheap portfolio template'), keywords)
        self.assertIn((HOME, 'software engineer portfolio examples'), keywords)   # reverse research candidate
        bad = next(r for r in rows if r['keyword'] == 'bad cheap portfolio template' and r['page_id'] == HOME)
        self.assertEqual((bad['decision'], bad['final_status'], bad['filter_stage']), ('reject', 'rejected', 'gate'))
        self.assertEqual(bad['rejection_codes'], 'low_volume')
        self.assertIn('below the minimum', bad['rejection_reasons'])
        unknown = next(r for r in rows if r['keyword'] == 'unknown metric keyword idea' and r['page_id'] == HOME)
        self.assertEqual((unknown['search_volume'], unknown['keyword_difficulty'], unknown['cpc']), ('', '', ''))
        self.assertEqual(unknown['decision'], 'review')
        reverse = next(r for r in rows if r['keyword'] == 'software engineer portfolio examples' and r['page_id'] == HOME)
        self.assertEqual((reverse['source'], reverse['competitor_url']), ('competitor-reverse', 'https://rival.example/portfolio'))
        self.assertEqual(reverse['top_regions'], 'California')
        self.assertIn('factor_volume', rows[0])
        # the same primary proposed for two pages: one page keeps it, the other is told which page owns it
        primaries = [r for r in rows if r['keyword'] == 'software engineer portfolio']
        self.assertEqual(sorted(r['final_status'] for r in primaries), ['primary', 'rejected'])
        loser = next(r for r in primaries if r['final_status'] == 'rejected')
        self.assertEqual(loser['filter_stage'], 'mapping')
        self.assertIn('cannibalization', loser['rejection_codes'])
        self.assertTrue(loser['mapped_page_url'])
        self.assertIn(loser['mapped_page_url'], loser['rejection_reasons'])

        data = json.loads((self.out / 'keyword_map.json').read_text())
        self.assertEqual(data['schema'], SCHEMA_VERSION)
        self.assertEqual(set(data), {'schema', 'market', 'business', 'generated_at', 'filters', 'pages'})
        self.assertEqual(set(data['pages']), {HOME, ABOUT})
        plan = data['pages'][HOME]
        for key in ('primary', 'secondary', 'questions', 'conversational', 'short_tail', 'intent', 'journey_stage', 'cluster',
                    'competitor_urls', 'content_gaps', 'status', 'url'):
            self.assertIn(key, plan)
        self.assertEqual(plan['competitor_urls'], ['https://rival.example/portfolio'])
        self.assertEqual(data['market']['country'], 'US')
        summary = json.loads((self.out / 'summary.json').read_text())
        self.assertEqual(summary['jev_mode'], 'heuristic-no-jev')
        self.assertEqual(summary['candidates'], len(rows))
        self.assertTrue((self.out / 'pages' / (HOME + '.json')).exists())
        self.assertTrue((self.out / 'summary.md').read_text().startswith('# Keyword research summary'))

    def test_degraded_mode_without_providers(self):
        generator = ScriptedGenerator({1: [cand('software engineer portfolio', KeywordKind.PRIMARY)]})
        state = run(generator, toolkit=FakeToolkit(providers=(), competitor=False), out=str(self.out))
        summary = state['written']['summary']
        self.assertTrue(summary['degraded'])
        self.assertIn('DEGRADED', (self.out / 'summary.md').read_text())
        self.assertEqual(summary['final_status'], {'review': 1})


if __name__ == '__main__':
    unittest.main()
