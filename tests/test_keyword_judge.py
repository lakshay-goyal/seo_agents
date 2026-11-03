"""Keyword judge: gates, decision table, scoring, quota fill, same_intent. No network: a fake Jev backend."""

import json

import pytest

from seo_agents.keyword_research.contracts import (BusinessProfile, Decision, JudgeVerdict, KeywordCandidate,
                                                   KeywordEvidence, KeywordKind, KeywordRecord, Metric, PageBrief,
                                                   Provenance, RejectionCode, ResearchTargets, SerpResult, SerpSnapshot,
                                                   Source, Tail)
from seo_agents.keyword_research.judge import HEURISTIC_MODE, JevError, JevKeywordJudge, JevResult
from seo_agents.keyword_research.judge import jev_questions as jq
from seo_agents.keyword_research.judge.gates import keyword_key, run_gates
from seo_agents.keyword_research.judge.jev_client import JevClient
from seo_agents.keyword_research.judge.scoring import WEIGHTS, composite_score, factor_scores

PROV = Provenance(provider='dataforseo', endpoint='keywords_data', retrieved_at='2026-11-03T10:00:00Z')

GOOD = {
    'user_intent': {'type': 'choice', 'choice': 'commercial'},
    'intent_clear': {'type': 'noul', 'noul': 0.9},
    'serp_intent': {'type': 'choice', 'choice': 'commercial'},
    'page_satisfies_intent': {'type': 'noul', 'noul': 0.9},
    'business_relevant': {'type': 'noul', 'noul': 0.95},
    'content_alignment': {'type': 'score', 'score': 2.8},
    'page_extendable': {'type': 'noul', 'noul': 0.8},
    'resolves_user_purpose': {'type': 'noul', 'noul': 0.85},
    'conversion_potential': {'type': 'score', 'score': 2.5},
    'specific_need': {'type': 'noul', 'noul': 0.9},
    'question_answerable': {'type': 'noul', 'noul': 0.9},
    'ai_answer_citable': {'type': 'noul', 'noul': 0.9},
    'competitor_brand': {'type': 'noul', 'noul': 0.05},
    'journey_stage': {'type': 'choice', 'choice': 'consideration'},
}


class FakeJev:
    """Answers every question with GOOD unless overridden per keyword: overrides[keyword][qid] = answer."""

    def __init__(self, overrides=None, fail_for=(), raise_all=False):
        self.overrides = overrides or {}
        self.fail_for = set(fail_for)
        self.raise_all = raise_all
        self.requests = []

    def ask(self, state, questions):
        self.requests.append((state, questions))
        if self.raise_all:
            raise JevError('503 upstream unavailable', payload_ref='jev/deadbeef.json')
        if 'pairs' in state:
            return JevResult(answers={qid: {'type': 'noul', 'noul': 0.8} for qid in questions}, model='fake', payload_ref=None)
        keywords = {key: c['keyword'] for key, c in state['candidates'].items()}
        if any(kw in self.fail_for for kw in keywords.values()):
            raise JevError('timeout', payload_ref='jev/abc.json')
        answers = {}
        for qid in questions:
            key, name = qid.split('__', 1)
            answers[qid] = self.overrides.get(keywords[key], {}).get(name, GOOD[name])
        return JevResult(answers=answers, model='jev-1.14.0', payload_ref='jev/fake.json')


def business(**kw):
    base = dict(brand='Acme CRM', site_type='saas', offering='CRM software for small agencies',
                audience=['agency owners'], competitors=['hubspot.com'], excluded_terms=['salesforce'])
    base.update(kw)
    return BusinessProfile(**base)


def page(page_type='product'):
    return PageBrief(page_id='p1', url='/crm-for-agencies', absolute_url='https://acme.test/crm-for-agencies',
                     page_type=page_type, title='CRM for agencies', description='', headings=['Pipelines', 'Pricing'],
                     copy=['Acme CRM helps small agencies track clients.'] * 3, images=[])


def cand(keyword, kind=KeywordKind.SECONDARY, tail=Tail.LONG):
    return KeywordCandidate(keyword=keyword, page_id='p1', kind=kind, source=Source.LLM_SEED, tail=tail)


