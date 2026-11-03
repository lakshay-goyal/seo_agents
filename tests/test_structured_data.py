"""Strict JSON-LD generation: product/SaaS, docs how-to, blog, collection pages and the schema.org/Google validator.
Synthetic page contexts; no network, no model."""

import json
import unittest

from seo_agents.onpage.changes import page_preview_renderer as render
from seo_agents.onpage.checks.structured_data_check import check_structured_data
from seo_agents.onpage.structured_data import schema_facts as facts
from seo_agents.onpage.structured_data import schema_rules as rules

ORIGIN = 'https://acme.dev'


def block(tag, text):
    return {'tag': tag, 'text': text, 'edit': None}


def section(heading, *blocks, level=2):
    return {'heading': heading, 'level': level, 'edit': None, 'blocks': list(blocks), 'data_driven': False}


def make_ctx(url, *, title, description=None, sections=(), images=(), assets=(), links=(), meta_tags=(), eeat=(),
             json_ld=(), site_urls=(), profiles=(), site_type='business', h1=None, main_words=600):
    sections = list(sections)
    copy = [title] + [s['heading'] for s in sections if s['heading']] + [b['text'] for s in sections for b in s['blocks']]
    return {
        'page_id': 'p_' + (url.strip('/').replace('/', '_') or 'home'), 'url': url, 'origin': ORIGIN,
        'absolute_url': ORIGIN + url, 'is_home': url == '/', 'lang': 'en', 'run_date': '2026-11-03',
        'title': title, 'description': description, 'brand_candidates': ['Acme'], 'site_type': site_type,
        'headings': ([{'level': 1, 'text': h1}] if h1 else []) + [{'level': s['level'], 'text': s['heading']} for s in sections],
        'sections': sections, 'copy': copy, 'corpus': ' \n '.join(copy), 'main_words': main_words,
        'images': list(images), 'assets': list(assets), 'links': list(links), 'profiles': list(profiles),
        'meta_tags': list(meta_tags), 'eeat_observed': list(eeat), 'json_ld': list(json_ld),
        'site_urls': [{'url': u, 'title': t, 'in_model': True} for u, t in site_urls], 'site_pages': {},
        'add_target': None, 'keyword_plan': None, 'keyword_research': None,
    }


def prior_for(page_type='other', site_type='business', plan=None, entities=()):
    return {'intent': {'data': {'brand_name': 'Acme', 'site_type': site_type, 'page_type': page_type,
                                'entities': list(entities)}},
            'page_plan': {'data': plan or {}}}


def run(ctx, prior):
    res = check_structured_data(ctx, prior)
    data = res['data']
    return res, data['json_ld'], {n['@type']: n for n in data['json_ld']['@graph']}


PRICING = [
    section('Pricing plans', block('p', 'Pick the plan that fits your team.')),
    section('Starter', block('p', 'Free forever for individuals.'), level=3),
    section('Team', block('p', '$49 per month for up to 10 users.'), level=3),
    section('Enterprise', block('p', '$199/month with SSO and audit logs.'), level=3),
    section('Features', block('li', 'No-code test automation for web and mobile apps'),
            block('li', 'Parallel runs in the cloud'), block('li', 'Self-healing locators')),
    section('Loved by teams', block('p', 'Rated 4.7 out of 5 by 1,250 reviews from our users.')),
    section('How do I get started with Acme?', block('p', 'Sign up with your work email, connect your repository and run '
                                                          'your first test in the cloud within minutes.')),
]


