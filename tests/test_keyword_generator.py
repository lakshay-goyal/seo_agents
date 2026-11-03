import json
import re
import unittest
from unittest import mock

from seo_agents.keyword_research.contracts import (BusinessProfile, Decision, JudgeVerdict, KeywordCandidate,
                                                   KeywordEvidence, KeywordKind, KeywordRecord, Market, Metric,
                                                   PageBrief, RejectionCode, Source, Tail)
from seo_agents.keyword_research.generation import (LlmKeywordGenerator, normalize_keyword, validate_business_profile,
                                                    validate_candidates)
from seo_agents.keyword_research.generation import llm_writer
from seo_agents.keyword_research.generation.llm_writer import KeywordWriter, load_skill, skill_ladder
from seo_agents.lib.models import AGENT_MODELS
from seo_agents.lib.prompts import KEYWORD_RESEARCH_PROMPTS_DIR


def text_of(content):
    if isinstance(content, list):
        return ''.join(part.get('text', '') for part in content)
    return content


class FakeModel:
    """Returns canned answers in order; records (tier, label, temperature, messages)."""

    def __init__(self, script, calls, tier, label, temperature):
        self.script, self.calls = script, calls
        self.tier, self.label, self.temperature = tier, label, temperature

    def invoke(self, messages):
        self.calls.append({'tier': self.tier, 'label': self.label, 'temperature': self.temperature,
                           'messages': messages})
        answer = self.script.pop(0)
        return type('R', (), {'content': answer if isinstance(answer, str) else json.dumps(answer)})()


def fake_writer(script, attempts=3):
    calls = []

    def factory(tier, label=None, temperature=0):
        return FakeModel(script, calls, tier, label, temperature)
    return KeywordWriter(model_factory=factory, attempts=attempts), calls


SITE_PACKET = {
    'brand_candidates': ['Acme Bikes'],
    'site_type_hint': 'ecommerce',
    'pages': [{'page_id': 'p-home', 'url': '/', 'title': 'Acme Bikes - Electric bikes for city commuters'},
              {'page_id': 'p-guide', 'url': '/guides/ebike-battery', 'title': 'How long does an e-bike battery last'},
              {'page_id': 'p-folding', 'url': '/bikes/folding', 'title': 'Folding electric bikes'}],
    'copy': ['Acme Bikes sells folding electric bikes for city commuters. Unlike rivalcycles.com we ship free.'],
}

PROFILE = {'brand': 'Acme Bikes', 'site_type': 'ecommerce', 'offering': 'Sells folding electric bikes for commuters.',
           'audience': ['city commuters'], 'value_props': ['free shipping'],
           'seed_topics': ['folding electric bikes'], 'competitors': ['rivalcycles.com'], 'excluded_terms': []}

GUIDE = PageBrief(page_id='p-guide', url='/guides/ebike-battery', absolute_url=None, page_type='article',
                  title='How long does an e-bike battery last', description='Battery life guide',
                  headings=['How long does an e-bike battery last', 'Charging tips'],
                  copy=['Most lithium e-bike batteries last 3 to 5 years.'], images=[], lang='en')

BUSINESS = BusinessProfile(brand='Acme Bikes', site_type='ecommerce', offering='Folding electric bikes.',
                           competitors=['rivalcycles.com'], excluded_terms=['rivalcycles'])

NEEDS = {'primary': 1, 'secondary': 2, 'question': 1, 'conversational': 1, 'short_tail': 0}


def cand(keyword, kind='secondary', tail='long', intent='informational', rationale='Owners want battery lifespan; '
         'the guide answers it directly.', **extra):
    return {'keyword': keyword, 'kind': kind, 'tail': tail, 'intent': intent, 'rationale': rationale, **extra}


def good_answer():
    return {'page_intent': 'informational', 'candidates': [
        cand('how long does an e-bike battery last', kind='primary'),
        cand('e-bike battery lifespan in years'),
        cand('lithium e-bike battery charging tips'),
        cand('how often should i charge my e-bike battery', kind='question'),
        cand('how long will a lithium battery on a folding e-bike last for daily commuting', kind='conversational'),
    ]}


def validate(answer, **kw):
    params = dict(page=GUIDE, business=BUSINESS, needs=NEEDS, competitor_keywords=[], blocked=[], limit=10)
    params.update(kw)
    return validate_candidates(answer, **params)