def evidence(keyword, *, volume=200, kd=20, serp=True, **metrics):
    ev = KeywordEvidence(keyword=keyword)
    for name, value in {'search_volume': volume, 'keyword_difficulty': kd, **metrics}.items():
        ev.metrics[name] = Metric(value=value, provenance=PROV)
    if serp:
        ev.serp = SerpSnapshot(keyword=keyword, features=['people_also_ask'], people_also_ask=['what is a crm?'],
                               results=[SerpResult(position=i + 1, url=f'https://site{i}.test/{keyword.replace(" ", "-")}',
                                                   domain=f'site{i}.test', title=f'{keyword} {i}', result_type='organic')
                                        for i in range(10)])
    return ev


def run(keywords, overrides=None, targets=None, accepted=(), page_type='product', client=None, **ev_kw):
    candidates = [k if isinstance(k, KeywordCandidate) else cand(k) for k in keywords]
    evid = {c.keyword: evidence(c.keyword, **ev_kw) for c in candidates}
    judge = JevKeywordJudge(client if client is not None else FakeJev(overrides))
    return judge.judge(page(page_type), business(), candidates, evid, accepted=list(accepted),
                       targets=targets or ResearchTargets())


# ---------------------------------------------------------------- gates
def test_gate_low_volume_and_high_kd_with_values_and_provenance():
    c = cand('crm pipeline template for agencies')
    hits = run_gates(c, evidence(c.keyword, volume=4, kd=62), business(), ResearchTargets(), [])
    codes = {h.code for h in hits}
    assert codes == {RejectionCode.LOW_VOLUME, RejectionCode.HIGH_DIFFICULTY}
    kd_reason = next(h.reason for h in hits if h.code is RejectionCode.HIGH_DIFFICULTY)
    assert 'KD 62 > max 40' in kd_reason and '(dataforseo, 2026-11-03)' in kd_reason


def test_unknown_metrics_never_reject_but_all_unknown_flags_missing_data_review():
    c = cand('crm for small creative agencies')
    only_kd_unknown = run_gates(c, evidence(c.keyword, volume=50, kd=None), business(), ResearchTargets(), [])
    assert only_kd_unknown == []
    all_unknown = run_gates(c, evidence(c.keyword, volume=None, kd=None), business(), ResearchTargets(), [])
    assert [(h.code, h.hard) for h in all_unknown] == [(RejectionCode.MISSING_DATA, False)]
    with_gsc = run_gates(c, evidence(c.keyword, volume=None, kd=None, gsc_impressions=40), business(), ResearchTargets(), [])
    assert with_gsc == []


def test_conversational_exempt_from_volume_and_missing_data():
    c = cand('which crm should a 5 person agency use', kind=KeywordKind.CONVERSATIONAL)
    assert run_gates(c, evidence(c.keyword, volume=0, kd=None), business(), ResearchTargets(), []) == []
    assert run_gates(c, evidence(c.keyword, volume=None, kd=None), business(), ResearchTargets(), []) == []


def test_short_tail_uses_short_tail_difficulty_cap():
    c = cand('agency crm', tail=Tail.SHORT)
    assert run_gates(c, evidence(c.keyword, kd=50), business(), ResearchTargets(), []) == []
    assert run_gates(c, evidence(c.keyword, kd=60), business(), ResearchTargets(), [])[0].code is RejectionCode.HIGH_DIFFICULTY


