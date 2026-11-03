"""On-page stage: JSON-only inputs, validators, scope gate, dedup and golden checks on a PR#8-like Vite SPA fixture
(5 routes sharing one index.html head). The model is a scripted fake; no network."""

from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from seo_agents.onpage import ops
from seo_agents.onpage import validators as v
from seo_agents.onpage.checks import CHECKS
from seo_agents.onpage.graph import run_onpage
from seo_agents.onpage.writer import Writer, load_skill, skill_catalog

FIXTURE = Path(__file__).parent / 'fixtures' / 'onpage' / 'vite-spa' / 'seo_reports'
HOME, ABOUT = 'p_42099b4af0', 'p_69bbc6be2c'
BRAND = 'Lakshay Goyal'


def text_of(content):
    if isinstance(content, list):
        return ''.join(p.get('text', '') if isinstance(p, dict) else str(p) for p in content)
    return content


def link_id(href, anchor, nth=0):
    return 'lnk_' + hashlib.sha1(f'{href}|{anchor}|{nth}'.encode()).hexdigest()[:8]


HOME_ANSWER = ('Lakshay Goyal is a Software Engineer at Testsigma who builds web and mobile apps. He works on test automation and '
               'ships side projects such as Unlovable, which builds web apps from a prompt, and FOZO, a food ordering app for college '
               'campuses. Read his blog for notes on software.')
CONTACT_FAQ = {'question': 'How can I contact Lakshay Goyal?',
               'answer': 'You can reach him by email or through his GitHub and LinkedIn profiles, which are linked on every page of the '
                         'site. The contact links sit in the side rail next to the page content.'}


def plan_answers(url, attempt):
    if url == '/':
        good = {'title': 'Lakshay Goyal, Software Engineer for Web and Mobile',
                'description': 'Lakshay Goyal is a Software Engineer at Testsigma building web and mobile apps. See projects like '
                               'Unlovable and FOZO, and get in touch.',
                'h1': 'Lakshay Goyal, Software Engineer',
                'outline': [{'level': 2, 'text': 'Work experience at Testsigma', 'replaces_id': 'h1'}],
                'direct_answer': HOME_ANSWER, 'sections': [], 'faq': [CONTACT_FAQ], 'fact_slots': {'f1': 'direct_answer'}}
        if attempt == 0:  # rewrites a heading rendered from experiences.json (data template) -> rejected
            return {**good, 'outline': [{'level': 2, 'text': 'Where does Lakshay work?', 'replaces_id': 'h2'}]}
        if attempt == 1:  # FAQ restates an H2 -> rejected
            return {**good, 'faq': [{'question': 'What is the work experience?', 'answer': CONTACT_FAQ['answer']}]}
        return good
    return {'title': 'About Lakshay Goyal, Software Engineer',
            'description': 'About Lakshay Goyal, a Software Engineer building thoughtful mobile and web products, and the curiosity '
                           'that keeps him exploring software.',
            'h1': 'About Lakshay Goyal and his work', 'outline': [],
            'direct_answer': 'Lakshay Goyal is a Software Engineer building thoughtful mobile and web products. Here is a little about '
                             'him: the curiosity that pulled him into software, and the interests that keep him exploring beyond it, '
                             'from side projects to reading and writing about software.',
            'sections': [], 'faq': [], 'fact_slots': {}}


def answer_for(skill, packet, attempt):
    url = (packet.get('page') or {}).get('url')
    if skill == 'search-intent':
        return {'brand_name': BRAND, 'site_type': 'personal', 'page_type': 'home' if url == '/' else 'about',
                'primary_topic': 'lakshay goyal software engineer', 'intent': 'navigational', 'journey_stage': 'consideration',
                'primary_query': 'lakshay goyal software engineer', 'secondary_queries': ['lakshay goyal projects'],
                'entities': [BRAND, 'Testsigma'], 'user_questions': ['How can I contact Lakshay Goyal?'],
                'cluster': {'role': 'pillar', 'pillar_url': None, 'related_urls': ['/blog', '/nope']}, 'answer_gap': 'no summary'}
    if skill == 'page-plan':
        return plan_answers(packet['page']['url'], attempt)
    if skill == 'media-links':
        return {'alts': [{'id': i['id'], 'alt': f"Screenshot of the {i['src'].split('/')[-1].split('.')[0]} project interface"}
                         for i in packet['images']],
                'anchors': [{'id': l['id'], 'anchor': 'Lakshay Goyal blog posts', 'reason': 'generic'} for l in packet['links']],
                'new_links': []}
    if skill == 'eeat-aeo-advice':
        items = [{'topic': 'eeat.experience', 'text': 'Link each project to its live demo and source code so visitors can verify the work.'}]
        if attempt == 0 and url is None and packet.get('page_type') == 'home':
            items.append({'topic': 'eeat.authorship', 'text': 'Add an author box with a bio at the end of the page.'})
        return {'advice': items, 'missing_facts': ['[VERIFY: years of experience]']}
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
        self.calls.append((key, self.tier))
        return type('R', (), {'content': json.dumps(answer_for(skill, packet, attempt))})()