def record(keyword, page_id='p-guide', kind=KeywordKind.SECONDARY, codes=(), reasons=(), status='rejected'):
    candidate = KeywordCandidate(keyword=keyword, page_id=page_id, kind=kind, source=Source.LLM_SEED, tail=Tail.LONG)
    verdict = JudgeVerdict(keyword=keyword, page_id=page_id,
                           decision=Decision.REJECT if codes else Decision.ACCEPT, composite_score=None,
                           factor_scores={}, judgments={}, rejection_codes=list(codes), reasons=list(reasons))
    return KeywordRecord(candidate=candidate, evidence=None, verdict=verdict, final_status=status)


class CandidateValidationTests(unittest.TestCase):
    def test_good_answer_passes(self):
        batch = validate(good_answer())
        self.assertEqual(len(batch.candidates), 5)
        self.assertEqual(batch.page_intent, 'informational')
        self.assertTrue(all(c.source is Source.LLM_SEED and c.page_id == 'p-guide' for c in batch.candidates))

    def test_normalize(self):
        self.assertEqual(normalize_keyword('  How   Long Does It LAST?  '), 'how long does it last')
        self.assertEqual(normalize_keyword('Owner’s guide'), "owner's guide")

    def test_metric_fields_are_rejected(self):
        answer = good_answer()
        answer['candidates'][0]['search_volume'] = 1200
        with self.assertRaisesRegex(ValueError, 'metric field'):
            validate(answer)
        answer = good_answer()
        answer['candidates'][1]['difficulty'] = 'low'
        with self.assertRaisesRegex(ValueError, 'metric field'):
            validate(answer)

    def single_error(self, item):
        answer = good_answer()
        answer['candidates'].append(item)
        batch = validate(answer)
        self.assertEqual(len(batch.dropped), 1, batch.dropped)
        return batch.dropped[0]

    def test_per_candidate_rules(self):
        self.assertIn('short tail', self.single_error(cand('ebike battery')))
        self.assertIn('must have tail "long"', self.single_error(cand('best ebike battery care', tail='short')))
        self.assertIn('read as a question', self.single_error(cand('e-bike battery replacement cost guide', kind='question')))
        self.assertIn('competitor brand', self.single_error(cand('rivalcycles battery life comparison')))
        self.assertIn('URLs', self.single_error(cand('acmebikes.com battery guide page')))
        self.assertIn('duplicate', self.single_error(cand('E-bike battery lifespan in years ')))
        self.assertIn('rationale', self.single_error(cand('ebike battery storage in winter', rationale='')))
        self.assertIn('rationale', self.single_error(cand('ebike battery storage in winter', rationale='x' * 241)))
        self.assertIn('cannot be satisfied', self.single_error(
            cand('buy replacement e-bike battery online', intent='transactional')))
        self.assertIn('page language', self.single_error(cand('батарея для электровелосипеда срок службы')))
        self.assertIn('max 10', self.single_error(cand(' '.join(['word'] * 11))))

    def test_short_tail_allowed_when_requested(self):
        needs = {**NEEDS, 'short_tail': 1}
        answer = good_answer()
        answer['candidates'] += [cand('ebike battery', tail='long'), cand('battery care', tail='short')]
        self.assertIn('must have tail "short"', validate(answer, needs=needs).dropped[0])
        answer = good_answer()
        answer['candidates'].append(cand('ebike battery', tail='short'))
        batch = validate(answer, needs={**NEEDS, 'short_tail': 1})
        self.assertIn('ebike battery', [c.keyword for c in batch.candidates])
        with self.assertRaisesRegex(ValueError, 'short tail was requested'):
            validate(good_answer(), needs={**NEEDS, 'short_tail': 1})

    def test_page_intent_must_fit_page_type(self):
        answer = good_answer()
        answer['page_intent'] = 'transactional'
        with self.assertRaisesRegex(ValueError, 'does not fit a article page'):
            validate(answer)

    def test_needs_respected(self):
        answer = good_answer()
        answer['candidates'] = [c for c in answer['candidates'] if c['kind'] != 'question']
        with self.assertRaisesRegex(ValueError, 'need 1 valid "question"'):
            validate(answer)
        batch = validate(good_answer(), needs={**NEEDS, 'conversational': 0})
        self.assertNotIn('conversational', [c.kind.value for c in batch.candidates])
        self.assertTrue(any('not needed' in d for d in batch.dropped))

    def test_too_many_invalid_triggers_repair(self):
        answer = good_answer()
        answer['candidates'] += [cand('rivalcycles battery test one'), cand('rivalcycles battery test two')]
        with self.assertRaisesRegex(ValueError, 'invalid'):
            validate(answer)

    def test_blocked_keywords_and_competitor_adaptation(self):
        answer = good_answer()
        answer['candidates'].append(cand('e-bike battery replacement cost guide', adapted_from='ebike battery cost'))
        competitor = KeywordCandidate(keyword='ebike battery cost', page_id='p-guide', kind=KeywordKind.SECONDARY,
                                      source=Source.COMPETITOR_REVERSE, tail=Tail.LONG,
                                      competitor_url='https://other.example/x')
        batch = validate(answer, competitor_keywords=[competitor], blocked=['E-Bike battery lifespan in years'])
        keywords = {c.keyword: c for c in batch.candidates}
        self.assertNotIn('e-bike battery lifespan in years', keywords)
        adapted = keywords['e-bike battery replacement cost guide']
        self.assertIs(adapted.source, Source.COMPETITOR_REVERSE)
        self.assertEqual(adapted.competitor_url, 'https://other.example/x')

    def test_non_latin_language(self):
        page = PageBrief(page_id='p', url='/', absolute_url=None, page_type=None, title='', description='',
                         headings=[], copy=[], images=[], lang='ru')
        answer = {'page_intent': 'informational', 'candidates': [
            cand('срок службы батареи электровелосипеда', kind='primary')]}
        batch = validate(answer, page=page, needs={'primary': 1})
        self.assertEqual(len(batch.candidates), 1)


