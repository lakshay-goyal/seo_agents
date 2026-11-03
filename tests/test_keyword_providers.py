"""Keyword providers: no network. httpx.MockTransport serves trimmed fixtures shaped like the DataForSEO / Google docs
examples. Covers unknown != 0, provenance, skipped providers, Ads competition isolation, trend math, click opportunity,
content alignment and the on-disk cache."""

from contextlib import redirect_stdout
import io
import json
import tempfile
import unittest
from unittest import mock

import httpx

from seo_agents.keyword_research.contracts import KeywordKind, Market, PageBrief, SerpResult, Source, Tail
from seo_agents.keyword_research.providers import metrics_compute as mc
from seo_agents.keyword_research.providers.dataforseo import DataForSEOClient
from seo_agents.keyword_research.providers.google_ads import GoogleAdsClient
from seo_agents.keyword_research.providers.http_cache import HttpTransport, JsonCache
from seo_agents.keyword_research.providers.questions import AutocompleteQuestions
from seo_agents.keyword_research.providers.research_toolkit import ProviderResearchToolkit
from seo_agents.keyword_research.providers.search_console import SearchConsoleClient

MARKET = Market()


def dfs(items, cost=0.01, extra_result=None):
    result = [{'items_count': len(items), 'items': items}]
    if extra_result is not None:
        result.insert(0, extra_result)
    return {'version': '0.1.20260917', 'status_code': 20000, 'status_message': 'Ok.', 'cost': cost,
            'tasks_count': 1, 'tasks_error': 0,
            'tasks': [{'id': 't1', 'status_code': 20000, 'status_message': 'Ok.', 'cost': cost, 'result': result}]}