class ProductPageTests(unittest.TestCase):
    def setUp(self):
        self.ctx = make_ctx('/pricing', title='Pricing — Acme', description='Acme pricing: free, team and enterprise plans '
                            'for cloud test automation.', h1='Simple pricing for every team', sections=PRICING,
                            images=[{'src': '/img/dashboard.png', 'alt': 'Acme dashboard', 'natural': [1600, 900], 'above_fold': True},
                                    {'src': '/img/logo.png', 'alt': 'Acme logo', 'natural': [600, 600]}],
                            assets=[{'src': '/img/logo.png', 'type': 'image', 'natural': [512, 512]}],
                            links=[{'href': 'mailto:sales@acme.dev'}, {'href': 'tel:+1 (415) 555-0100'}],
                            profiles=['https://www.linkedin.com/company/acme', 'https://github.com/acme/acme-cli',
                                      'https://twitter.com/intent/tweet?text=x', 'https://x.com/acme'],
                            site_urls=[('/', 'Acme'), ('/pricing', 'Pricing — Acme')])
        self.ctx['keyword_research'] = {'business': {'offering': 'cloud test automation platform'}}
        self.res, self.ld, self.nodes = run(self.ctx, prior_for())

    def test_software_application_with_offers_rating_features(self):
        app = self.nodes['WebApplication']
        self.assertEqual(app['name'], 'Acme')
        self.assertEqual(app['applicationCategory'], 'DeveloperApplication')
        prices = {o['name']: (o['price'], o.get('priceCurrency')) for o in app['offers']}
        self.assertEqual(prices, {'Starter': ('0', 'USD'), 'Team': ('49', 'USD'), 'Enterprise': ('199', 'USD')})
        team = next(o for o in app['offers'] if o['name'] == 'Team')
        self.assertEqual(team['priceSpecification']['unitCode'], 'MON')
        self.assertEqual(app['aggregateRating'], {'@type': 'AggregateRating', 'ratingValue': '4.7', 'bestRating': 5,
                                                  'worstRating': 1, 'reviewCount': 1250})
        self.assertIn('Parallel runs in the cloud', app['featureList'])
        self.assertEqual(app['publisher'], {'@id': ORIGIN + '/#organization'})
        self.assertEqual(self.nodes['ItemPage']['mainEntity'], {'@id': app['@id']})

    def test_graph_is_valid_and_complete(self):
        self.assertEqual(rules.errors_of(rules.validate(self.ld)), [])
        self.assertEqual(self.res['data']['validation']['errors'], [])
        self.assertEqual(self.res['data']['schema_kind'], 'product')
        for kind in ('ItemPage', 'WebSite', 'Organization', 'ImageObject', 'BreadcrumbList', 'FAQPage', 'WebApplication'):
            self.assertIn(kind, self.nodes)
        org = self.nodes['Organization']
        self.assertEqual(org['email'], 'sales@acme.dev')
        self.assertEqual(org['telephone'], '+14155550100')
        self.assertEqual(org['sameAs'], ['https://www.linkedin.com/company/acme', 'https://x.com/acme'])  # no repo/intent
        self.assertEqual(org['logo'], {'@id': ORIGIN + '/#logo'})
        image = next(n for n in self.ld['@graph'] if n['@id'].endswith('#primaryimage'))
        self.assertEqual(image['contentUrl'], ORIGIN + '/img/dashboard.png')  # the logo is never the page image
        crumbs = self.nodes['BreadcrumbList']['itemListElement']
        self.assertEqual([(c['position'], c['name']) for c in crumbs], [(1, 'Home'), (2, 'Simple pricing for every team')])
        faq = self.nodes['FAQPage']['mainEntity']
        self.assertEqual(faq[0]['name'], 'How do I get started with Acme?')

    def test_third_party_rating_is_not_marked_up(self):
        sections = PRICING[:5] + [section('Reviews', block('p', 'Rated 4.8 out of 5 on G2 from 300 reviews.'))]
        ctx = make_ctx('/pricing', title='Pricing — Acme', h1='Pricing', sections=sections)
        res, _ld, nodes = run(ctx, prior_for())
        app = nodes['WebApplication']
        self.assertNotIn('aggregateRating', app)
        self.assertTrue(any('third-party' in w for w in res['data']['validation']['warnings']))
        self.assertTrue(any('aggregateRating' in i['needed'] for i in res['client_inputs']))

    def test_product_without_price_asks_owner(self):
        ctx = make_ctx('/products/widget', title='Widget — Acme', h1='Widget', site_type='ecommerce',
                       sections=[section('About the widget', block('p', 'A sturdy steel widget for workshops.'))])
        res, ld, nodes = run(ctx, prior_for(page_type='product', site_type='ecommerce', entities=['Widget']))
        self.assertEqual(nodes['Product']['name'], 'Widget')
        self.assertEqual(nodes['Product']['brand'], {'@id': ORIGIN + '/#organization'})
        self.assertNotIn('offers', nodes['Product'])
        self.assertTrue(any('price' in i['needed'].lower() for i in res['client_inputs']))
        self.assertTrue(any('Product.offers' in n for n in res['data']['validation']['needs_input']))