def test_competitor_trend_zero_click_serp_dominated_gates():
    t = ResearchTargets()
    assert run_gates(cand('hubspot alternative for agencies'), evidence('x'), business(), t, [])[0].code \
        is RejectionCode.COMPETITOR_BRAND
    assert run_gates(cand('salesforce pricing for agencies'), evidence('x'), business(), t, [])[0].code \
        is RejectionCode.COMPETITOR_BRAND
    declining = run_gates(cand('crm for fax agencies'), evidence('x', volume=40, trend_direction='declining'), business(), t, [])
    assert declining[0].code is RejectionCode.DECLINING_TREND and '40/month' in declining[0].reason
    zero = run_gates(cand('crm meaning for agencies'), evidence('x', click_opportunity=0.1), business(), t, [])
    assert zero[0].code is RejectionCode.ZERO_CLICK and '0.10' in zero[0].reason
    question = cand('what is a crm for agencies', kind=KeywordKind.QUESTION)
    assert run_gates(question, evidence('x', click_opportunity=0.1), business(), t, []) == []
    dom = run_gates(cand('best crm software agencies'), evidence('x', serp_authority_share=0.8), business(), t, [])
    assert dom[0].code is RejectionCode.SERP_DOMINATED
    assert run_gates(cand('best crm software agencies'), evidence('x', serp_authority_share=0.8), business(), t, [],
                     site_authority='high') == []


def test_duplicate_variant_of_accepted_keyword():
    assert keyword_key('CRM for agencies') == keyword_key('agency crms')
    accepted = [KeywordRecord(candidate=cand('crm for agencies'), evidence=None, verdict=None, final_status='secondary')]
    hits = run_gates(cand('agencies crm'), evidence('x'), business(), ResearchTargets(), accepted)
    assert hits[0].code is RejectionCode.DUPLICATE and 'crm for agencies' in hits[0].reason


# ---------------------------------------------------------------- decision table
def test_accept_row_with_score_and_role():
    [v] = run(['crm for small marketing agencies'])
    assert v.decision is Decision.ACCEPT and v.assigned_kind is KeywordKind.SECONDARY
    assert v.composite_score is not None and 0 < v.composite_score <= 100
    assert v.judgments['mode'] == 'jev' and v.judgments['business_relevant']['noul'] == 0.95
    assert v.jev_payload_ref == 'jev/fake.json'


def test_hard_gate_skips_jev():
    client = FakeJev()
    [v] = run(['crm pipeline for agencies'], client=client, kd=80)
    assert v.decision is Decision.REJECT and v.rejection_codes == [RejectionCode.HIGH_DIFFICULTY]
    assert v.composite_score is None and client.requests == []


@pytest.mark.parametrize('override, code, fragment', [
    ({'page_satisfies_intent': {'type': 'noul', 'noul': 0.1}}, RejectionCode.INTENT_MISMATCH, 'page_satisfies_intent 0.10'),
    ({'serp_intent': {'type': 'choice', 'choice': 'informational'}}, RejectionCode.INTENT_MISMATCH, 'SERP intent "informational"'),
    ({'business_relevant': {'type': 'noul', 'noul': 0.2}}, RejectionCode.NOT_RELEVANT, 'business_relevant 0.20'),
    ({'competitor_brand': {'type': 'noul', 'noul': 0.9}}, RejectionCode.COMPETITOR_BRAND, 'competitor_brand 0.90'),
    ({'resolves_user_purpose': {'type': 'noul', 'noul': 0.05}}, RejectionCode.UNSATISFIABLE, 'resolves_user_purpose 0.05'),
    ({'content_alignment': {'type': 'score', 'score': 0.2}, 'page_extendable': {'type': 'noul', 'noul': 0.1}},
     RejectionCode.POOR_CONTENT_ALIGNMENT, 'content_alignment 0.20/3'),
    ({'conversion_potential': {'type': 'score', 'score': 0.3}}, RejectionCode.LOW_CONVERSION, 'conversion_potential 0.30/3'),
])
def test_reject_rows(override, code, fragment):
    [v] = run(['crm for small marketing agencies'], overrides={'crm for small marketing agencies': override})
    assert v.decision is Decision.REJECT and code in v.rejection_codes
    assert any(fragment in r for r in v.reasons), v.reasons


def test_low_alignment_but_extendable_is_not_rejected():
    o = {'content_alignment': {'type': 'score', 'score': 0.2}, 'page_extendable': {'type': 'noul', 'noul': 0.9}}
    [v] = run(['crm for small marketing agencies'], overrides={'crm for small marketing agencies': o})
    assert v.decision is Decision.ACCEPT