def monthly(volumes, year=2025):
    return [{'year': year + (i // 12), 'month': i % 12 + 1, 'search_volume': v} for i, v in enumerate(volumes)]


OVERVIEW = dfs([{
    'se_type': 'google', 'keyword': 'trail running shoes for flat feet', 'location_code': 2840, 'language_code': 'en',
    'keyword_info': {'competition': 0.96, 'competition_level': 'HIGH', 'cpc': 1.47, 'search_volume': 1900,
                     'low_top_of_page_bid': 0.5, 'high_top_of_page_bid': 2.1,
                     'monthly_searches': monthly([100, 120, 140, 160, 180, 200, 220, 240, 260, 280, 300, 320])},
    'keyword_properties': {'keyword_difficulty': 23, 'detected_language': 'en'},
    'search_intent_info': {'se_type': 'google', 'main_intent': 'commercial', 'foreign_intent': None},
}, {
    # Returned but without data: must stay None (unknown), never 0.
    'se_type': 'google', 'keyword': 'obscure keyword term', 'keyword_info': {'search_volume': None, 'cpc': None,
                                                                           'competition': None, 'monthly_searches': None},
    'keyword_properties': {'keyword_difficulty': None}, 'search_intent_info': None,
}])
DIFFICULTY = dfs([{'se_type': 'google', 'keyword': 'obscure keyword term', 'keyword_difficulty': None}])
INTENT = dfs([{'keyword': 'obscure keyword term', 'keyword_intent': {'label': 'informational', 'probability': 0.91},
               'secondary_keyword_intents': None}])
SERP_RESULT = {
    'keyword': 'trail running shoes for flat feet', 'type': 'organic', 'se_domain': 'google.com',
    'location_code': 2840, 'language_code': 'en',
    'item_types': ['paid', 'featured_snippet', 'people_also_ask', 'organic'],
    'items': [
        {'type': 'paid', 'rank_group': 1, 'rank_absolute': 1, 'domain': 'www.shoeads.com', 'title': 'Ad',
         'url': 'https://www.shoeads.com/'},
        {'type': 'featured_snippet', 'rank_group': 1, 'rank_absolute': 2, 'domain': 'www.runnersworld.com',
         'title': 'Best trail shoes', 'url': 'https://www.runnersworld.com/gear/a1/best-trail-shoes/'},
        {'type': 'people_also_ask', 'rank_group': 1, 'rank_absolute': 3, 'items': [
            {'type': 'people_also_ask_element', 'title': 'What shoes are best for flat feet?'},
            {'type': 'people_also_ask_element', 'title': 'Can you run trails with flat feet?'}]},
        {'type': 'organic', 'rank_group': 1, 'rank_absolute': 4, 'domain': 'www.reddit.com',
         'title': 'Flat feet trail shoes? : r/trailrunning', 'url': 'https://www.reddit.com/r/trailrunning/x'},
        {'type': 'organic', 'rank_group': 2, 'rank_absolute': 5, 'domain': 'www.trailshop.com',
         'title': 'Trail running shoes', 'url': 'https://www.trailshop.com/collections/trail-running-shoes'},
        {'type': 'organic', 'rank_group': 3, 'rank_absolute': 6, 'domain': 'mysite.com',
         'title': 'Our shoes', 'url': 'https://mysite.com/shoes'},
        {'type': 'organic', 'rank_group': 4, 'rank_absolute': 7, 'domain': 'www.g2.com',
         'title': 'Reviews', 'url': 'https://www.g2.com/x'},
        {'type': 'organic', 'rank_group': 5, 'rank_absolute': 8, 'domain': 'flatfootrunner.com',
         'title': 'How to choose trail shoes for flat feet', 'url': 'https://flatfootrunner.com/blog/flat-feet-trail'},
    ]}
SERP = dfs([])
SERP['tasks'][0]['result'] = [SERP_RESULT]   # SERP endpoints: result[0] is the SERP itself (items = SERP elements)
RANKED = dfs([{
    'se_type': 'google',
    'keyword_data': {'se_type': 'google', 'keyword': 'best trail shoes for overpronation',
                     'keyword_info': {'search_volume': 140, 'competition': 0.4},
                     'keyword_properties': {'keyword_difficulty': 7},
                     'search_intent_info': {'main_intent': 'commercial'}},
    'ranked_serp_element': {'se_type': 'google', 'serp_item': {
        'type': 'organic', 'rank_group': 3, 'rank_absolute': 4, 'url': 'https://flatfootrunner.com/blog/flat-feet-trail',
        'etv': 12.5}},
}])
TRENDS = dfs([{
    'position': 1, 'type': 'google_trends_graph', 'title': 'Interest over time', 'keywords': ['a shoes', 'b shoes'],
    'data': [{'date_from': '2025-11-02', 'date_to': '2025-11-08', 'timestamp': 1762041600, 'missing_data': False,
              'values': [40, 80]},
             {'date_from': '2025-11-09', 'date_to': '2025-11-15', 'timestamp': 1762646400, 'missing_data': True,
              'values': [None, None]}],
    'averages': [42, 77]},
    {'position': 2, 'type': 'google_trends_map', 'title': 'Interest by subregion', 'keywords': ['a shoes', 'b shoes'],
     'data': [{'geo_id': 'US-NY', 'geo_name': 'New York', 'values': [55, 100], 'max_value_index': 1}]}])
ADS_HISTORICAL = {'results': [{
    'text': 'trail running shoes for flat feet', 'closeVariants': ['trail running shoe for flat feet'],
    'keywordMetrics': {'competition': 'HIGH', 'avgMonthlySearches': '2400', 'competitionIndex': '88',
                       'lowTopOfPageBidMicros': '510000', 'highTopOfPageBidMicros': '2300000',
                       'monthlySearchVolumes': [{'month': 'JANUARY', 'year': '2025', 'monthlySearches': '2000'}]}},
    {'text': 'obscure keyword term', 'keywordMetrics': {'competition': 'LOW', 'competitionIndex': '12'}}]}
GSC_ROWS = {'rows': [{'keys': ['trail running shoes for flat feet'], 'clicks': 3, 'impressions': 120, 'ctr': 0.025,
                      'position': 14.2}], 'responseAggregationType': 'byProperty'}

ROUTES = {
    'keyword_overview/live': OVERVIEW, 'bulk_keyword_difficulty/live': DIFFICULTY, 'search_intent/live': INTENT,
    'serp/google/organic/live/advanced': SERP, 'ranked_keywords/live': RANKED, 'google_trends/explore/live': TRENDS,
    'generateKeywordHistoricalMetrics': ADS_HISTORICAL, 'oauth2.googleapis.com/token': {'access_token': 'tok',
                                                                                         'expires_in': 3599},
    'searchAnalytics/query': GSC_ROWS,
}


class Recorder:
    def __init__(self, routes=None, status=None):
        self.routes = routes or ROUTES
        self.status = status or {}
        self.requests = []

    def __call__(self, request: httpx.Request):
        self.requests.append(request)
        url = str(request.url)
        for fragment, code in list(self.status.items()):
            if fragment in url and code:
                self.status[fragment] = None   # fail once
                return httpx.Response(code, text='busy')
        for fragment, body in self.routes.items():
            if fragment in url:
                return httpx.Response(200, json=body)
        if 'suggestqueries' in url:
            q = request.url.params['q']
            return httpx.Response(200, json=[q, [q + ' 2025', q + ' explained', 'unrelated thing']])
        return httpx.Response(404, json={'error': 'no route ' + url})


def transport(recorder, cache=None):
    return HttpTransport(httpx.Client(transport=httpx.MockTransport(recorder)), cache, sleep=lambda s: None)


def toolkit(recorder, *, ads=False, gsc=False, autocomplete=False, enrich_serp=True):
    t = transport(recorder)
    with redirect_stdout(io.StringIO()):
        return ProviderResearchToolkit(
            dataforseo=DataForSEOClient('login', 'secret-pass', t),
            google_ads=GoogleAdsClient(t, developer_token='dev', client_id='cid', client_secret='cs', refresh_token='rt',
                                       customer_id='123-456-7890') if ads else None,
            search_console=SearchConsoleClient(t, client_id='c', client_secret='s', refresh_token='r',
                                               site_url='sc-domain:mysite.com') if gsc else None,
            autocomplete=AutocompleteQuestions(t, sleep=lambda s: None) if autocomplete else None,
            enrich_serp=enrich_serp)


PAGE = PageBrief(page_id='p1', url='/shoes', absolute_url='https://mysite.com/shoes', page_type='category',
                 title='Trail Running Shoes for Flat Feet', description='Supportive trail shoes.',
                 headings=['H1: Trail running shoes', 'Choosing support for flat arches'],
                 copy=['Our trail shoes give stability on rocky paths.'], images=[{'src': 'a.jpg', 'alt': 'shoe'}])


class EnrichTests(unittest.TestCase):
    def setUp(self):
        self.rec = Recorder()
        self.kit = toolkit(self.rec, ads=True, gsc=True)
        with redirect_stdout(io.StringIO()):
            self.ev = self.kit.enrich(['Trail running shoes for flat feet', 'obscure keyword term'], MARKET, page=PAGE)

    def test_metrics_and_provenance(self):
        ev = self.ev['trail running shoes for flat feet']
        self.assertEqual(ev.value('search_volume'), 1900)
        self.assertEqual(ev.value('keyword_difficulty'), 23)
        self.assertEqual(ev.value('provider_intent'), 'commercial')
        self.assertEqual(ev.value('trend_direction'), 'rising')
        for name, metric in ev.metrics.items():
            self.assertIsNotNone(metric.provenance, name)
            self.assertTrue(metric.provenance.retrieved_at.endswith('Z'), name)
        self.assertEqual(ev.metrics['search_volume'].provenance.provider, 'dataforseo')
        self.assertEqual(ev.metrics['search_volume'].provenance.location, 2840)
        self.assertEqual(ev.metrics['trend_direction'].provenance.provider, 'computed')

    def test_unknown_is_not_zero(self):
        ev = self.ev['obscure keyword term']
        self.assertIn('search_volume', ev.metrics)
        self.assertIsNone(ev.metrics['search_volume'].value)
        self.assertIsNone(ev.value('keyword_difficulty'))
        self.assertIsNone(ev.value('cpc'))
        self.assertEqual(ev.value('provider_intent'), 'informational')   # filled by search_intent fallback
        self.assertNotIn('gsc_impressions', ev.metrics)                    # absent GSC row = unknown
        self.assertIsNone(ev.value('search_volume_ads'))

    def test_ads_volume_kept_separately_and_competition_never_difficulty(self):
        ev = self.ev['trail running shoes for flat feet']
        self.assertEqual(ev.value('search_volume_ads'), 2400)
        self.assertEqual(ev.metrics['search_volume_ads'].provenance.provider, 'google_ads')
        self.assertEqual(ev.value('ads_competition_ads'), 0.88)
        self.assertEqual(ev.value('bid_high'), 2.3)
        self.assertEqual(ev.value('keyword_difficulty'), 23)
        obscure = self.ev['obscure keyword term']
        self.assertIsNone(obscure.value('keyword_difficulty'))   # Ads index 12 must NOT land here
        self.assertEqual(obscure.value('ads_competition'), 0.12)  # DataForSEO had none -> Ads fallback, Ads provenance
        self.assertEqual(obscure.metrics['ads_competition'].provenance.provider, 'google_ads')

    def test_gsc_and_serp_metrics(self):
        ev = self.ev['trail running shoes for flat feet']
        self.assertEqual(ev.value('gsc_impressions'), 120)
        self.assertEqual(ev.metrics['gsc_position'].provenance.provider, 'gsc')
        self.assertEqual(ev.serp.people_also_ask, ['What shoes are best for flat feet?',
                                                   'Can you run trails with flat feet?'])
        self.assertAlmostEqual(ev.value('click_opportunity'), 1 - 0.15 - 0.15 - 0.05)
        self.assertAlmostEqual(ev.value('serp_authority_share'), 0.2)   # reddit 1 of 5 organic
        self.assertEqual(ev.competitor_urls, ['https://www.trailshop.com/collections/trail-running-shoes',
                                              'https://flatfootrunner.com/blog/flat-feet-trail'])
        self.assertGreater(ev.value('content_alignment'), 0.9)

    def test_credentials_not_logged_and_ads_competition_paid(self):
        auth = [r.headers.get('authorization', '') for r in self.rec.requests if 'dataforseo' in str(r.url)]
        self.assertTrue(all(a.startswith('Basic ') for a in auth))
        ads = [r for r in self.rec.requests if 'googleads' in str(r.url)][0]
        self.assertIn('/customers/1234567890:generateKeywordHistoricalMetrics', str(ads.url))
        self.assertEqual(ads.headers['developer-token'], 'dev')
        body = json.loads(ads.content)
        self.assertEqual(body['geoTargetConstants'], ['geoTargetConstants/2840'])
        self.assertEqual(body['language'], 'languageConstants/1000')


class ProviderErrorTests(unittest.TestCase):
    def test_error_recorded_not_zeroed(self):
        rec = Recorder(routes={k: v for k, v in ROUTES.items() if k != 'keyword_overview/live'})
        kit = toolkit(rec, enrich_serp=False)
        with redirect_stdout(io.StringIO()):
            ev = kit.enrich(['some keyword here'], MARKET)['some keyword here']
        self.assertTrue(any('HTTP 404' in e for e in ev.errors))
        self.assertIsNone(ev.value('search_volume'))
        self.assertNotIn('secret-pass', ' '.join(ev.errors))

    def test_retry_on_429(self):
        rec = Recorder(status={'keyword_overview/live': 429})
        kit = toolkit(rec, enrich_serp=False)
        with redirect_stdout(io.StringIO()):
            ev = kit.enrich(['trail running shoes for flat feet'], MARKET)
        self.assertEqual(ev['trail running shoes for flat feet'].value('search_volume'), 1900)
        self.assertEqual(sum('keyword_overview' in str(r.url) for r in rec.requests), 2)

    def test_spend_cap(self):
        rec = Recorder()
        client = DataForSEOClient('l', 'p', transport(rec), max_spend=0.01)
        with redirect_stdout(io.StringIO()):
            client.keyword_overview(['a b c'], MARKET)
            with self.assertRaisesRegex(Exception, 'MAX_SPEND'):
                client.keyword_overview(['d e f'], MARKET)


class AvailabilityTests(unittest.TestCase):
    def test_missing_credentials_skip_providers(self):
        env = {'SEO_KEYWORD_AUTOCOMPLETE': '0', 'SEO_KEYWORD_CACHE': '0'}
        out = io.StringIO()
        with mock.patch.dict('os.environ', env, clear=True), redirect_stdout(out):
            kit = ProviderResearchToolkit.from_env(transport(Recorder()))
            self.assertEqual(kit.available_providers(), [])
            self.assertEqual(kit.enrich(['x y z'], MARKET)['x y z'].metrics, {})
            self.assertEqual(kit.competitor_keywords('https://a.com/x', MARKET), [])
            self.assertIsNone(kit.serp('x', MARKET))
        self.assertIn('dataforseo skipped', out.getvalue())

    def test_env_enables_providers(self):
        env = {'DATAFORSEO_LOGIN': 'l', 'DATAFORSEO_PASSWORD': 'p', 'GSC_CLIENT_ID': 'c', 'GSC_CLIENT_SECRET': 's',
               'GSC_REFRESH_TOKEN': 'r', 'GSC_SITE_URL': 'https://mysite.com/'}
        with mock.patch.dict('os.environ', env, clear=True), redirect_stdout(io.StringIO()):
            kit = ProviderResearchToolkit.from_env(transport(Recorder()))
        self.assertEqual(kit.available_providers(), ['dataforseo', 'gsc', 'google_trends', 'autocomplete'])


class CandidateSourceTests(unittest.TestCase):
    def test_competitor_keywords_page_level(self):
        rec = Recorder()
        kit = toolkit(rec)
        cands = kit.competitor_keywords('flatfootrunner.com/blog/flat-feet-trail', MARKET)
        self.assertEqual(len(cands), 1)
        c = cands[0]
        self.assertEqual((c.keyword, c.source, c.tail), ('best trail shoes for overpronation',
                                                         Source.COMPETITOR_REVERSE, Tail.LONG))
        self.assertIn('#3', c.rationale)
        body = json.loads([r for r in rec.requests if 'ranked_keywords' in str(r.url)][0].content)
        self.assertEqual(body[0]['target'], 'https://flatfootrunner.com/blog/flat-feet-trail')

    def test_competitor_urls_exclude_own_and_authorities(self):
        kit = toolkit(Recorder())
        urls = kit.competitor_urls(['trail running shoes for flat feet'], MARKET, own_domain='https://www.mysite.com')
        self.assertEqual(urls, ['https://www.trailshop.com/collections/trail-running-shoes',
                                'https://flatfootrunner.com/blog/flat-feet-trail'])

    def test_questions_paa_and_autocomplete(self):
        rec = Recorder()
        kit = toolkit(rec, autocomplete=True)
        kit.autocomplete.max_requests = 3
        cands = kit.questions(['trail running shoes for flat feet'], MARKET, limit=10)
        texts = [c.keyword for c in cands]
        self.assertEqual(texts[0], 'what shoes are best for flat feet')
        self.assertTrue(all(c.source == Source.QUESTION_EXPANSION for c in cands))
        self.assertIn('what trail running shoes for flat feet explained', texts)
        self.assertNotIn('unrelated thing', texts)
        self.assertEqual(sum('suggestqueries' in str(r.url) for r in rec.requests), 3)   # request cap
        self.assertEqual(cands[0].kind, KeywordKind.QUESTION)

    def test_compare_trends(self):
        kit = toolkit(Recorder())
        out = kit.compare_trends(['a shoes', 'b shoes'], MARKET)
        self.assertEqual(out['b shoes']['average'], 77)
        self.assertEqual(out['a shoes']['interest_over_time'][0]['value'], 40)
        self.assertIsNone(out['a shoes']['interest_over_time'][1]['value'])   # missing_data -> unknown
        self.assertEqual(out['b shoes']['region_interest'], [{'region': 'New York', 'geo_id': 'US-NY', 'value': 100}])
        self.assertEqual(out['a shoes']['provenance']['provider'], 'google_trends')


class ComputeTests(unittest.TestCase):
    def test_trend_directions(self):
        def rows(values):
            return [{'year': 2025, 'month': i + 1, 'volume': v} for i, v in enumerate(values)]
        self.assertEqual(mc.trend_direction(rows([100] * 12)), 'stable')
        self.assertEqual(mc.trend_direction(rows(range(100, 220, 10))), 'rising')
        self.assertEqual(mc.trend_direction(rows(range(220, 100, -10))), 'declining')
        seasonal = rows([50, 50, 50, 50, 50, 300, 320, 50, 50, 50, 50, 50])
        self.assertEqual(mc.trend_analysis(seasonal), ('seasonal', [6, 7]))
        self.assertIsNone(mc.trend_direction(rows([10, 20])))              # too few points -> unknown
        self.assertIsNone(mc.trend_direction(rows([None] * 12)))
        self.assertIsNone(mc.trend_direction(rows([0] * 12)))

    def test_click_opportunity(self):
        self.assertEqual(mc.click_opportunity([]), 1.0)
        self.assertIsNone(mc.click_opportunity(None))
        self.assertEqual(mc.click_opportunity(['ai_overview', 'featured_snippet', 'paid', 'shopping', 'local_pack',
                                               'knowledge_graph', 'video']), 0.0)
        self.assertEqual(mc.click_opportunity(['video', 'video']), 0.95)

    def test_authority_share(self):
        results = [SerpResult(i + 1, 'https://' + d + '/x', d, 't', 'organic')
                   for i, d in enumerate(['en.wikipedia.org', 'irs.gov', 'shop.com', 'blog.io'])]
        self.assertEqual(mc.serp_authority_share(results), 0.5)
        self.assertIsNone(mc.serp_authority_share([]))

    def test_content_alignment(self):
        self.assertEqual(mc.content_alignment('trail running shoes for flat feet', PAGE), 1.0)
        partial = mc.content_alignment('waterproof trail shoes', PAGE)
        self.assertAlmostEqual(partial, round(2 / 3, 3))
        self.assertAlmostEqual(mc.content_alignment('rocky paths', PAGE), 0.4)
        self.assertEqual(mc.content_alignment('arch support', PAGE), 0.7)
        self.assertEqual(mc.content_alignment('kayak paddles', PAGE), 0.0)
        self.assertIsNone(mc.content_alignment('x', None))

    def test_page_type_guess(self):
        self.assertEqual(mc.page_type_guess('https://a.com/'), 'home')
        self.assertEqual(mc.page_type_guess('https://a.com/products/x-1'), 'product')
        self.assertEqual(mc.page_type_guess('https://a.com/collections/shoes'), 'category')
        self.assertEqual(mc.page_type_guess('https://a.com/blog/how-to'), 'article')
        self.assertEqual(mc.page_type_guess('https://www.reddit.com/r/x'), 'forum')
        self.assertEqual(mc.page_type_guess('https://a.com/bmi-calculator'), 'tool')


class CacheTests(unittest.TestCase):
    def test_cache_hit_skips_network_and_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            rec = Recorder()
            client = DataForSEOClient('l', 'p', transport(rec, JsonCache(tmp, ttl_days=14)))
            with redirect_stdout(io.StringIO()):
                first = client.keyword_overview(['trail running shoes for flat feet'], MARKET)
                second = client.keyword_overview(['trail running shoes for flat feet'], MARKET)
            self.assertEqual(first, second)
            self.assertEqual(len(rec.requests), 1)
            self.assertEqual(client.calls, 1)
            self.assertAlmostEqual(client.spent, 0.01)
            # Expired entries are refetched; disabled cache never stores.
            expired = DataForSEOClient('l', 'p', transport(rec, JsonCache(tmp, ttl_days=0)))
            with redirect_stdout(io.StringIO()):
                expired.keyword_overview(['trail running shoes for flat feet'], MARKET)
            self.assertEqual(len(rec.requests), 2)

    def test_cache_files_hold_no_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = DataForSEOClient('login-user', 'secret-pass', transport(Recorder(), JsonCache(tmp)))
            with redirect_stdout(io.StringIO()):
                client.keyword_overview(['a b c'], MARKET)
            import pathlib
            blobs = ' '.join(p.read_text() for p in pathlib.Path(tmp).rglob('*.json'))
            self.assertTrue(blobs)
            self.assertNotIn('secret-pass', blobs)

    def test_cache_env(self):
        with mock.patch.dict('os.environ', {'SEO_KEYWORD_CACHE': '0', 'SEO_KEYWORD_CACHE_TTL_DAYS': '3'}):
            cache = JsonCache.from_env()
        self.assertFalse(cache.enabled)
        self.assertEqual(cache.ttl_seconds, 3 * 86400)


if __name__ == '__main__':
    unittest.main()
