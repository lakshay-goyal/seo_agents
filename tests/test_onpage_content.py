"""On-page content studio: competitor research (fetch, extract, patterns), article rules (URL, title CTR, alt, lists,
tables, diagrams, cited fresh facts, no copying) and the end-to-end article run on the vite-spa fixture.
The web (robots.txt, competitor pages, SERP) and the model are fakes; no network."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import random
import shutil
import tempfile
import unittest

from seo_agents.onpage.content import article_rules as rules
from seo_agents.onpage.content.competitor_analyzer import analyze
from seo_agents.onpage.content.competitor_fetcher import CompetitorFetcher
from seo_agents.onpage.content.competitor_research import CompetitorResearch
from seo_agents.onpage.content.content_studio import ArticleStudio, url_prefix
from seo_agents.onpage.content.html_extractor import extract
from seo_agents.onpage.onpage_analysis import run_onpage
from seo_agents.onpage.writer.content_writer import ContentWriter, load_skill

from test_onpage import FIXTURE, HOME, answer_for, text_of

RUN_DATE = '2026-11-02'
COMP_A = 'https://comp-a.example/blog/deploy-react-vercel'
COMP_B = 'https://comp-b.example/guides/react-deployment'
COMP_PRIVATE = 'https://comp-c.example/private/react-vercel'
STAT = 'Vercel reports that 87% of deployments finish in under 60 seconds for small React projects on the free plan.'


def page_html(title, h2s, *, date, stat, extra=''):
    sections = ''.join(f'<h2>{h}</h2><p>{h} explained with a worked example for teams that ship React apps every week '
                       f'and want a predictable release process.</p>' for h in h2s)
    return f'''<!doctype html><html><head><title>{title}</title>
<meta name="description" content="{title} for React developers">
<meta property="article:modified_time" content="{date}T10:00:00Z">
<script type="application/ld+json">{{"@context":"https://schema.org","@type":"FAQPage","mainEntity":[]}}</script>
</head><body><nav><a href="/">Home</a><p>Navigation text that should not count as main content at all.</p></nav>
<main><h1>{title}</h1><p>{stat}</p>{sections}
<ul><li>Install the CLI</li><li>Link the project</li></ul>
<table><tr><th>Host</th><th>Build</th></tr><tr><td>Vercel</td><td>Fast</td></tr></table>
<img src="/img/dashboard.png" alt="Vercel dashboard with a finished deployment"><img src="/img/x.png">
{extra}</main><footer><p>Footer links and copyright notice for the competitor site.</p></footer></body></html>'''


PAGES = {
    COMP_A: page_html('Deploy React to Vercel: Step-by-Step', ['Build the React app', 'Configure environment variables',
                                                               'Deploy with the Vercel CLI'], date='2026-09-01', stat=STAT),
    COMP_B: page_html('React Deployment Guide (Updated)', ['Build your React app', 'Environment variables configuration',
                                                           'Custom domains'], date='2019-01-01',
                      stat='A survey found that 45% of teams deploy React apps manually without any pipeline in place.'),
    COMP_PRIVATE: page_html('Private', ['Hidden'], date='2026-01-01', stat='Nothing to see here at all for this test case.'),
}


def fake_get(url):
    if url.endswith('/robots.txt'):
        return 200, url, {'content-type': 'text/plain'}, 'User-agent: *\nDisallow: /private\n'
    if url in PAGES:
        return 200, url, {'content-type': 'text/html; charset=utf-8'}, PAGES[url]
    return 404, url, {'content-type': 'text/html'}, ''


class FakeSerp:
    def __init__(self):
        self.calls = []

    def top(self, keyword, market=None, *, own_domain=None, limit=8):
        self.calls.append(keyword)
        return {'results': [{'position': 1, 'url': COMP_A, 'domain': 'comp-a.example', 'title': 'Deploy React to Vercel: Step-by-Step',
                             'aggregator': False},
                            {'position': 2, 'url': COMP_B, 'domain': 'comp-b.example', 'title': 'React Deployment Guide (Updated)',
                             'aggregator': False},
                            {'position': 3, 'url': COMP_PRIVATE, 'domain': 'comp-c.example', 'title': 'Private', 'aggregator': False}],
                'people_also_ask': ['Is Vercel free for React apps?'], 'features': ['people_also_ask'], 'error': None}


def research():
    return CompetitorResearch(fetcher=CompetitorFetcher(get=fake_get, cache=False, enabled=True), serp=FakeSerp())


# ---------------------------------------------------------------- fake article answers
VOCAB = ('build pipeline preview branch commit review rollback cache bundle asset route domain certificate header '
         'redirect function edge region latency monitor alert budget team workflow release checklist script variable '
         'secret token project framework config output folder static server client render hydrate prefetch image font '
         'compress minify split chunk lazy module package lockfile install command terminal dashboard setting option '
         'feature limit plan usage quota log trace metric error warning status health check uptime incident owner '
         'reviewer approval merge main staging production environment promote alias history snapshot artifact upload '
         'download mirror origin proxy rewrite fallback page layout component state store fetch request response json '
         'schema test unit integration smoke lint format type compile transpile target browser device mobile desktop '
         'audit score vitals paint shift input delay speed index weight request count third party script tag consent '
         'privacy policy analytics event funnel goal conversion signup trial invoice seat member role access guard').split()


def filler(rng, words):
    picked = [rng.choice(VOCAB) for _ in range(words)]
    out, sentence = [], []
    for i, w in enumerate(picked):
        sentence.append(w)
        if len(sentence) >= 10 or i == len(picked) - 1:
            out.append(' '.join(sentence).capitalize() + '.')
            sentence = []
    return ' '.join(out)


def article_answer(packet, attempt):
    brief = packet['brief']
    if brief['slug'] != 'deploy-react-app-vercel':
        return {'title': 'too short'}  # the second article never validates -> status failed
    rng = random.Random(7)
    stat_id = next(s['id'] for s in packet['sources'] if '87%' in s['passage'])
    sections = [{'heading': 'How do you deploy a React app to Vercel?', 'blocks': [
        {'tag': 'p', 'text': 'You deploy a React app to Vercel by connecting the repository, letting Vercel detect the framework, '
                             'setting environment variables and pushing to the main branch. Every push builds a preview, and '
                             'merging promotes it to production with a stable domain and automatic certificates for the site.'},
        {'tag': 'ol', 'items': ['Connect the repository in the dashboard.', 'Confirm the detected build command.',
                                'Add environment variables for each stage.', 'Push to main to promote the build.']}]}]
    for i, heading in enumerate(['Prepare the build before the first deploy', 'Configure environment variables safely',
                                 'Compare Vercel deployment options', 'Monitor and roll back releases',
                                 'Speed up builds with caching']):
        blocks = [{'tag': 'p', 'text': filler(rng, 55)}, {'tag': 'p', 'text': filler(rng, 55)},
                  {'tag': 'p', 'text': filler(rng, 55)}]
        if i == 0:
            blocks.append({'tag': 'p', 'text': 'Start from the <a href="/blog">blog guides on shipping sites</a> and read '
                                               '<a href="/blog/deploying-your-site-with-vercel">deploying your site with Vercel</a> '
                                               'for the basics, then come back here.'})
        if i == 1:
            blocks.append({'tag': 'ul', 'items': ['Keep secrets out of the repository', 'Scope variables per environment',
                                                  'Rotate tokens after team changes']})
        if i == 2:
            blocks.append({'tag': 'table', 'caption': 'Deployment options for a React app on Vercel',
                           'header': ['Option', 'Best for', 'Speed'],
                           'rows': [['Git integration', 'Teams with pull requests', f'87% of deployments under 60 seconds [{stat_id}]'],
                                    ['Vercel CLI', 'Local or scripted releases', 'Depends on the machine']]})
            blocks.append({'tag': 'diagram', 'code': 'flowchart LR\n  A[Push commit] --> B[Preview build]\n  B --> C[Production]',
                           'alt': 'Flow from a git push to a preview build and then production on Vercel',
                           'caption': 'How a commit moves from preview to production'})
        if i == 3:
            blocks.append({'tag': 'image_brief', 'filename': 'vercel-deployment-history-rollback.webp',
                           'alt': 'Vercel deployment history list with the rollback button highlighted',
                           'caption': 'Rolling back to an earlier deployment',
                           'prompt': 'Annotated screenshot of the Vercel deployments list with the instant rollback action circled.'})
            blocks.append({'tag': 'p', 'text': 'Small React projects on the free plan usually finish deploying in under '
                                               f'60 seconds, with 87% doing so [{stat_id}]. See the '
                                               '<a href="/about">team behind this site</a> for context.'})
        sections.append({'heading': heading, 'blocks': blocks})
    good = {
        'title': 'How to Deploy a React App to Vercel (2026 Guide)',
        'h1': 'Deploy a React App to Vercel: A Practical Walkthrough',
        'meta_description': 'Learn how to deploy a React app to Vercel step by step: connect the repo, set environment '
                            'variables, preview every push and roll back safely.',
        'intro': 'To deploy a React app to Vercel, connect your Git repository, keep the detected build settings, add your '
                 'environment variables and push to the main branch. Vercel builds a preview for every push and promotes '
                 'the main branch to production, so releases stay fast and reversible.',
        'key_takeaways': ['Connect the repository once and every push gets a preview.', 'Keep secrets in scoped environment variables.',
                          'Roll back instantly from the deployment history.'],
        'sections': sections,
        'faq': [{'question': 'Is Vercel free for React apps?',
                 'answer': 'Vercel has a free hobby plan that covers personal React projects with previews and production '
                           'deployments; commercial teams move to a paid plan for collaboration and higher limits.'}],
        'tags': ['react', 'vercel', 'deployment', 'web hosting'],
        'category': 'Guides',
        'link_from': [{'url': '/blog/deploying-your-site-with-vercel', 'anchor': 'deploy a React app to Vercel'}],
    }
    if attempt == 0:
        return {**good, 'title': 'Deploying a React App on Vercel Platform'}  # no click-through hook -> repaired
    return good


def planner_answer(packet):
    pool = {t['text']: t['id'] for t in packet['pool']}
    return {'articles': [
        {'topic_ids': [pool['how to deploy a react app to vercel']], 'primary_keyword': 'deploy react app vercel',
         'secondary_keywords': ['vercel react deployment'], 'questions': ['how to deploy a react app to vercel'],
         'search_intent': 'informational', 'format': 'how-to', 'angle': 'A start-to-finish walkthrough with rollback and caching.',
         'audience': 'React developers', 'working_title': 'How to deploy a React app to Vercel', 'slug': 'deploy-react-app-vercel',
         'pillar_url': '/blog'},
        {'topic_ids': [pool['react app deployment checklist']], 'primary_keyword': 'react deployment checklist',
         'secondary_keywords': [], 'questions': [], 'search_intent': 'informational', 'format': 'checklist',
         'angle': 'A pre-launch checklist for React releases.', 'audience': 'React developers',
         'working_title': 'React deployment checklist for launches', 'slug': 'react-deployment-checklist', 'pillar_url': None}]}


class FakeModel:
    def __init__(self, calls):
        self.calls = calls

    def invoke(self, messages):
        skill = text_of(messages[0].content).split('# Skill: ', 1)[1].split('\n', 1)[0].strip()
        packet = json.loads(text_of(messages[1].content))
        key = skill + '|' + json.dumps((packet.get('brief') or {}).get('slug') or (packet.get('page') or {}).get('url'))
        attempt = sum(1 for c in self.calls if c[0] == key)
        self.calls.append((key, packet))
        if skill == 'article-planner':
            answer = planner_answer(packet)
        elif skill == 'article-writer':
            answer = article_answer(packet, attempt)
        else:
            answer = answer_for(skill, packet, attempt)
        return type('R', (), {'content': json.dumps(answer)})()


KEYWORD_MAP = {'schema': 'seo-keywords/1.0', 'market': {'country': 'US', 'location_code': 2840, 'language_code': 'en'},
               'business': {'brand': 'Lakshay Goyal', 'site_type': 'personal', 'offering': 'web and mobile apps',
                            'seed_topics': ['react deployment']},
               'pages': {HOME: {'page_id': HOME, 'url': '/', 'intent': 'navigational', 'journey_stage': 'consideration',
                                'primary': 'lakshay goyal software engineer', 'secondary': [],
                                'questions': ['how to deploy a react app to vercel'], 'conversational': [], 'short_tail': [],
                                'cluster': {}, 'competitor_urls': [COMP_A, COMP_B],
                                'content_gaps': ['react app deployment checklist'], 'status': 'complete'}}}


def run_studio():
    calls = []
    writer = ContentWriter(model_factory=lambda tier, label=None: FakeModel(calls), attempts=3)
    tmp = tempfile.mkdtemp()
    root = Path(tmp) / 'seo_reports'
    shutil.copytree(FIXTURE, root)
    (root / 'keywords').mkdir()
    (root / 'keywords' / 'keyword_map.json').write_text(json.dumps(KEYWORD_MAP))
    lab = research()
    with redirect_stdout(io.StringIO()):
        state = run_onpage(root, run_date=RUN_DATE, writer=writer, only_pages=[HOME], research=lab,
                           studio=ArticleStudio(lab, count=2))
    return {'calls': calls, 'state': state, 'out': root / 'onpage', 'tmp': tmp, 'serp': lab.serp}


class ExtractorAndPatternTests(unittest.TestCase):
    def test_extract_keeps_structure_and_drops_chrome(self):
        page = extract(PAGES[COMP_A], COMP_A)
        self.assertEqual(page['title'], 'Deploy React to Vercel: Step-by-Step')
        self.assertEqual(page['h1'], 'Deploy React to Vercel: Step-by-Step')
        self.assertEqual([h['text'] for h in page['outline']][:2], ['Build the React app', 'Configure environment variables'])
        self.assertEqual(page['modified'], '2026-09-01')
        self.assertTrue(page['faq'])
        self.assertEqual(page['counts']['table'], 1)
        self.assertEqual(page['image_alt_coverage'], 0.5)
        self.assertEqual(page['slug_words'], ['deploy', 'react', 'vercel'])
        self.assertFalse(any('Navigation text' in b['text'] or 'Footer' in b['text'] for b in page['blocks']))

    def test_patterns_find_shared_subtopics_formats_and_titles(self):
        pages = [extract(PAGES[COMP_A], COMP_A), extract(PAGES[COMP_B], COMP_B)]
        p = analyze(pages, RUN_DATE)
        self.assertEqual(p['pages'], 2)
        headings = [t['heading'] for t in p['common_subtopics']]
        self.assertIn('Build the React app', headings)   # "Build your React app" on the other page
        self.assertEqual(p['formats']['table'], 1.0)
        self.assertEqual(p['freshness']['newest'], '2026-09-01')
        self.assertEqual(p['freshness']['stale_share'], 0.5)
        self.assertEqual(p['title_patterns']['colon'], 0.5)
        self.assertEqual(p['title_patterns']['brackets'], 0.5)

    def test_url_prefix_reuses_the_blog_folder(self):
        self.assertEqual(url_prefix(['/', '/blog', '/blog/a-post', '/about']), '/blog')
        self.assertEqual(url_prefix(['/', '/about']), '/blog')
        self.assertEqual(url_prefix(['/guides/a', '/guides/b']), '/guides')


class ArticleRuleTests(unittest.TestCase):
    def test_slug_rules(self):
        ok = rules.check_slug('deploy-react-app-vercel', primary='deploy react app vercel', prefix='/blog', existing_paths=['/blog'])
        self.assertEqual(ok, '/blog/deploy-react-app-vercel')
        bad = {'Deploy_React': 'lowercase', 'blog-react-deploy': 'folder word', 'react-react-deploy': 'repeats a word',
               'react-deploy-2026': 'year', 'react-12345-deploy': 'id-like', 'the-react-deploy': 'filler',
               'cooking-recipes': 'does not name the topic', 'react': 'words'}
        for slug, why in bad.items():
            with self.assertRaises(ValueError, msg=slug) as caught:
                rules.check_slug(slug, primary='react deploy', prefix='/blog')
            self.assertIn(why, str(caught.exception), slug)
        with self.assertRaises(ValueError):
            rules.check_slug('deploying-your-site-vercel', primary='deploy site vercel', prefix='/blog',
                             existing_paths=['/blog/deploying-your-site-vercel'])

    def test_title_needs_keyword_hook_and_current_year(self):
        self.assertIn('year', rules.ctr_hooks('How to Deploy a React App to Vercel (2026 Guide)', 2026))
        with self.assertRaises(ValueError):
            rules.check_ctr_title('Deploying React Apps On Vercel Platform', 2026)
        with self.assertRaises(ValueError):
            rules.check_title('Deploy a React App to Vercel in 2024 (Guide)', primary='deploy react app vercel', run_year=2026)
        with self.assertRaises(ValueError):
            rules.check_title('Ten Ways to Bake Sourdough Bread at Home', primary='deploy react app vercel', run_year=2026)

    def test_alt_text_rules(self):
        seen = set()
        self.assertTrue(rules.check_alt('Vercel dashboard showing a finished React build', 'alt', seen=seen))
        for alt in ('image of a dashboard', 'screenshot.png', 'Dashboard', 'Vercel dashboard showing a finished React build'):
            with self.assertRaises(ValueError, msg=alt):
                rules.check_alt(alt, 'alt', seen=seen)

    def test_list_table_and_diagram_formatting(self):
        self.assertEqual(rules.check_list(['Install the CLI.', 'Link the project.'], 'l'), ['Install the CLI.', 'Link the project.'])
        for items in (['- Install the CLI', 'Link it'], ['1. Install', 'Link'], ['Install the CLI.', 'Link the project'],
                      ['Only one'], ['Same item', 'same item']):
            with self.assertRaises(ValueError, msg=items):
                rules.check_list(items, 'l')
        with self.assertRaises(ValueError):
            rules.check_table({'header': ['A', 'B'], 'rows': [['1', '2'], ['3']]}, 't')
        self.assertTrue(rules.check_mermaid('flowchart LR\n  A --> B', 'd'))
        with self.assertRaises(ValueError):
            rules.check_mermaid('```mermaid\nflowchart LR\n A-->B\n```', 'd')
        with self.assertRaises(ValueError):
            rules.check_mermaid('hello world', 'd')

    def test_numbers_need_a_fresh_cited_source(self):
        sources = [{'id': 'S1', 'url': COMP_A, 'date': '2026-09-01', 'passage': STAT},
                   {'id': 'S2', 'url': COMP_B, 'date': '2019-01-01', 'passage': '45% of teams deploy manually.'},
                   {'id': 'S3', 'url': COMP_B, 'date': None, 'passage': '30% of teams use previews.'}]
        kw = {'corpus': 'Ada builds apps', 'sources': sources, 'run_date': RUN_DATE}
        claims = rules.check_facts('87% of deployments finish fast [S1].', 'p', **kw)
        self.assertEqual(claims, [{'number': '87%', 'source': 'S1'}])
        self.assertEqual(rules.check_facts('Follow these 5 steps in 2026.', 'p', **kw), [])   # small count + run year
        for text, why in (('87% of deployments finish fast.', 'not backed'), ('45% of teams deploy manually [S2].', 'fresher'),
                          ('30% of teams use previews [S3].', 'unknown'), ('Cited [S9].', 'not in sources'),
                          ('By 2030 everyone deploys.', 'future year'), ('Over 300 teams agree [S1].', 'not backed')):
            with self.assertRaises(ValueError, msg=text) as caught:
                rules.check_facts(text, 'p', **kw)
            self.assertIn(why, str(caught.exception), text)

    def test_skills_render_article_limits(self):
        system, meta = load_skill('article-writer')
        self.assertNotIn('{{', system)
        self.assertIn('30-60 chars', system)
        self.assertIn('P11 Learn from competitors', system)
        self.assertEqual(meta['tier'], 'mid')
        self.assertNotIn('{{', load_skill('article-planner')[0])
        self.assertIn('P11', load_skill('page-plan')[0])


class ContentStudioRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = run_studio()
        cls.out = cls.res['out']

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.res['tmp'], ignore_errors=True)

    def test_competitors_are_fetched_respecting_robots(self):
        data = json.loads((self.out / 'competitors' / 'index.json').read_text())
        fetched = {p['url']: p for p in data['fetched']}
        self.assertEqual(fetched[COMP_A]['title'], 'Deploy React to Vercel: Step-by-Step')
        self.assertEqual(fetched[COMP_PRIVATE]['error'], 'disallowed by robots.txt')
        page = data['pages'][HOME]
        self.assertGreaterEqual(page['patterns']['pages'], 2)
        self.assertNotIn('texts', page)
        self.assertFalse(any('blocks' in p for p in data['fetched']))  # no competitor body text is written

    def test_page_plan_and_content_quality_see_competitor_patterns(self):
        plans = [p for key, p in self.res['calls'] if key.startswith('page-plan|')]
        self.assertTrue(plans)
        comp = plans[0]['competitors']
        self.assertIn('common_subtopics', comp)
        self.assertIn('Deploy React to Vercel: Step-by-Step', comp['titles'])
        quality = self.res['state']['results'][HOME]['content_quality']['data']
        self.assertIsNotNone(quality['competitor_median_words'])

    def test_article_is_written_validated_and_rendered(self):
        index = json.loads((self.out / 'articles' / 'index.json').read_text())
        rows = {r['slug']: r for r in index['articles']}
        self.assertEqual(rows['deploy-react-app-vercel']['status'], 'done')
        self.assertEqual(rows['react-deployment-checklist']['status'], 'failed')
        self.assertEqual(index['plan']['url_prefix'], '/blog')
        record = json.loads((self.out / 'articles' / 'deploy-react-app-vercel.json').read_text())
        article = record['article']
        self.assertEqual(article['url'], '/blog/deploy-react-app-vercel')
        self.assertIn('year', article['title_hooks'])
        self.assertGreaterEqual(article['word_count'], 900)
        self.assertIn('/blog', article['internal_links'])
        self.assertGreaterEqual(len(article['internal_links']), 3)
        self.assertEqual(record['fact_check']['freshness']['last_updated'], RUN_DATE)
        self.assertTrue(any(c['number'] == '87%' for c in record['fact_check']['numeric_claims']))
        self.assertEqual([g['@type'] for g in record['json_ld']['@graph']], ['BlogPosting', 'FAQPage', 'BreadcrumbList'])
        writer_calls = [k for k, _ in self.res['calls'] if k == 'article-writer|"deploy-react-app-vercel"']
        self.assertEqual(len(writer_calls), 2)  # the hook-less title was rejected and repaired

    def test_markdown_formatting(self):
        md = (self.out / 'articles' / 'deploy-react-app-vercel.md').read_text()
        self.assertTrue(md.startswith('---\ntitle: "How to Deploy a React App to Vercel (2026 Guide)"'))
        self.assertIn('# Deploy a React App to Vercel: A Practical Walkthrough', md)
        self.assertIn('*Last updated: November 2, 2026*', md)
        self.assertIn('1. Connect the repository in the dashboard.', md)
        self.assertIn('- Keep secrets out of the repository', md)
        self.assertIn('| Option | Best for | Speed |\n| --- | --- | --- |', md)
        self.assertIn('```mermaid\nflowchart LR', md)
        self.assertIn('![Vercel deployment history list with the rollback button highlighted]'
                      '(/images/vercel-deployment-history-rollback.webp', md)
        self.assertIn('[blog guides on shipping sites](/blog)', md)
        self.assertIn(f'[[1]]({COMP_A})', md)
        self.assertIn('## Sources', md)
        self.assertNotIn('[S', md)


if __name__ == '__main__':
    unittest.main()