class ContentPageTests(unittest.TestCase):
    def test_docs_page_gets_tech_article_and_howto(self):
        ctx = make_ctx('/docs/quickstart', title='Quickstart — Acme Docs', description='Install the Acme CLI and run a test.',
                       h1='Quickstart', sections=[
                           section('Getting started', block('li', 'Install the CLI with npm install -g acme.'),
                                   block('li', 'Log in with acme login.'), block('li', 'Run acme test in your project.')),
                           section('What does the CLI need?', block('p', 'Node.js 20 or newer and a free Acme account with '
                                                                          'an API token from the dashboard settings.'))],
                       site_urls=[('/', 'Acme'), ('/docs', 'Docs — Acme'), ('/docs/quickstart', 'Quickstart')])
        res, ld, nodes = run(ctx, prior_for())
        self.assertEqual(rules.errors_of(rules.validate(ld)), [])
        self.assertEqual(nodes['TechArticle']['headline'], 'Quickstart')
        steps = nodes['HowTo']['step']
        self.assertEqual([s['position'] for s in steps], [1, 2, 3])
        self.assertEqual(steps[1]['text'], 'Log in with acme login.')
        crumbs = [c['name'] for c in nodes['BreadcrumbList']['itemListElement']]
        self.assertEqual(crumbs, ['Home', 'Docs', 'Quickstart'])  # real ancestor page, named by its title
        self.assertIn('FAQPage', nodes)

    def test_blog_post_dates_author_and_keywords(self):
        ctx = make_ctx('/blog/flaky-tests', title='Fix flaky tests — Acme Blog', description='Why tests flake and how to fix them.',
                       h1='How we fixed flaky tests', images=[{'src': '/img/flaky.jpg', 'alt': 'Chart', 'natural': [1200, 675]}],
                       meta_tags=[{'property': 'article:published_time', 'content': '2026-03-04T09:00:00+00:00'}],
                       eeat=[{'signal': 'authorship', 'status': 'observed', 'evidence': [{'text': 'By Priya Sharma'}]},
                             {'signal': 'dates', 'status': 'observed', 'evidence': [{'text': 'Updated on Jun 2, 2026'}]}],
                       sections=[section('Why tests flake', block('p', 'Timing, shared state and network calls.'))])
        ctx['keyword_plan'] = {'primary': 'flaky tests', 'secondary': ['fix flaky tests']}
        res, ld, nodes = run(ctx, prior_for(page_type='article'))
        post = nodes['BlogPosting']
        self.assertEqual(post['datePublished'], '2026-03-04T09:00:00+00:00')
        self.assertEqual(post['dateModified'], '2026-06-02')
        self.assertEqual(post['author'], {'@type': 'Person', 'name': 'Priya Sharma'})
        self.assertEqual(post['publisher'], {'@id': ORIGIN + '/#organization'})
        self.assertEqual(post['keywords'], 'flaky tests, fix flaky tests')
        self.assertEqual(post['wordCount'], 600)
        self.assertFalse(any('datePublished' in i['needed'] for i in res['client_inputs']))
        self.assertEqual(rules.errors_of(rules.validate(ld)), [])

    def test_collection_page_item_list(self):
        ctx = make_ctx('/blog', title='Blog — Acme', h1='Blog', sections=[section('Latest', block('p', 'Notes from the team.'))],
                       site_urls=[('/', 'Acme'), ('/blog', 'Blog'), ('/blog/a', 'Post A — Acme'), ('/blog/b', 'Post B'),
                                  ('/blog/b/comments', 'Comments')])
        _res, ld, nodes = run(ctx, prior_for(page_type='collection'))
        items = nodes['ItemList']['itemListElement']
        self.assertEqual([(i['name'], i['url']) for i in items], [('Post A', ORIGIN + '/blog/a'), ('Post B', ORIGIN + '/blog/b')])
        self.assertEqual(nodes['CollectionPage']['mainEntity'], {'@id': nodes['ItemList']['@id']})

    def test_plan_faq_with_placeholder_is_not_marked_up(self):
        plan = {'title': 'About Acme', 'h1': 'About Acme', 'faq': [
            {'question': 'When was Acme founded?', 'answer': 'Acme was founded in [VERIFY: founding year] by two engineers.'}]}
        ctx = make_ctx('/about', title='About — Acme', sections=[section('Our story', block('p', 'We build test tools.'))])
        res, _ld, nodes = run(ctx, prior_for(page_type='about', plan=plan))
        self.assertNotIn('FAQPage', nodes)
        self.assertIn('AboutPage', nodes)

    def test_existing_markup_types_not_regenerated_are_flagged(self):
        ctx = make_ctx('/about', title='About — Acme', sections=[section('Our story', block('p', 'We build test tools.'))],
                       json_ld=[{'types': ['Event', 'Organization'], 'valid': True}, {'types': [], 'valid': False,
                                                                                    'error': 'Expecting value'}])
        res, _ld, _nodes = run(ctx, prior_for(page_type='about'))
        self.assertEqual(res['data']['validation']['existing_types_not_regenerated'], ['Event'])
        self.assertEqual(res['changes'][0]['op'], 'set_text')
        self.assertIn('invalid JSON-LD', res['changes'][0]['rationale'])