class BusinessProfileTests(unittest.TestCase):
    def test_grounded_profile(self):
        profile = validate_business_profile(PROFILE, SITE_PACKET, Market())
        self.assertEqual(profile.brand, 'Acme Bikes')
        self.assertIn('rivalcycles', profile.excluded_terms)

    def test_brand_must_appear_in_packet(self):
        with self.assertRaisesRegex(ValueError, 'does not appear in the packet'):
            validate_business_profile({**PROFILE, 'brand': 'Bikely'}, SITE_PACKET)
        with self.assertRaisesRegex(ValueError, 'not mentioned in the packet'):
            validate_business_profile({**PROFILE, 'competitors': ['trek.com']}, SITE_PACKET)

    def test_profile_repair_loop(self):
        writer, calls = fake_writer([{**PROFILE, 'brand': 'Bikely Inc'}, PROFILE])
        generator = LlmKeywordGenerator(writer)
        profile = generator.profile_business(SITE_PACKET, Market())
        self.assertEqual(profile.brand, 'Acme Bikes')
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]['label'], 'keyword_research:business-profile')
        self.assertIn('does not appear in the packet', text_of(calls[1]['messages'][-1].content))
        self.assertEqual(generator.last_meta['attempts'], 2)
        self.assertEqual([p['url'] for p in generator.site_pages], ['/', '/guides/ebike-battery', '/bikes/folding'])