def test_low_conversion_only_on_commercial_pages_for_primary_secondary():
    o = {'conversion_potential': {'type': 'score', 'score': 0.1}, 'serp_intent': {'type': 'choice', 'choice': 'informational'},
         'user_intent': {'type': 'choice', 'choice': 'informational'}}
    [v] = run(['crm for small marketing agencies'], overrides={'crm for small marketing agencies': o}, page_type='article')
    assert v.decision is Decision.ACCEPT


def test_question_kind_noul():
    q = cand('how do agencies track client projects', kind=KeywordKind.QUESTION)
    [v] = run([q], overrides={q.keyword: {'question_answerable': {'type': 'noul', 'noul': 0.1}}})
    assert v.decision is Decision.REJECT and v.rejection_codes == [RejectionCode.UNSATISFIABLE]
    [v] = run([q], overrides={q.keyword: {'question_answerable': {'type': 'noul', 'noul': 0.5}}})
    assert v.decision is Decision.REVIEW and 'question_answerable 0.50' in v.reasons[0]


@pytest.mark.parametrize('qid', list(jq.KEY_NOULS))
def test_unsettled_band_goes_to_review(qid):
    [v] = run(['crm for small marketing agencies'], overrides={'crm for small marketing agencies': {qid: {'type': 'noul', 'noul': 0.5}}})
    assert v.decision is Decision.REVIEW and v.rejection_codes == []
    assert f'{qid} 0.50' in v.reasons[0]


def test_user_vs_serp_intent_disagreement_is_review():
    o = {'user_intent': {'type': 'choice', 'choice': 'informational'}}
    [v] = run(['crm for small marketing agencies'], overrides={'crm for small marketing agencies': o})
    assert v.decision is Decision.REVIEW and 'disagrees with SERP intent' in v.reasons[0]


def test_no_serp_user_intent_mismatch():
    o = {'user_intent': {'type': 'choice', 'choice': 'informational'}}
    [v] = run(['crm for small marketing agencies'], overrides={'crm for small marketing agencies': o}, serp=False)
    assert v.decision is Decision.REJECT and v.rejection_codes == [RejectionCode.INTENT_MISMATCH]
    o['intent_clear'] = {'type': 'noul', 'noul': 0.5}
    [v] = run(['crm for small marketing agencies'], overrides={'crm for small marketing agencies': o}, serp=False)
    assert v.decision is Decision.REVIEW


def test_api_error_is_judge_error_review_not_reject():
    [v] = run(['crm for small marketing agencies'], client=FakeJev(raise_all=True))
    assert v.decision is Decision.REVIEW and v.rejection_codes == [RejectionCode.JUDGE_ERROR]
    assert '503' in v.reasons[0] and v.jev_payload_ref == 'jev/deadbeef.json'


def test_api_error_isolated_per_request():
    verdicts = run(['crm for small marketing agencies', 'crm for design studios'],
                   client=FakeJev(fail_for={'crm for design studios'}))
    assert [v.decision for v in verdicts] == [Decision.ACCEPT, Decision.REVIEW]


def test_missing_answer_is_judge_error():
    class Partial(FakeJev):
        def ask(self, state, questions):
            result = super().ask(state, questions)
            result.answers.pop('k0__business_relevant')
            return result
    [v] = run(['crm for small marketing agencies'], client=Partial())
    assert v.rejection_codes == [RejectionCode.JUDGE_ERROR] and 'business_relevant' in v.reasons[0]


def test_missing_data_soft_gate_caps_at_review():
    [v] = run(['crm for small marketing agencies'], volume=None, kd=None)
    assert v.decision is Decision.REVIEW and v.rejection_codes == [RejectionCode.MISSING_DATA]


def test_heuristic_mode_without_api_key(monkeypatch, caplog):
    monkeypatch.delenv('TYPESAFE_API_KEY', raising=False)
    judge = JevKeywordJudge.from_env()
    assert judge.mode == HEURISTIC_MODE and 'TYPESAFE_API_KEY not set' in caplog.text
    c = cand('crm for small marketing agencies')
    [v] = judge.judge(page(), business(), [c], {c.keyword: evidence(c.keyword)}, accepted=[], targets=ResearchTargets())
    assert v.decision is Decision.REVIEW and v.judgments['mode'] == 'heuristic-no-jev'
    assert v.factor_scores['relevance'] is None and v.composite_score is not None


