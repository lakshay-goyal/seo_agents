"""On-page stage with researched keywords (seo_reports/keywords/keyword_map.json): loading and matching, researched
keywords winning over the model hypothesis, keyword/question validators, fact-check blocking and the before/after
keyword coverage in the QA output. The model is a scripted fake; no network."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.checks.fact_check_freshness_check import check_fact_check
from seo_agents.onpage.checks.page_plan_check import check_questions
from seo_agents.onpage.onpage_analysis import run_onpage
from seo_agents.onpage.page_context.page_context_builder import PageContextBuilder
from seo_agents.onpage.reports.exploration_reports import ExplorationReportsLoader
from seo_agents.onpage.validation import content_validators as v
from seo_agents.onpage.writer.content_writer import ContentWriter, load_skill

from test_onpage import ABOUT, BRAND, FIXTURE, HOME, all_changes, plan_answers, run_fixture, text_of

PRIMARY = 'software engineer testsigma'
QUESTION = 'What projects has Lakshay Goyal built?'
PROMPT = 'who is lakshay goyal and what does he build'
KEYWORD_MAP = {
    'schema': 'keyword-research/1',
    'market': {'country': 'IN', 'location_code': 2356, 'language_code': 'en', 'language_name': 'English'},
    'business': {'brand': BRAND, 'site_type': 'personal', 'offering': 'Software engineering portfolio'},
    'generated_at': '2026-11-01T10:00:00+00:00', 'filters': {},
    'pages': {
        HOME: {'page_id': HOME, 'url': '/', 'intent': 'navigational', 'journey_stage': 'consideration', 'primary': PRIMARY,
               'secondary': ['lakshay goyal projects'], 'questions': [QUESTION], 'conversational': [PROMPT], 'short_tail': [],
               'cluster': {'role': 'pillar', 'pillar_url': None, 'related_urls': ['https://www.lakshaygoyal.in/blog', '/nope']},
               'competitor_urls': ['https://example.com/dev'], 'content_gaps': ['kubernetes deployment pipelines'],
               'iterations': 2, 'status': 'complete'},
        # Different key: matched to the /about page by url (trailing slash and origin ignored).
        'p_from_research': {'page_id': 'p_from_research', 'url': 'https://www.lakshaygoyal.in/about/', 'intent': 'navigational',
                            'journey_stage': None, 'primary': 'about lakshay goyal', 'secondary': [], 'questions': [],
                            'conversational': [], 'short_tail': [], 'cluster': {}, 'competitor_urls': [], 'content_gaps': [],
                            'iterations': 1, 'status': 'partial'},
    },
}

DIRECT_ANSWER = ('Lakshay Goyal is a Software Engineer at Testsigma working on test automation for web and mobile apps. He is a '
                 'curious builder with a bad habit of going down every rabbit hole, and he writes about software on his blog '
                 'for readers who like building things.')
PROJECTS_ANSWER = ('Lakshay Goyal has built Unlovable and FOZO as side projects. Unlovable builds web apps from a prompt, so a short '
                   'description becomes a working application. FOZO is a food ordering app for college campuses that lets students '
                   'order food. Both projects show how he builds web and mobile apps end to end.')
GOOD_HOME = {'title': 'Lakshay Goyal, Software Engineer at Testsigma',
             'description': 'Lakshay Goyal is a Software Engineer at Testsigma building web and mobile apps. See projects like '
                            'Unlovable and FOZO, and get in touch.',
             'h1': 'Software Engineer at Testsigma', 'outline': [], 'direct_answer': DIRECT_ANSWER,
             'sections': [{'replaces_id': None, 'heading': 'What projects has Lakshay Goyal built?', 'level': 2,
                           'blocks': [{'tag': 'p', 'text': PROJECTS_ANSWER}]}],
             'faq': [], 'fact_slots': {}, 'cta': {'text': 'Read Lakshay Goyal blog posts', 'href': '/blog'}}


def researched_answer(skill, packet, attempt):
    url = (packet.get('page') or {}).get('url')
    if skill == 'search-intent':
        # The model proposes its own keywords; with a researched plan they must be ignored.
        return {'brand_name': BRAND, 'site_type': 'personal', 'page_type': 'home' if url == '/' else 'about',
                'primary_topic': 'lakshay goyal developer', 'intent': 'informational', 'journey_stage': 'consideration',
                'primary_query': 'lakshay goyal developer', 'secondary_queries': ['react developer india'],
                'entities': [BRAND, 'Testsigma'], 'user_questions': ['Is Lakshay Goyal hiring?'],
                'cluster': {'role': 'standalone', 'pillar_url': None, 'related_urls': []}, 'answer_gap': 'no summary'}
    if skill == 'page-plan':
        if url != '/':
            return plan_answers(url, 2)
        if attempt == 0:  # title/H1 without the researched primary -> rejected
            return {**GOOD_HOME, 'title': 'Lakshay Goyal, Software Engineer for Web and Mobile'}
        if attempt == 1:  # the question keyword is not covered -> rejected
            return {**GOOD_HOME, 'sections': []}
        return GOOD_HOME
    if skill == 'media-links':
        return {'alts': [], 'anchors': [], 'new_links': []}
    if skill == 'eeat-aeo-advice':
        items = [{'topic': 'geo.same_as', 'text': 'List the GitHub and LinkedIn profiles as sameAs on the Person entity.'}]
        if attempt == 0 and packet['page_type'] == 'home':  # no visible FAQ on the page -> FAQPage advice rejected
            items.append({'topic': 'aeo.faq_schema', 'text': 'Add FAQPage markup for the questions on this page.'})
        return {'advice': items, 'missing_facts': []}
    raise AssertionError('unexpected skill ' + skill)


class FakeModel:
    def __init__(self, calls, tier):
        self.calls, self.tier = calls, tier

    def invoke(self, messages):
        system = text_of(messages[0].content)
        skill = system.split('# Skill: ', 1)[1].split('\n', 1)[0].strip()
        packet = json.loads(text_of(messages[1].content))
        key = (skill, json.dumps(packet.get('page', {}).get('url') if isinstance(packet.get('page'), dict) else packet.get('page_type')))
        attempt = sum(1 for c in self.calls if c[0] == key)
        self.calls.append((key, self.tier, packet))
        return type('R', (), {'content': json.dumps(researched_answer(skill, packet, attempt))})()


def copy_fixture(with_map):
    tmp = tempfile.mkdtemp()
    root = Path(tmp) / 'seo_reports'
    shutil.copytree(FIXTURE, root)
    if with_map:
        (root / 'keywords').mkdir()
        (root / 'keywords' / 'keyword_map.json').write_text(json.dumps(KEYWORD_MAP))
    return tmp, root


def context(root, page_id=HOME):
    reports = ExplorationReportsLoader().load(root)
    return PageContextBuilder().build(reports, page_id, run_date='2026-11-02')


class KeywordMapLoadingTest(unittest.TestCase):
    def setUp(self):
        self.tmp, self.root = copy_fixture(with_map=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_plan_matched_by_page_id_then_url(self):
        home = context(self.root, HOME)
        self.assertEqual(home['keyword_plan']['primary'], PRIMARY)
        self.assertEqual(home['keyword_plan']['cluster']['related_urls'], ['/blog', '/nope'])  # origin stripped
        self.assertEqual(home['keyword_research']['market']['country'], 'IN')
        about = context(self.root, ABOUT)
        self.assertEqual(about['keyword_plan']['primary'], 'about lakshay goyal')

    def test_no_keyword_map_means_no_plan(self):
        tmp, root = copy_fixture(with_map=False)
        try:
            ctx = context(root)
            self.assertIsNone(ctx['keyword_plan'])
            self.assertIsNone(ctx['keyword_research'])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        res = run_fixture()  # the original golden run (no keyword_map.json): model hypotheses, unchanged behaviour
        try:
            page = res['pages'][HOME]
            self.assertEqual(page['intent']['query_source'], 'model-hypothesis')
            self.assertEqual(page['intent']['primary_query'], 'lakshay goyal software engineer')
            self.assertIsNone(page['keyword_research'])
            self.assertEqual(page['keyword_coverage']['source'], 'model-hypothesis')
            self.assertIn(page['quality']['verdict'], ('pass', 'needs_work', 'fail'))
        finally:
            shutil.rmtree(res['tmp'], ignore_errors=True)


class KeywordValidatorsTest(unittest.TestCase):
    def test_primary_must_be_in_title(self):
        with self.assertRaises(ValueError):
            v.primary_in('Lakshay Goyal, Software Engineer for Web and Mobile', PRIMARY, 'title')
        v.primary_in('Lakshay Goyal, Software Engineer at Testsigma', PRIMARY, 'title')
        v.primary_in('Testsigma software engineers', PRIMARY, 'title')  # word order / plural may vary

    def test_keyword_stuffing_caps(self):
        with self.assertRaises(ValueError):
            v.keyword_caps('Lakshay Goyal projects and more lakshay goyal projects', ['lakshay goyal projects'], name='title', short=True)
        body = ' '.join(['Lakshay Goyal projects are built for the web.'] * 6)
        with self.assertRaises(ValueError):
            v.keyword_caps(body, ['lakshay goyal projects'], name='body')
        v.keyword_caps('Lakshay Goyal projects ship to the web.', ['lakshay goyal projects'], name='title', short=True)

    def test_uncovered_question_is_rejected(self):
        tmp, root = copy_fixture(with_map=True)
        try:
            ctx = context(root)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        keywords = {'questions': [QUESTION], 'questions_answered_now': []}
        with self.assertRaisesRegex(ValueError, 'not covered'):
            check_questions({'sections': [], 'faq': [], 'outline': []}, keywords, ctx, {}, [BRAND])
        short = {'sections': [{'heading': QUESTION, 'blocks': [{'tag': 'p', 'text': 'Unlovable and FOZO.'}]}], 'faq': [], 'outline': []}
        with self.assertRaisesRegex(ValueError, 'words'):
            check_questions(short, keywords, ctx, {}, [BRAND])
        # A brand-only overlap ("Lakshay Goyal, Software Engineer") never counts as covering the question.
        self.assertFalse(v.covers_question(QUESTION, 'Lakshay Goyal, Software Engineer', ignore=[BRAND]))
        ok = {'sections': [{'heading': QUESTION, 'blocks': [{'tag': 'p', 'text': PROJECTS_ANSWER}]}], 'faq': [], 'outline': []}
        self.assertEqual(check_questions(ok, keywords, ctx, {}, [BRAND]), {QUESTION: 'sections[0]'})
        declared = {'sections': [], 'faq': [], 'outline': [], 'unanswerable': [{'question': QUESTION, 'needed': 'list of shipped projects'}]}
        self.assertEqual(check_questions(declared, keywords, ctx, {}, [BRAND]), {})

    def test_fact_check_blocks_unsupported_claim(self):
        tmp, root = copy_fixture(with_map=False)
        try:
            ctx = context(root)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        invented = ops.make_change(ctx, 'page_plan', 'content.section[0]', None,
                                   {'heading': 'Awards', 'blocks': [{'tag': 'p', 'text': 'He won the Golden Widget award from '
                                                                     'Acme Corporation for enterprise migrations in Berlin.'}]},
                                   op='insert_node', target=None)
        grounded = ops.make_change(ctx, 'page_plan', 'content.section[1]', None,
                                   {'heading': 'Projects', 'blocks': [{'tag': 'p', 'text': 'Unlovable builds web apps from a prompt. '
                                                                       'FOZO is a food ordering app for college campuses.'}]},
                                   op='insert_node', target=None)
        prior = {'intent': {'status': 'done', 'data': {'primary_query': 'lakshay goyal software engineer', 'entities': [BRAND]}},
                 'page_plan': {'status': 'done', 'changes': [invented, grounded],
                               'data': {'direct_answer': 'Lakshay Goyal is a software engineer.'}}}
        res = check_fact_check(ctx, prior)
        self.assertEqual(invented['status'], 'blocked')
        self.assertEqual(invented['blocked_by'], 'fact_check')
        self.assertTrue(invented['evidence']['fact_check']['unsupported_claims'])
        self.assertNotEqual(grounded['status'], 'blocked')
        self.assertEqual(res['data']['quality']['verdict'], 'fail')
        self.assertIn(invented['id'], res['data']['quality']['blocked_changes'])
        self.assertTrue(any(invented['id'] == i['change'] for i in res['client_inputs']))

    def test_page_plan_prompt_selects_keyword_principle(self):
        plan, _ = load_skill('page-plan')
        self.assertIn('## P10', plan)
        self.assertNotIn('## P10', load_skill('search-intent')[0])  # principles stay per skill


class KeywordMapGoldenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, root = copy_fixture(with_map=True)
        cls.calls = []
        writer = ContentWriter(model_factory=lambda tier, label=None: FakeModel(cls.calls, tier), attempts=3)
        with redirect_stdout(io.StringIO()):
            run_onpage(root, run_date='2026-11-02', writer=writer, only_pages=[HOME, ABOUT])
        out = root / 'onpage'
        cls.pages = {pid: json.loads((out / 'pages' / (pid + '.json')).read_text()) for pid in (HOME, ABOUT)}
        cls.index = json.loads((out / 'index.json').read_text())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_check_finished(self):
        for page in self.pages.values():
            self.assertEqual({k: c['status'] for k, c in page['checks'].items() if c['status'] != 'done'}, {})

    def test_researched_keywords_win_over_model_hypothesis(self):
        intent = self.pages[HOME]['intent']
        self.assertEqual(intent['query_source'], 'keyword-research')
        self.assertEqual(intent['primary_query'], PRIMARY)
        self.assertEqual(intent['secondary_queries'], ['lakshay goyal projects'])
        self.assertEqual(intent['user_questions'], [QUESTION])
        self.assertEqual(intent['intent'], 'navigational')  # model said informational
        self.assertEqual(intent['cluster']['related_urls'], ['/blog'])
        self.assertEqual(intent['model_hypothesis_ignored']['primary_query'], 'lakshay goyal developer')
        self.assertEqual(intent['market']['country'], 'IN')
        self.assertEqual(intent['provenance']['plan_status'], 'complete')
        packet = next(p for (k, _, p) in self.calls if k[0] == 'search-intent' and k[1] == '"/"')
        self.assertEqual(packet['researched']['primary_query'], PRIMARY)
        about = self.pages[ABOUT]['intent']
        self.assertEqual(about['primary_query'], 'about lakshay goyal')  # matched by url
        self.assertEqual(about['journey_stage'], 'consideration')  # plan had none -> the model decides
        self.assertEqual(self.index['pages'][0]['query_source'], 'keyword-research')

    def test_keyword_plan_measures_secondary_questions_and_gaps(self):
        kp = self.pages[HOME]['keyword_plan']
        self.assertEqual(kp['source'], 'keyword-research')
        self.assertIn('lakshay goyal projects', kp['secondary_placements_current'])
        self.assertFalse(kp['questions_current'][QUESTION]['answered'])
        self.assertIn(PROMPT, kp['conversational_current'])
        gaps = ' | '.join(kp['alignment_gaps'])
        self.assertIn(f'primary "{PRIMARY}" missing from title', gaps)
        self.assertIn('competitors cover: kubernetes deployment pipelines', gaps)

    def test_page_plan_validators_repair_missing_primary_and_question(self):
        meta = self.pages[HOME]['checks']['page_plan']['meta']
        self.assertEqual(meta['attempts'], 3)
        self.assertIn('primary keyword', meta['errors'][0])
        self.assertIn('not covered', meta['errors'][1])
        plan = self.pages[HOME]['checks']['page_plan']['data']
        self.assertEqual(plan['questions_covered'], {QUESTION: 'sections[0]'})
        self.assertEqual(plan['cta'], {'text': 'Read Lakshay Goyal blog posts', 'href': '/blog'})
        advice_meta = self.pages[HOME]['checks']['advice']['meta']
        self.assertIn('FAQPage', advice_meta['errors'][0])  # no visible FAQ -> no FAQPage advice

    def test_before_after_with_served_keywords(self):
        page = self.pages[HOME]
        changes = all_changes(page)
        title = next(c for c in changes if c['field'] == 'head.title')
        self.assertIn(PRIMARY, title['serves'])
        section = next(c for c in changes if c['field'] == 'content.section[0]')
        self.assertIn(QUESTION, section['serves'])
        self.assertTrue(any(c['field'] == 'content.cta' for c in changes))
        coverage = page['keyword_coverage']
        self.assertEqual(coverage['source'], 'keyword-research')
        self.assertEqual(coverage['primary_placements']['title']['before'], False)
        self.assertIn(coverage['primary_placements']['title']['after'], (True, 'blocked'))  # shared-shell head is blocked
        self.assertFalse(coverage['questions'][QUESTION]['before'])
        self.assertIn(coverage['questions'][QUESTION]['after'], (True, 'needs_input'))
        self.assertEqual(page['checklist']['questions_answered']['before'], False)

    def test_quality_verdict_recorded(self):
        quality = self.pages[HOME]['quality']
        self.assertIn(quality['verdict'], ('pass', 'needs_work', 'fail'))
        self.assertTrue(quality['helpfulness']['primary_intent_answered'])
        self.assertEqual(quality['helpfulness']['questions'][QUESTION], 'answered (proposed)')
        self.assertEqual(quality['helpfulness']['content_gaps_open'], ['kubernetes deployment pipelines'])
        self.assertEqual(self.index['pages'][0]['quality'], quality['verdict'])
        self.assertFalse(quality['blocked_changes'], quality)  # every proposed sentence is grounded in the page facts


if __name__ == '__main__':
    unittest.main()