class GeneratorTests(unittest.TestCase):
    def generator(self, script):
        writer, calls = fake_writer(script)
        generator = LlmKeywordGenerator(writer, site_pages=SITE_PACKET['pages'])
        return generator, calls

    def run_generate(self, generator, **kw):
        params = dict(needs=NEEDS, competitor_keywords=[], accepted=[], rejected=[], iteration=0, limit=10)
        params.update(kw)
        return generator.generate(GUIDE, BUSINESS, **params)

    def test_repair_loop_on_bad_output(self):
        bad = good_answer()
        bad['candidates'][0]['volume'] = 900
        generator, calls = self.generator(['not json at all', bad, good_answer()])
        candidates = self.run_generate(generator)
        self.assertEqual(len(candidates), 5)
        self.assertEqual(len(calls), 3)
        self.assertEqual([c['tier'] for c in calls], ['mid', 'mid', 'mid'])
        self.assertEqual(calls[0]['temperature'], 0.4)
        self.assertEqual(calls[0]['label'], 'keyword_research:keyword-ideation')
        self.assertIn('metric field', text_of(calls[2]['messages'][-1].content))
        self.assertEqual(generator.last_meta['attempts'], 3)

    def test_all_attempts_fail_returns_empty(self):
        generator, _ = self.generator(['{}', '{}', '{}'])
        self.assertEqual(self.run_generate(generator), [])
        self.assertTrue(generator.last_meta['failed'])

    def test_feedback_in_packet_and_rejected_not_reproposed(self):
        rejected = [record('e-bike battery lifespan in years', codes=[RejectionCode.HIGH_DIFFICULTY],
                           reasons=['kd 62 > 40'])]
        accepted = [record('folding electric bike for apartment', page_id='p-folding', status='primary')]
        competitor = KeywordCandidate(keyword='ebike battery cost', page_id='p-guide', kind=KeywordKind.SECONDARY,
                                      source=Source.COMPETITOR_REVERSE, tail=Tail.LONG, competitor_url='https://c.example/a')
        evidence = {'ebike battery cost': KeywordEvidence(keyword='ebike battery cost',
                                                          metrics={'search_volume': Metric(320), 'keyword_difficulty': Metric(None)})}
        answer = good_answer()
        answer['candidates'] += [cand('folding electric bike for apartment'), cand('e-bike battery care in cold weather')]
        generator, calls = self.generator([answer])
        candidates = self.run_generate(generator, rejected=rejected, accepted=accepted, iteration=1,
                                       competitor_keywords=[competitor], competitor_evidence=evidence)
        keywords = [c.keyword for c in candidates]
        self.assertNotIn('e-bike battery lifespan in years', keywords)
        self.assertNotIn('folding electric bike for apartment', keywords)
        self.assertTrue(all(c.iteration == 1 for c in candidates))
        packet = json.loads(text_of(calls[0]['messages'][1].content))
        self.assertEqual(packet['rejected_on_page'][0]['codes'], ['high_difficulty'])
        self.assertEqual(packet['rejection_summary'], {'high_difficulty': 1})
        self.assertEqual(packet['accepted_elsewhere'], [{'keyword': 'folding electric bike for apartment',
                                                         'page_url': '/bikes/folding'}])
        self.assertEqual(packet['competitor_keywords'][0], {'keyword': 'ebike battery cost',
                                                            'competitor_url': 'https://c.example/a', 'volume': 320})
        self.assertNotIn('/guides/ebike-battery', [p['url'] for p in packet['other_pages']])
        self.assertEqual(packet['needs'], NEEDS)

    def test_nothing_needed_skips_model(self):
        generator, calls = self.generator([])
        self.assertEqual(self.run_generate(generator, needs={k: 0 for k in NEEDS}), [])
        self.assertEqual(calls, [])


class ModelLayerTests(unittest.TestCase):
    def test_every_keyword_research_model_entry_has_a_prompt(self):
        keys = [k for k in AGENT_MODELS if k.startswith('keyword_research.')]
        self.assertEqual(sorted(keys), ['keyword_research.business-profile', 'keyword_research.keyword-ideation'])
        for key in keys:
            skill = key.partition('.')[2]
            self.assertTrue((KEYWORD_RESEARCH_PROMPTS_DIR / skill / 'SKILL.md').exists(), key)

    def test_prompt_frontmatter_holds_no_model_settings_and_limits_render(self):
        for path in KEYWORD_RESEARCH_PROMPTS_DIR.glob('*/SKILL.md'):
            front = path.read_text().split('---')[1]
            for key in ('tier:', 'ladder:', 'votes:', 'temperature:'):
                self.assertNotIn('\n' + key, front, path)
            system, meta = load_skill(path.parent.name)
            self.assertIsNone(re.search(r'\{\{\w+\}\}', system), path)
            self.assertIn('Output discipline', system)
            self.assertIn('K1 Grounded', system)

    def test_ladders(self):
        self.assertEqual(skill_ladder('business-profile'), ['small', 'haiku', 'mid'])
        self.assertEqual(skill_ladder('keyword-ideation'), ['mid', 'mid'])

    def test_mid_reasoning_effort(self):
        with mock.patch('seo_agents.llm.chat_model_factory.get_chat_model') as get_model, \
                mock.patch.dict('os.environ', {}, clear=False) as env:
            env.pop('SEO_KEYWORD_REASONING_EFFORT', None)
            llm_writer.default_factory('mid', 'keyword_research:keyword-ideation', temperature=0.4)
            self.assertEqual(get_model.call_args.kwargs['reasoning_effort'], 'low')
            llm_writer.default_factory('small', 'keyword_research:business-profile')
            self.assertIsNone(get_model.call_args.kwargs['reasoning_effort'])
            env['SEO_KEYWORD_REASONING_EFFORT'] = 'medium'
            llm_writer.default_factory('mid', 'x')
            self.assertEqual(get_model.call_args.kwargs['reasoning_effort'], 'medium')


if __name__ == '__main__':
    unittest.main()