class ValidatorTests(unittest.TestCase):
    def graph(self, *nodes):
        return {'@context': 'https://schema.org', '@graph': list(nodes)}

    def messages(self, data, **kw):
        return [r['message'] for r in rules.errors_of(rules.validate(data, **kw))]

    def test_ids_refs_and_urls(self):
        data = self.graph({'@type': 'WebPage', '@id': ORIGIN + '/x#webpage', 'url': '/x', 'name': 'X',
                           'isPartOf': {'@id': ORIGIN + '/#website'}},
                          {'@type': 'Thing', '@id': ORIGIN + '/x#webpage'})
        found = ' | '.join(self.messages(data))
        self.assertIn('absolute http(s) URL', found)
        self.assertIn('does not resolve', found)
        self.assertIn('duplicate @id', found)

    def test_offer_rating_and_enums(self):
        app = {'@type': 'SoftwareApplication', '@id': ORIGIN + '/#app', 'name': 'A', 'applicationCategory': 'Tools',
               'offers': {'@type': 'Offer', 'price': '$49', 'priceCurrency': 'usd'},
               'aggregateRating': {'@type': 'AggregateRating', 'ratingValue': '6', 'ratingCount': 3}}
        org = {'@type': 'Organization', '@id': ORIGIN + '/#organization', 'name': 'Acme', 'url': ORIGIN + '/',
               'aggregateRating': {'@type': 'AggregateRating', 'ratingValue': '5', 'ratingCount': 9}}
        found = ' | '.join(self.messages(self.graph(app, org)))
        for text in ('plain number', 'ISO 4217', 'outside', 'application category', 'self-serving'):
            self.assertIn(text, found)

    def test_text_dates_headline_and_visibility(self):
        post = {'@type': 'BlogPosting', '@id': ORIGIN + '/p#article', 'headline': 'x' * 111, 'datePublished': '03/04/2026',
                'description': 'See <b>this</b>'}
        faq = {'@type': 'FAQPage', '@id': ORIGIN + '/p#faq', 'mainEntity': [
            {'@type': 'Question', 'name': 'Is it fast?', 'acceptedAnswer': {'@type': 'Answer', 'text': 'Yes [S1].'}}]}
        found = ' | '.join(self.messages(self.graph(post, faq), visible=rules.norm('nothing relevant')))
        for text in ('over 110', 'ISO 8601', 'HTML markup', 'citation marker', 'not visible'):
            self.assertIn(text, found)

    def test_prune_drops_bad_optional_nodes_and_their_refs(self):
        page = {'@type': 'WebPage', '@id': ORIGIN + '/#webpage', 'url': ORIGIN + '/', 'name': 'Home',
                'mainEntity': {'@id': ORIGIN + '/#howto'}}
        howto = {'@type': 'HowTo', '@id': ORIGIN + '/#howto', 'name': 'Do it',
                 'step': [{'@type': 'HowToStep', 'position': 1, 'text': 'Only one'}]}
        data, issues, dropped = rules.prune(self.graph(page, howto), keep={ORIGIN + '/#webpage'})
        self.assertEqual([n['@type'] for n in data['@graph']], ['WebPage'])
        self.assertNotIn('mainEntity', data['@graph'][0])
        self.assertEqual(dropped[0]['@type'], 'HowTo')
        self.assertEqual(rules.errors_of(issues), [])


