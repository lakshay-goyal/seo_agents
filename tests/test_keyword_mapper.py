"""KeywordMapper: cannibalization guard, one primary per page, clusters, content gaps. No network."""

import unittest

from seo_agents.keyword_research.contracts import (BusinessProfile, Decision, JudgeVerdict, KeywordCandidate,
                                                   KeywordEvidence, KeywordKind, KeywordRecord, Metric, PageBrief,
                                                   RejectionCode, ResearchTargets, Source, Tail)
from seo_agents.keyword_research.mapping.keyword_mapper import KeywordMapper


def page(pid, url, copy=()):
    return PageBrief(page_id=pid, url=url, absolute_url='https://site.example' + url, page_type=None, title=pid,
                     description=None, headings=[], copy=list(copy), images=[])


def record(pid, keyword, kind=KeywordKind.PRIMARY, score=60.0, alignment=None, decision=Decision.ACCEPT, codes=(),
           source=Source.LLM_SEED, volume=None):
    metrics = {}
    if alignment is not None:
        metrics['content_alignment'] = Metric(alignment)
    if volume is not None:
        metrics['search_volume'] = Metric(volume)
    candidate = KeywordCandidate(keyword=keyword, page_id=pid, kind=kind, source=source,
                                 tail=Tail.LONG if len(keyword.split()) >= 3 else Tail.SHORT)
    verdict = JudgeVerdict(keyword=keyword, page_id=pid, decision=decision, composite_score=score, factor_scores={},
                           judgments={}, rejection_codes=list(codes), reasons=['r'] if codes else [],
                           assigned_kind=kind if decision == Decision.ACCEPT else None)
    return KeywordRecord(candidate=candidate, evidence=KeywordEvidence(keyword=keyword, metrics=metrics), verdict=verdict)


class FakeJudge:
    def __init__(self, scores):
        self.scores, self.asked = scores, []

    def same_intent(self, pairs, evidence):
        self.asked += pairs
        return {p: self.scores.get(frozenset(p), 0.0) for p in pairs}


PAGES = {'a': page('a', '/services/react', ['We build react apps for startups']), 'b': page('b', '/blog/react-tips')}
TARGETS = ResearchTargets(primary=1, secondary=1, question=0, conversational=0)