# ---------------------------------------------------------------- scoring
def test_weights_sum_to_one_and_unknown_factors_renormalize():
    assert abs(sum(WEIGHTS.values()) - 1) < 1e-9
    full = {name: 1.0 for name in WEIGHTS}
    assert composite_score(full) == (100.0, [])
    partial = dict(full, gsc_opportunity=None, trend=None)
    score, unknown = composite_score(partial)
    assert score == 100.0 and set(unknown) == {'gsc_opportunity', 'trend'}   # unknown is not 0
    half = dict({n: None for n in WEIGHTS}, relevance=0.5)
    assert composite_score(half) == (50.0, [n for n in WEIGHTS if n != 'relevance'])
    assert composite_score({n: None for n in WEIGHTS})[0] is None


def test_factor_scores_from_metrics():
    ev = evidence('x', volume=9999, kd=40, serp_authority_share=0.2, trend_direction='rising',
                  gsc_impressions=1000, gsc_position=12)
    f = factor_scores(ev, None)
    assert f['ranking_feasibility'] == pytest.approx(0.7) and f['demand'] == pytest.approx(1.0, abs=1e-4)
    assert f['trend'] == 1.0 and f['gsc_opportunity'] == pytest.approx(1.0) and f['relevance'] is None


# ---------------------------------------------------------------- quota
def test_quota_fill_by_score_and_quota_full_reason():
    keywords = [f'crm for {x} agencies' for x in ('legal', 'marketing', 'design', 'video', 'seo', 'pr')]
    overrides = {kw: {'business_relevant': {'type': 'noul', 'noul': 0.71 + i * 0.04}} for i, kw in enumerate(keywords)}
    verdicts = run(keywords, overrides=overrides)
    accepted = [v for v in verdicts if v.decision is Decision.ACCEPT]
    full = [v for v in verdicts if RejectionCode.QUOTA_FULL in v.rejection_codes]
    assert len(accepted) == 4 and len(full) == 2
    assert min(v.composite_score for v in accepted) >= max(v.composite_score for v in full)
    assert all('secondary 4' in v.reasons[-1] for v in full)


def test_primary_overflow_to_secondary_and_existing_accepted_counts():
    p1, p2 = cand('crm for small legal agencies', KeywordKind.PRIMARY), cand('crm for small video agencies', KeywordKind.PRIMARY)
    existing = [KeywordRecord(candidate=cand(f'tool {i} for agencies'), evidence=None, verdict=None, final_status='secondary')
                for i in range(3)]
    verdicts = run([p1, p2], accepted=existing)
    roles = sorted(v.judgments.get('role') for v in verdicts)
    assert roles == ['primary', 'secondary']
    verdicts = run([p1, p2], accepted=existing + [KeywordRecord(candidate=cand('tool 9 for agencies'), evidence=None,
                                                                verdict=None, final_status='secondary')])
    assert sorted(v.decision.value for v in verdicts) == ['accept', 'reject']


def test_short_tail_rejected_when_quota_zero_and_allowed_when_raised():
    short = cand('agency crm', KeywordKind.PRIMARY, Tail.SHORT)
    [v] = run([short])
    assert v.rejection_codes == [RejectionCode.QUOTA_FULL] and 'short-tail quota is 0' in v.reasons[-1]
    [v] = run([short], targets=ResearchTargets(short_tail=1))
    assert v.decision is Decision.ACCEPT and v.judgments['role'] == 'primary'


def test_within_batch_variants_dedup():
    verdicts = run(['crm for agencies', 'agency crms'])
    assert sorted(v.decision.value for v in verdicts) == ['accept', 'reject']
    loser = next(v for v in verdicts if v.decision is Decision.REJECT)
    assert loser.rejection_codes == [RejectionCode.DUPLICATE]