class HelperTests(unittest.TestCase):
    def test_script_tag_cannot_be_closed_by_text(self):
        html = render.json_ld({'@context': 'https://schema.org', 'text': 'a </script><script>alert(1)</script> <!-- b'})
        self.assertEqual(html.count('</script>'), 1)
        body = html.split('\n', 1)[1].rsplit('\n</script>', 1)[0]
        self.assertEqual(json.loads(body)['text'], 'a </script><script>alert(1)</script> <!-- b')

    def test_money_parsing(self):
        ctx = make_ctx('/pricing', title='Pricing', sections=[section('Pro', block('p', '€29.00 / month per user')),
                                                              section('Max', block('p', 'Billed 1,200 INR a year'))])
        rows = {(r['name'], r['price'], r['currency'], r['unit'], r['per']) for r in facts.offers(ctx)}
        self.assertEqual(rows, {('Pro', '29', 'EUR', 'MON', 'user'), ('Max', '1200', 'INR', 'ANN', None)})

    def test_same_as_keeps_profiles_only(self):
        self.assertEqual(facts.same_as(['https://github.com/me', 'https://github.com/me/repo', 'https://www.linkedin.com/in/me/',
                                        'https://twitter.com/intent/tweet', 'https://example.org/team']),
                         ['https://github.com/me', 'https://www.linkedin.com/in/me', 'https://example.org/team'])


class ReviewRegressionTests(unittest.TestCase):
    def test_nested_error_prunes_owner_node(self):
        sections = [section('Pro', block('p', '$29 per month.')),
                    section('Reviews', block('p', 'Rated 5.9/5 from 120 reviews.'))]
        ctx = make_ctx('/products/pro', title='Pro test automation app — Acme', h1='Pro', sections=sections)
        res, ld, nodes = run(ctx, prior_for(page_type='product'))
        self.assertEqual(res['data']['validation']['errors'], [])
        self.assertTrue(res['data']['validation']['pruned'])  # out-of-range rating -> product node not shipped
        self.assertEqual(rules.errors_of(rules.validate(ld)), [])

    def test_no_origin_means_no_change(self):
        ctx = make_ctx('/x', title='X')
        ctx['origin'] = ctx['absolute_url'] = None
        res = check_structured_data(ctx, prior_for())
        self.assertEqual(res['changes'], [])
        self.assertTrue(any('Production domain' in i['needed'] for i in res['client_inputs']))

    def test_odd_existing_types_do_not_crash(self):
        ctx = make_ctx('/about', title='About — Acme', json_ld=[{'types': ['Organization', None], 'valid': True},
                                                               {'types': 'Event', 'valid': True}, 'junk'])
        res = check_structured_data(ctx, prior_for(page_type='about'))
        self.assertEqual(res['data']['validation']['existing_types_not_regenerated'], ['Event'])

    def test_byline_needs_a_real_name(self):
        def by(text):
            return facts.author({'eeat_observed': [{'signal': 'authorship', 'status': 'observed', 'evidence': [{'text': text}]}]})
        self.assertIsNone(by('By the end of this guide, you will learn testing'))
        self.assertEqual(by('By Priya Sharma'), 'Priya Sharma')
        self.assertEqual(by('Written by Jo Lee | 5 min read'), 'Jo Lee')

    def test_currency_follows_locale_and_free_plan_sentence(self):
        sec = [section('Pricing', block('p', 'Pro is $29/mo. Start with our free plan.'))]
        au = make_ctx('/pricing', title='Pricing', sections=sec)
        au['lang'] = 'en-AU'
        rows = facts.offers(au)
        self.assertEqual([(r['price'], r['currency']) for r in rows], [('29', 'AUD')])  # one offer per plan section
        mixed = make_ctx('/pricing', title='Pricing', sections=[section('Pricing', block('p', 'Free plan or $9/mo.'))])
        self.assertEqual([r['price'] for r in facts.offers(mixed)], ['9'])
        de = make_ctx('/pricing', title='Pricing', sections=[section('Pro', block('p', '¥500 per month'))])
        de['lang'] = 'de'
        self.assertEqual(facts.offers(de), [])  # ambiguous symbol, unknown locale: not guessed

    def test_unlabeled_date_is_not_published(self):
        ctx = {'eeat_observed': [{'signal': 'dates', 'status': 'observed', 'evidence': [{'text': 'Offer ends Dec 31, 2026'}]}]}
        self.assertEqual(facts.dates(ctx), {'published': None, 'modified': None})

    def test_relative_src_resolves_against_page_and_is_encoded(self):
        ctx = {'origin': ORIGIN, 'url': '/blog/post-1', 'absolute_url': ORIGIN + '/blog/post-1'}
        self.assertEqual(facts.absolute(ctx, 'cover.jpg'), ORIGIN + '/blog/cover.jpg')
        self.assertEqual(facts.absolute(ctx, '/uploads/My Photo.jpg'), ORIGIN + '/uploads/My%20Photo.jpg')

    def test_citation_in_plan_description_is_stripped(self):
        plan = {'title': 'Fix flaky tests', 'description': 'Why tests flake and how to fix them [S1].', 'h1': 'Fix flaky tests'}
        ctx = make_ctx('/blog/flaky', title='Flaky', sections=[section('Why', block('p', 'Timing.'))])
        _res, _ld, nodes = run(ctx, prior_for(page_type='article', plan=plan))
        self.assertEqual(nodes['BlogPosting']['description'], 'Why tests flake and how to fix them.')