class CannibalizationTests(unittest.TestCase):
    def test_same_intent_keeps_stronger_page_and_names_it(self):
        judge = FakeJudge({frozenset({'hire react app developers', 'hire react app developer'}): 0.92})
        results = {'a': {'records': [record('a', 'hire react app developers', score=80, alignment=0.9)], 'status': 'done'},
                   'b': {'records': [record('b', 'hire react app developer', score=70, alignment=0.4)], 'status': 'done'}}
        out = KeywordMapper(judge).map(PAGES, results, targets=TARGETS)
        self.assertEqual(judge.asked, [('hire react app developers', 'hire react app developer')])
        loser = next(r for r in out.records if r.candidate.page_id == 'b')
        self.assertEqual(loser.verdict.decision, Decision.REJECT)
        self.assertIn(RejectionCode.CANNIBALIZATION, loser.verdict.rejection_codes)
        self.assertIn('/services/react', loser.verdict.reasons[-1])
        self.assertEqual((loser.final_status, loser.mapped_page_id), ('rejected', 'a'))
        self.assertEqual(out.plans['a'].primary, 'hire react app developers')
        self.assertIsNone(out.plans['b'].primary)
        self.assertEqual(out.cannibalization[0]['winner_page_id'], 'a')
        # inputs are not mutated
        self.assertEqual(results['b']['records'][0].verdict.decision, Decision.ACCEPT)

    def test_different_intent_keeps_both(self):
        judge = FakeJudge({})
        results = {'a': {'records': [record('a', 'react app development agency')]},
                   'b': {'records': [record('b', 'react app development tutorial')]}}
        out = KeywordMapper(judge).map(PAGES, results, targets=TARGETS)
        self.assertEqual(len(judge.asked), 1)
        self.assertEqual((out.plans['a'].primary, out.plans['b'].primary),
                         ('react app development agency', 'react app development tutorial'))

    def test_identical_keyword_never_shared_and_skips_judge(self):
        judge = FakeJudge({})
        results = {'a': {'records': [record('a', 'react consulting', score=50)]},
                   'b': {'records': [record('b', 'react consulting', score=90, alignment=1.0)]}}
        out = KeywordMapper(judge).map(PAGES, results, targets=TARGETS)
        self.assertEqual(judge.asked, [])
        self.assertEqual(out.plans['b'].primary, 'react consulting')
        self.assertIsNone(out.plans['a'].primary)

    def test_page_that_lost_its_primary_promotes_its_best_secondary(self):
        results = {'a': {'records': [record('a', 'react consulting', score=90, alignment=1.0)]},
                   'b': {'records': [record('b', 'react consulting', score=40),
                                     record('b', 'react performance tips guide', KeywordKind.SECONDARY, score=55)]}}
        out = KeywordMapper(FakeJudge({})).map(PAGES, results, targets=TARGETS)
        self.assertEqual(out.plans['b'].primary, 'react performance tips guide')
        self.assertEqual(out.plans['b'].secondary, [])
        self.assertEqual(out.plans['b'].status, 'partial')

    def test_extra_primaries_become_secondary_then_quota_full(self):
        results = {'a': {'records': [record('a', 'react agency berlin', score=90), record('a', 'react studio berlin', score=80),
                                     record('a', 'react shop berlin', score=70)]}}
        out = KeywordMapper(FakeJudge({})).map({'a': PAGES['a']}, results, targets=TARGETS)
        plan = out.plans['a']
        self.assertEqual((plan.primary, plan.secondary), ('react agency berlin', ['react studio berlin']))
        shop = next(r for r in out.records if r.candidate.keyword == 'react shop berlin')
        self.assertEqual(shop.verdict.rejection_codes, [RejectionCode.QUOTA_FULL])
        self.assertEqual(plan.status, 'complete')


class PlanTests(unittest.TestCase):
    def test_cluster_pillar_is_broadest_head(self):
        pages = {'a': page('a', '/react'), 'b': page('b', '/react/hooks'), 'c': page('c', '/contact')}
        results = {'a': {'records': [record('a', 'react tutorials')]},
                   'b': {'records': [record('b', 'react hooks tutorial for beginners')]},
                   'c': {'records': [record('c', 'contact a web agency')]}}
        out = KeywordMapper(FakeJudge({})).map(pages, results, business=BusinessProfile('Acme', 'business', 'x'),
                                               targets=TARGETS)
        self.assertEqual(out.plans['a'].cluster['role'], 'pillar')
        self.assertEqual(out.plans['b'].cluster, {'role': 'cluster', 'pillar_url': '/react', 'related_urls': ['/react'],
                                                  'shared_terms': ['react', 'tutorial']})
        self.assertEqual(out.plans['c'].cluster['role'], 'standalone')

    def test_content_gaps_are_relevant_competitor_keywords_the_copy_misses(self):
        results = {'a': {'records': [
            record('a', 'react app agency', score=80),
            record('a', 'react native consulting rates', KeywordKind.SECONDARY, source=Source.COMPETITOR_REVERSE, volume=90),
            record('a', 'react apps for startups', KeywordKind.SECONDARY, decision=Decision.REJECT,
                   codes=[RejectionCode.QUOTA_FULL], source=Source.COMPETITOR_REVERSE),
            record('a', 'vue migration services', KeywordKind.SECONDARY, decision=Decision.REJECT,
                   codes=[RejectionCode.NOT_RELEVANT], source=Source.COMPETITOR_REVERSE)]}}
        out = KeywordMapper(FakeJudge({})).map({'a': PAGES['a']}, results, targets=TARGETS)
        self.assertEqual(out.plans['a'].content_gaps, ['react native consulting rates'])


if __name__ == '__main__':
    unittest.main()