def run_fixture(only=(HOME, ABOUT)):
    calls = []
    writer = Writer(model_factory=lambda tier, label=None: FakeModel(calls, tier), attempts=3)
    tmp = tempfile.mkdtemp()
    root = Path(tmp) / 'seo_reports'
    shutil.copytree(FIXTURE, root)
    with redirect_stdout(io.StringIO()):
        state = run_onpage(root, run_date='2026-11-02', writer=writer, only_pages=list(only))
    out = root / 'onpage'
    pages = {pid: json.loads((out / 'pages' / (pid + '.json')).read_text()) for pid in only}
    return {'calls': calls, 'state': state, 'out': out, 'pages': pages, 'tmp': tmp,
            'site': json.loads((out / 'site.json').read_text()), 'index': json.loads((out / 'index.json').read_text()),
            'inputs': json.loads((out / 'client_inputs.json').read_text()),
            'html': (out / 'pages' / (HOME + '.expected.html')).read_text()}


def all_changes(page):
    return page['changes'] + page['changes_more']


class OnpageUnitTest(unittest.TestCase):
    def test_skills_match_checks_and_principles_are_subset(self):
        names = {s['name'] for s in skill_catalog()}
        for _, skill, _, uses_llm in CHECKS:
            self.assertIn(skill, names)
            system, meta = load_skill(skill)
            self.assertEqual(meta.get('kind'), 'llm' if uses_llm else 'deterministic')
            self.assertNotIn('{{', system)
        plan, meta = load_skill('page-plan')
        self.assertIn('P6 Fact-check', plan)
        self.assertNotIn('## P5', plan)  # per-skill principle subset, not the whole file
        self.assertIn('40-60 words', plan)  # limits rendered from policy.LIMITS (validator uses the same constant)
        self.assertEqual(meta['tier'], 'mid')
        self.assertEqual(load_skill('media-links')[1]['tier'], 'small')
        self.assertEqual(sum(1 for c in CHECKS if c[3]), 4)  # 4 model calls per page (was 10)

    def test_validators(self):
        with self.assertRaises(ValueError):
            v.grounded('We served 500 clients', 'Ada builds apps')
        with self.assertRaises(ValueError):
            v.grounded('Ada has two years of experience', 'Ada builds apps')  # spelled-out numbers are grounded too
        v.grounded('Ada has two years of experience', 'Ada: 2 years building apps')
        v.grounded('Since 2021 [VERIFY: 40 clients]', 'since 2021')
        with self.assertRaises(ValueError):
            v.entity_cap([('a', 'Ada Lane builds. Ada Lane ships.')], 'Ada Lane')
        v.entity_cap([('a', 'Ada Lane builds apps. She ships.'), ('b', 'Ada Lane writes.')], 'Ada Lane')
        with self.assertRaises(ValueError):
            v.keeps_client_terms('Builds browser products', 'builds web products')
        v.keeps_client_terms('Builds web products', 'builds web products')
        self.assertTrue(v.similar('What is the work experience?', 'Work Experience'))
        self.assertFalse(v.similar('How can I contact Ada?', 'Work Experience'))
        with self.assertRaises(ValueError):
            v.check_anchor('click here')
        with self.assertRaises(ValueError):
            v.heading_hierarchy([1, 3])

    def test_gate_per_route_stack_is_not_blocked(self):
        ctx = {'page_id': 'p1', 'url': '/about', 'head_scope': 'per-route', 'recipe': {}, 'framework': 'static-html', 'template': 'about.html'}
        target = ops.target_of({'kind': 'literal', 'file': 'about.html', 'line': 5, 'col': 2, 'confidence': 0.97}, confidence=0.97)
        change = ops.make_change(ctx, 't', 'head.title', 'a', 'b', target=target, op='set_text')
        gated, prerender, per_route = ops.gate([change], ctx)
        self.assertEqual(gated[0]['status'], 'proposed')
        self.assertFalse(prerender or per_route)
        shared = ops.target_of({'kind': 'literal', 'file': 'app/layout.tsx', 'line': 9, 'shared_by': ['/', '/a'], 'verify': 'grounded'})
        change = ops.make_change({**ctx, 'head_scope': 'layout'}, 't', 'head.title', 'a', 'b', target=shared, op='set_text')
        gated, _, per_route = ops.gate([change], {**ctx, 'head_scope': 'layout'})
        self.assertEqual(gated[0]['status'], 'blocked')
        self.assertEqual(gated[0]['depends_on'], ['chg_per_route_head'])
        self.assertTrue(per_route)

    def test_reference_validator_rejects_per_instance_template_edit_and_head_inserts(self):
        ctx = {'page_id': 'p1', 'url': '/', 'head_scope': 'per-route', 'recipe': {}}
        template = ops.target_of({'kind': 'literal', 'file': 'src/App.jsx', 'line': 190, 'scope': 'template', 'verify': 'grounded'})
        change = ops.make_change(ctx, 't', 'links.anchor', 'Live', 'Unlovable live demo', target=template, op='set_text')
        body = ops.make_change(ctx, 't', 'content.faq', None, [], op='insert_node',
                               target=ops.target_of({'kind': 'literal', 'file': 'index.html', 'line': 15, 'verify': 'grounded'},
                                                    placement='before </head>'))
        main, more = ops.consolidate([change, body], ctx)
        self.assertTrue(all(c['status'] == 'needs_input' for c in main + more))

    def test_placeholder_targets_are_not_concrete(self):
        self.assertFalse(ops.target_of({'kind': 'add', 'file': 'index.html', 'line': 1})['verified'])
        self.assertFalse(ops.target_of({'kind': 'computed', 'file': 'App.jsx', 'line': 12, 'confidence': 0.9})['verified'])
        self.assertTrue(ops.target_of({'kind': 'literal', 'file': 'x.html', 'line': 3, 'verify': 'grounded'})['verified'])
        self.assertFalse(ops.target_of({'kind': 'literal', 'file': 'x.html', 'line': 3, 'verify': 'heuristic'})['verified'])


class OnpageGoldenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = run_fixture()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.res['tmp'], ignore_errors=True)

    def test_model_calls_tiers_and_repairs(self):
        calls = self.res['calls']
        per_skill = {}
        for (skill, _), tier in calls:
            per_skill.setdefault(skill, []).append(tier)
        self.assertEqual(len(per_skill['search-intent']), 2)
        self.assertEqual(per_skill['search-intent'], ['small', 'small'])
        home_plan = [t for (k, t) in calls if k[0] == 'page-plan' and k[1] == '"/"']
        self.assertEqual(home_plan, ['mid', 'mid', 'mid'])  # data-heading rewrite + FAQ=H2 rejected, repaired
        self.assertLessEqual(len(calls), 2 * 4 + 2 + 1)
        statuses = {k: c['status'] for k, c in self.res['pages'][HOME]['checks'].items()}
        self.assertTrue(all(s in ('done', 'skipped') for s in statuses.values()), statuses)

    def test_no_duplicate_ids_or_dedup_keys(self):
        for page in self.res['pages'].values():
            changes = all_changes(page)
            ids = [c['id'] for c in changes]
            keys = [c['dedup_key'] for c in changes]
            self.assertEqual(len(ids), len(set(ids)), ids)
            self.assertEqual(len(keys), len(set(keys)), keys)
            self.assertLessEqual(len(page['changes']), 15)

    def test_head_changes_on_shared_shell_are_blocked_behind_prerender(self):
        for page in self.res['pages'].values():
            self.assertEqual(page['head_scope'], 'shared-shell')
            for c in all_changes(page):
                if c['field'].startswith(('head.', 'technical.canonical')):
                    self.assertEqual(c['status'], 'blocked', c['field'])
                    self.assertIn('chg_prerender', c['depends_on'])
                    self.assertNotEqual((c['target'] or {}).get('file'), 'index.html')
                    self.assertEqual(c['shell_location']['file'], 'index.html')
                self.assertNotEqual((c.get('target') or {}).get('line'), 1)
        canon = {c['value'] for p in self.res['pages'].values() for c in all_changes(p) if c['field'] == 'technical.canonical'}
        self.assertEqual(canon, {'https://www.lakshaygoyal.in/', 'https://www.lakshaygoyal.in/about'})  # self-referencing per route

    def test_priorities_use_real_field_names(self):
        by_field = {c['field']: c for c in all_changes(self.res['pages'][HOME])}
        self.assertEqual(by_field['head.title']['impact'], 'high')
        self.assertEqual(by_field['head.description']['impact'], 'high')
        self.assertEqual(by_field['headings.h1']['impact'], 'high')

    def test_links_accessibility_and_anchors(self):
        changes = all_changes(self.res['pages'][HOME])
        self.assertFalse([c for c in changes if c['field'] == 'links.accessible_name'])  # aria-labels exist
        anchors = [c for c in changes if c['field'] == 'links.anchor']
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0]['link_id'], link_id('/blog', 'Read more'))
        self.assertEqual(anchors[0]['target']['file'], 'src/App.jsx')
        self.assertFalse([c for c in changes if 'Exness' in json.dumps(c.get('current'))])  # invisible link: no rewrite
        self.assertFalse([c for c in changes if c['field'].startswith('links') and c.get('link_id') and 'PageRail' in json.dumps(c['target'])])

    def test_data_driven_images_one_change_per_pointer_and_template(self):
        changes = all_changes(self.res['pages'][HOME])
        alts = [c for c in changes if c['field'] == 'images.alt']
        self.assertEqual(sorted(c['target']['pointer'] for c in alts), ['/0/alt', '/1/alt'])
        self.assertTrue(all(c['op'] == 'set_json' for c in alts))
        self.assertEqual(len([c for c in changes if c['field'] == 'images.alt_template']), 1)
        dims = [c for c in changes if c['field'] == 'images.dimensions']
        self.assertEqual(len(dims), 1)
        self.assertEqual(dims[0]['target']['scope'], 'template')
        self.assertTrue(any(a['topic'] == 'images.weight' for a in self.res['pages'][HOME]['advice']))

    def test_faq_entity_cap_and_json_ld(self):
        page = self.res['pages'][HOME]
        changes = all_changes(page)
        faq = next(c for c in changes if c['field'] == 'content.faq')['value']
        headings = [h['text'] for h in json.loads((FIXTURE / 'site_model' / 'pages' / (HOME + '.json')).read_text())['headings']]
        for q in faq:
            self.assertFalse(any(v.similar(q['question'], h) for h in headings + ['Work experience at Testsigma']))
        plan = page['checks']['page_plan']['data']
        units = [plan['direct_answer']] + [q['answer'] for q in plan['faq']]
        self.assertTrue(all(v.phrase_count(u, BRAND) <= 1 for u in units))
        ld = next(c for c in changes if c['field'] == 'head.json_ld')['value']
        nodes = {n['@type']: n for n in ld['@graph']}
        self.assertIn('ProfilePage', nodes)
        self.assertEqual(nodes['Person']['name'], BRAND)
        self.assertNotIn('image', nodes['Person'])  # logo is never Person.image
        self.assertTrue(all('intent' not in u for u in nodes['Person']['sameAs']))
        self.assertEqual(nodes['FAQPage']['mainEntity'][0]['acceptedAnswer']['text'], faq[0]['answer'])
        self.assertFalse([c for c in changes if c['field'].startswith('freshness.')])  # personal home: no "Last updated"
        self.assertFalse([a for a in page['advice'] if 'author box' in a['advice'].lower()])

    def test_site_level_platform_changes(self):
        site = self.res['site']
        by_id = {c['id']: c for c in site['changes']}
        self.assertIn('chg_prerender', by_id)
        self.assertEqual(by_id['chg_prerender']['op'], 'platform_config')
        self.assertIn('Vercel', by_id['chg_redirect_permanent']['target']['where'])
        self.assertIn('chg_soft_404', by_id)
        self.assertIn('SPA catch-all', by_id['chg_robots_txt']['rationale'])
        self.assertIn('chg_sitemap_xml', by_id)
        self.assertEqual(sorted(by_id['chg_prerender']['pages']), ['/', '/about'])

    def test_client_inputs_checklist_and_review_html(self):
        needed = [i['needed'] for i in self.res['inputs']['inputs']]
        self.assertTrue(any('1200x630' in n for n in needed))
        self.assertTrue(any('years of experience' in n for n in needed))
        checklist = self.res['pages'][HOME]['checklist']
        self.assertEqual(checklist['title_tag']['after'], True)  # current title already within limits
        self.assertEqual(checklist['canonical']['after'], 'blocked')
        self.assertEqual(checklist['social_meta']['after'], 'blocked')  # never hard-coded True
        self.assertEqual(checklist['served_html']['after'], 'needs_platform_change')
        self.assertIn('REVIEW AID ONLY', self.res['html'])
        self.assertIn('Lakshay Goyal, Software Engineer', self.res['html'])
        row = self.res['index']['pages'][0]
        self.assertGreaterEqual(row['checklist_after_pass'], row['checklist_before_pass'])


if __name__ == '__main__':
    unittest.main()