class ExistingMarkupTests(unittest.TestCase):
    def test_existing_nodes_are_merged_or_carried_over(self):
        existing = {'@context': 'https://schema.org', '@graph': [
            {'@type': 'Organization', 'name': 'Acme Inc', 'foundingDate': '2019-04-01', 'logo': 'not a url',
             'address': {'@type': 'PostalAddress', 'addressLocality': 'Austin', 'addressCountry': 'US'}},
            {'@type': 'Event', 'name': 'Acme Summit', 'startDate': '2026-12-01',
             'location': {'@type': 'Place', 'name': 'Austin', 'address': 'Austin, TX'}},
            {'@type': 'Course', 'name': 'Broken', 'startDate': 'next week'}]}
        ctx = make_ctx('/about', title='About — Acme', sections=[section('Our story', block('p', 'We build test tools.'))],
                       json_ld=[{'types': ['Organization', 'Event', 'Course'], 'valid': True, 'value': existing}])
        res, ld, nodes = run(ctx, prior_for(page_type='about'))
        org = nodes['Organization']
        self.assertEqual(org['name'], 'Acme')  # our value wins
        self.assertEqual(org['foundingDate'], '2019-04-01')  # owner fact kept
        self.assertEqual(org['address']['addressLocality'], 'Austin')
        self.assertNotIn('logo', org)  # invalid owner value reverted, never breaks the node
        self.assertEqual(nodes['Event']['@id'], ORIGIN + '/about#existing-event-2')
        self.assertNotIn('Course', nodes)  # invalid carried-over node pruned
        validation = res['data']['validation']
        self.assertEqual(validation['existing_carried_over'], ['Event'])
        self.assertEqual(validation['existing_types_not_regenerated'], ['Course'])
        self.assertEqual(rules.errors_of(rules.validate(ld)), [])
        self.assertTrue(res['changes'])