# ---------------------------------------------------------------- request shape / payload logging
def test_request_shape_references_state_paths():
    client = FakeJev()
    run(['crm for small marketing agencies', 'crm for design studios'], client=client)
    state, questions = client.requests[0]
    assert set(state) == {'business', 'page', 'candidates'} and list(state['candidates']) == ['k0']
    assert '`candidates.k0.keyword`' in questions['k0__user_intent']['instructions']
    assert len(questions) == len(jq.QUESTIONS)
    assert state['candidates']['k0']['facts']['monthly_searches'].startswith('moderate')   # numbers -> buckets
    assert 'rationale' not in state['candidates']['k0']


def test_chunking_multiple_keywords_per_request():
    client = FakeJev()
    candidates = [cand(f'crm for {x} agencies') for x in ('legal', 'design', 'video')]
    judge = JevKeywordJudge(client, keywords_per_request=2)
    judge.judge(page(), business(), candidates, {c.keyword: evidence(c.keyword) for c in candidates},
                accepted=[], targets=ResearchTargets())
    assert sorted(len(s['candidates']) for s, _ in client.requests) == [1, 2]


def test_jev_client_logs_payload_and_wraps_errors(tmp_path):
    from typesafe_sdk import Noul

    class Answer:
        def __init__(self, data):
            self.data = data

        def model_dump(self):
            return self.data

    class SDK:
        def __init__(self, fail=False):
            self.fail = fail

        def system_one(self, state, questions, model):
            assert isinstance(questions['q'], Noul)
            if self.fail:
                raise RuntimeError('boom')
            return type('R', (), {'answers': {'q': Answer({'type': 'noul', 'noul': 0.8})}, 'model': 'jev-1.14.0',
                                  'usage': None, 'request_id': 'r1'})()

    client = JevClient(api_key='secret-key', evidence_dir=tmp_path, sdk_client=SDK())
    result = client.ask({'doc': 'x'}, {'q': {'type': 'noul', 'instructions': 'Is `doc` x?'}})
    assert result.answers == {'q': {'type': 'noul', 'noul': 0.8}} and result.model == 'jev-1.14.0'
    logged = json.loads((tmp_path / result.payload_ref).read_text())
    assert logged['questions']['q']['instructions'] == 'Is `doc` x?' and 'secret-key' not in json.dumps(logged)
    failing = JevClient(api_key='secret-key', evidence_dir=tmp_path, sdk_client=SDK(fail=True))
    with pytest.raises(JevError) as err:
        failing.ask({'doc': 'y'}, {'q': {'type': 'noul', 'instructions': 'Is `doc` y?'}})
    assert 'boom' in json.loads((tmp_path / err.value.payload_ref).read_text())['error']


# ---------------------------------------------------------------- same_intent
def _serp_ev(keyword, urls):
    ev = KeywordEvidence(keyword=keyword)
    ev.serp = SerpSnapshot(keyword=keyword, features=[], people_also_ask=[],
                           results=[SerpResult(position=i + 1, url=u, domain='d', title='t', result_type='organic')
                                    for i, u in enumerate(urls)])
    return ev


def test_same_intent_serp_overlap_then_jev_for_ambiguous():
    shared = [f'https://s{i}.test/' for i in range(5)]
    ev = {'a': _serp_ev('a', shared + ['https://a.test']), 'b': _serp_ev('b', shared),
          'c': _serp_ev('c', ['https://c.test']), 'd': _serp_ev('d', shared[:2] + ['https://x.test']), 'e': KeywordEvidence('e')}
    client = FakeJev()
    out = JevKeywordJudge(client).same_intent([('a', 'b'), ('a', 'c'), ('a', 'd'), ('a', 'e')], ev)
    assert out[('a', 'b')] == 1.0 and out[('a', 'c')] < 0.3
    assert out[('a', 'd')] == 0.8 and out[('a', 'e')] == 0.8          # asked Jev
    assert len(client.requests[0][1]) == 2
    offline = JevKeywordJudge(None).same_intent([('a', 'd'), ('a', 'e')], ev)
    assert offline == {('a', 'd'): 0.5, ('a', 'e'): 0.5}
    failing = JevKeywordJudge(FakeJev(raise_all=True)).same_intent([('a', 'e')], ev)
    assert failing == {('a', 'e'): 0.5}                                 # error -> unsettled, never "no"