class LiveFindingsTests(unittest.TestCase):
    """Regressions from a live run on real pages (2026-11-03)."""

    def test_lowercase_type_is_named(self):
        data = {'@context': 'https://schema.org', '@graph': [{'@type': 'product', '@id': ORIGIN + '/#p', 'name': 'X'}]}
        self.assertIn("use 'Product'", ' '.join(r['message'] for r in rules.validate(data)))

    def test_howto_extras_are_merged(self):
        existing = {'@context': 'https://schema.org', '@type': 'HowTo', 'name': 'Enable it',
                    'tool': [{'@type': 'HowToTool', 'name': 'Acme CLI'}], 'totalTime': 'PT5M'}
        ctx = make_ctx('/docs/enable', title='Enable — Acme', h1='Enable it', json_ld=[
            {'types': ['HowTo', 'HowToTool'], 'valid': True, 'value': existing}], sections=[
            section('Steps', block('li', 'Open settings.'), block('li', 'Turn it on.'))])
        res, _ld, nodes = run(ctx, prior_for(page_type='docs'))
        self.assertEqual(nodes['HowTo']['totalTime'], 'PT5M')
        self.assertEqual(nodes['HowTo']['tool'][0]['name'], 'Acme CLI')
        self.assertEqual(res['data']['validation']['existing_types_not_regenerated'], [])

    def test_text_directly_in_div_is_content(self):
        from seo_agents.repo_analysis.measurement.html_summary import summarize_html
        html = ('<main><details><summary><h3>Can I cancel?</h3></summary>'
                '<div class="faq-answer">You can cancel <b>any time</b> you want.</div></details>'
                '<div>Hi</div><p>Para <span>text</span></p></main>')
        blocks = [(b['tag'], b['text']) for b in summarize_html(html, 'https://acme.dev/')['blocks']]
        self.assertIn(('div', 'You can cancel any time you want.'), blocks)
        self.assertIn(('p', 'Para text'), blocks)
        self.assertNotIn(('div', 'Hi'), blocks)  # under 3 words: not content

    def test_decorative_images_are_not_the_page_image(self):
        ctx = {'origin': ORIGIN, 'url': '/', 'absolute_url': ORIGIN + '/', 'images': [
            {'src': '/img/background-wave-pattern.webp', 'alt': 'bg-pattern', 'natural': [1200, 700]},
            {'src': '/img/spacer.png', 'alt': '', 'natural': [1200, 700]},
            {'src': '/img/product-shot.webp', 'alt': 'Test editor', 'natural': [1200, 700]}]}
        self.assertEqual([i['url'] for i in facts.page_images(ctx)], [ORIGIN + '/img/product-shot.webp'])

    def test_repeated_or_unlabelled_unsized_images_are_skipped(self):
        icon = {'src': '/img/book.png', 'alt': 'book'}
        ctx = {'origin': ORIGIN, 'url': '/', 'absolute_url': ORIGIN + '/',
               'images': [icon, icon, icon, {'src': '/img/x.png', 'alt': 'x'}, {'src': '/img/flow.png', 'alt': 'Checkout flow diagram'}]}
        self.assertEqual([i['url'] for i in facts.page_images(ctx)], [ORIGIN + '/img/flow.png'])

    def test_site_wide_inputs_are_asked_once(self):
        from seo_agents.onpage.output.onpage_output_writer import merge_site_inputs
        a = make_ctx('/a', title='A — Acme', site_type='personal')
        b = make_ctx('/b', title='B — Acme', site_type='personal')
        rows = []
        for ctx in (a, b):
            res = check_structured_data(ctx, prior_for(site_type='personal'))
            rows += [{**i, 'url': ctx['url']} for i in res['client_inputs']]
        portrait = [r for r in merge_site_inputs(rows) if 'Portrait' in r['needed']]
        self.assertEqual(len(portrait), 1)
        self.assertEqual(portrait[0]['urls'], ['/a', '/b'])


class ShowcaseTests(unittest.TestCase):
    def test_featured_projects_not_jobs(self):
        ctx = make_ctx('/', title='Jo Lee — Engineer', site_type='personal', sections=[
            section('Work Experience'), section('Engineer at Acme', block('p', 'Remote, full-time role.'), level=3),
            section('Engineer at Beta', block('p', 'Remote, part-time role.'), level=3),
            section('Selected Work'), section('Orbit - Web app.', block('p', 'Plans trips from a prompt.'), level=3),
            section('Orbit - Mobile app.', block('p', 'Plans trips on the go.'), level=3), section('Skills')],
            images=[{'src': '/img/OrbitWeb.png', 'alt': 'Orbit web screenshot', 'natural': [1200, 800]}])
        _res, ld, nodes = run(ctx, prior_for(page_type='home', site_type='personal'))
        show = nodes['ItemList']
        self.assertEqual(show['name'], 'Selected Work')
        items = [e['item'] for e in show['itemListElement']]
        self.assertEqual([i['name'] for i in items], ['Orbit - Web app', 'Orbit - Mobile app'])
        self.assertEqual(items[0]['image'], ORIGIN + '/img/OrbitWeb.png')
        self.assertNotIn('image', items[1])  # an image is never reused for a different item
        self.assertEqual(items[0]['creator'], {'@id': ORIGIN + '/#person'})
        self.assertEqual(rules.errors_of(rules.validate(ld)), [])


class RealPricingPageTests(unittest.TestCase):
    """Shapes seen on live pricing pages (Basecamp, Linear, Notion; 2026-11-03)."""

    def test_price_in_heading_badges_tables_and_faq(self):
        ctx = make_ctx('/pricing', title='Pricing — Acme', sections=[
            section('Free', block('div', 'Free for everyone'), block('li', 'Unlimited members'), block('li', '2 teams'), level=3),
            section('Basic', block('div', '$10 per user/month'), block('label', 'Billed yearly'), level=3),
            section('BusinessRecommended', block('div', '$20 per seat/month billed annually'), level=3),
            section('Freelancer, $29/mo', block('p', 'Get organized.')),
            section('Plans and features', block('td', '$99'), block('td', 'Unlimited')),
            section('I have pricing questions…', block('p', 'Yes, $50/month buys 1 TB more storage.'))])
        rows = [(r['name'], r['price'], r['currency'], r['unit'], r['per']) for r in facts.offers(ctx)]
        self.assertEqual(rows, [('Free', '0', None, None, None), ('Basic', '10', 'USD', 'MON', 'user'),
                                ('Business', '20', 'USD', 'MON', 'seat'), ('Freelancer', '29', 'USD', 'MON', None)])
        _res, ld, nodes = run(ctx, prior_for(page_type='product'))
        self.assertIn('SoftwareApplication', nodes)  # per-seat monthly pricing = software sold as a service
        self.assertEqual(rules.errors_of(rules.validate(ld)), [])

    def test_rupee_symbol_is_not_found_inside_words(self):
        self.assertEqual(list(facts.MONEY.finditer('Unlimited members 2 teams')), [])


class BatchFindingsTests(unittest.TestCase):
    """From a 25-page live sample of testsigma.com (2026-11-03)."""

    def test_nested_page_stub_is_a_reference_not_an_error(self):
        data = {'@context': 'https://schema.org', '@graph': [{
            '@type': 'Article', '@id': ORIGIN + '/p#a', 'headline': 'H',
            'isPartOf': {'@type': 'WebPage', '@id': ORIGIN + '/p'}}]}
        self.assertEqual(rules.errors_of(rules.validate(data)), [])

    def test_site_article_merges_into_our_blog_posting(self):
        existing = {'@context': 'https://schema.org', '@type': 'Article', 'headline': 'Old', 'datePublished': '2025-01-02',
                    'isPartOf': {'@type': 'WebPage', '@id': ORIGIN + '/blog/x'}}
        ctx = make_ctx('/blog/x', title='X — Acme', h1='X explained', json_ld=[{'types': ['Article', 'WebPage'], 'valid': True,
                                                                              'value': existing}],
                       sections=[section('Intro', block('p', 'Some text about X.'))])
        res, ld, nodes = run(ctx, prior_for(page_type='article'))
        self.assertNotIn('Article', nodes)  # merged, not duplicated
        self.assertEqual(nodes['BlogPosting']['headline'], 'X explained')  # our visible H1 wins
        self.assertEqual(nodes['BlogPosting']['datePublished'], '2025-01-02')  # the site's own date is kept
        self.assertEqual(rules.errors_of(rules.validate(ld)), [])


class AccordionFaqTests(unittest.TestCase):
    def test_details_summary_faq_is_marked_up(self):
        from seo_agents.repo_analysis.measurement.html_summary import summarize_html
        html = ('<main><h2>FAQs</h2><details><summary><h3>How do we automate cross browser testing?</h3></summary>'
                '<div class="faq-answer"><p>Run one test across Chrome, Firefox, Safari and Edge on Windows, Mac and Linux '
                'from the cloud grid.</p></div></details></main>')
        blocks = summarize_html(html, ORIGIN + '/x')['blocks']
        ctx = make_ctx('/x', title='X', sections=[section('FAQs', *[block(b['tag'], b['text']) for b in blocks if b['tag'] != 'h2'])])
        _res, ld, nodes = run(ctx, prior_for())
        self.assertEqual(nodes['FAQPage']['mainEntity'][0]['name'], 'How do we automate cross browser testing?')
        self.assertEqual(rules.errors_of(rules.validate(ld)), [])

    def test_site_webpage_merges_into_our_page_node(self):
        existing = {'@context': 'https://schema.org', '@type': 'WebPage', 'name': 'Old', 'dateModified': '2026-01-05'}
        ctx = make_ctx('/pricing', title='Pricing — Acme', json_ld=[{'types': ['WebPage'], 'valid': True, 'value': existing}],
                       sections=[section('Team', block('p', '$9 per user/month'))])
        _res, _ld, nodes = run(ctx, prior_for(page_type='product'))
        self.assertNotIn('WebPage', nodes)  # no second page node
        self.assertEqual(nodes['ItemPage']['dateModified'], '2026-01-05')


if __name__ == '__main__':
    unittest.main()
