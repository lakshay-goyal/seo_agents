"""DataForSEO v3 client (live endpoints). Basic auth DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD.

Every method returns raw-but-normalized provider dicts; research_toolkit.py turns them into Metrics with provenance.
Response paths follow https://docs.dataforseo.com/v3/ (tasks[0].result[*].items[*]).

Spend guard: SEO_KEYWORD_MAX_SPEND (USD, optional). The `cost` field of each non-cached response is summed; once the
total reaches the cap further calls raise ProviderError (recorded in KeywordEvidence.errors, never a fake value).
"""

from __future__ import annotations

import os

import httpx

from seo_agents.keyword_research.providers.http_cache import HttpTransport, ProviderError, env_number
from seo_agents.utils.step_logger import log

BASE_URL = 'https://api.dataforseo.com/v3'

EP_OVERVIEW = 'dataforseo_labs/google/keyword_overview/live'
EP_DIFFICULTY = 'dataforseo_labs/google/bulk_keyword_difficulty/live'
EP_INTENT = 'dataforseo_labs/google/search_intent/live'
EP_SUGGESTIONS = 'dataforseo_labs/google/keyword_suggestions/live'
EP_RELATED = 'dataforseo_labs/google/related_keywords/live'
EP_RANKED = 'dataforseo_labs/google/ranked_keywords/live'
EP_SERP = 'serp/google/organic/live/advanced'
EP_TRENDS = 'keywords_data/google_trends/explore/live'

BATCH_LIMITS = {EP_OVERVIEW: 700, EP_DIFFICULTY: 1000, EP_INTENT: 1000, EP_TRENDS: 5}
OK = 20000


def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def flat_keyword_item(item):
    """keyword_suggestions items are flat; related_keywords / ranked_keywords nest them under keyword_data."""
    return item.get('keyword_data') if isinstance(item.get('keyword_data'), dict) else item


class DataForSEOClient:
    name = 'dataforseo'

    def __init__(self, login: str | None, password: str | None, transport: HttpTransport, *,
                 max_spend: float | None = None, base_url: str = BASE_URL):
        self._auth = httpx.BasicAuth(login, password) if login and password else None
        self.transport = transport
        self.max_spend = max_spend
        self.base_url = base_url.rstrip('/')
        self.spent = 0.0
        self.calls = 0

    @classmethod
    def from_env(cls, transport: HttpTransport):
        spend = env_number('SEO_KEYWORD_MAX_SPEND', None)
        return cls(os.getenv('DATAFORSEO_LOGIN'), os.getenv('DATAFORSEO_PASSWORD'), transport, max_spend=spend)

    @property
    def available(self) -> bool:
        return self._auth is not None

    # ------------------------------------------------------------ transport
    def post(self, endpoint: str, task: dict) -> list[dict]:
        """One task per call (live endpoints). Returns tasks[0].result (list, may be empty)."""
        if not self.available:
            raise ProviderError('dataforseo: credentials not configured')
        if self.max_spend is not None and self.spent >= self.max_spend:
            raise ProviderError('dataforseo: SEO_KEYWORD_MAX_SPEND reached (' + format(self.spent, '.4f') + ' USD)')
        fetched = self.transport.fetch('POST', self.base_url + '/' + endpoint, json_body=[task], auth=self._auth)
        body = fetched.data or {}
        if not fetched.cached:
            self.calls += 1
            cost = body.get('cost')
            if isinstance(cost, (int, float)):
                self.spent += cost
                log('dataforseo ' + endpoint + ': cost ' + format(cost, '.4f') + ' USD (run total '
                    + format(self.spent, '.4f') + ')')
        if body.get('status_code') != OK:
            raise ProviderError('dataforseo ' + endpoint + ': ' + str(body.get('status_code')) + ' '
                                + str(body.get('status_message')))
        tasks = body.get('tasks') or []
        if not tasks:
            raise ProviderError('dataforseo ' + endpoint + ': empty tasks')
        task_out = tasks[0]
        if task_out.get('status_code') != OK:
            raise ProviderError('dataforseo ' + endpoint + ': task ' + str(task_out.get('status_code')) + ' '
                                + str(task_out.get('status_message')))
        return task_out.get('result') or []

    @staticmethod
    def items(result: list[dict]) -> list[dict]:
        out = []
        for block in result or []:
            out.extend((block or {}).get('items') or [])
        return out

    @staticmethod
    def _market(market):
        return {'location_code': market.location_code, 'language_code': market.language_code}

    # ------------------------------------------------------------ labs
    def keyword_overview(self, keywords: list[str], market) -> dict[str, dict]:
        out = {}
        for batch in chunks(keywords, BATCH_LIMITS[EP_OVERVIEW]):
            result = self.post(EP_OVERVIEW, {'keywords': batch, **self._market(market)})
            for item in self.items(result):
                if item.get('keyword'):
                    out[item['keyword'].lower()] = item
        return out

    def bulk_keyword_difficulty(self, keywords: list[str], market) -> dict[str, int | None]:
        out = {}
        for batch in chunks(keywords, BATCH_LIMITS[EP_DIFFICULTY]):
            result = self.post(EP_DIFFICULTY, {'keywords': batch, **self._market(market)})
            for item in self.items(result):
                if item.get('keyword'):
                    out[item['keyword'].lower()] = item.get('keyword_difficulty')
        return out

    def search_intent(self, keywords: list[str], market) -> dict[str, dict]:
        out = {}
        for batch in chunks(keywords, BATCH_LIMITS[EP_INTENT]):
            # docs list only `keywords` (+ tag) for this endpoint; no market fields are sent.
            result = self.post(EP_INTENT, {'keywords': batch})
            for item in self.items(result):
                if item.get('keyword') and item.get('keyword_intent'):
                    out[item['keyword'].lower()] = item['keyword_intent']
        return out

    def keyword_suggestions(self, seed: str, market, *, limit: int = 50) -> list[dict]:
        result = self.post(EP_SUGGESTIONS, {'keyword': seed, 'limit': min(max(limit, 1), 1000), **self._market(market)})
        return [flat_keyword_item(i) for i in self.items(result)]

    def related_keywords(self, seed: str, market, *, limit: int = 50, depth: int = 1) -> list[dict]:
        result = self.post(EP_RELATED, {'keyword': seed, 'depth': depth, 'limit': min(max(limit, 1), 1000),
                                        **self._market(market)})
        return [flat_keyword_item(i) for i in self.items(result)]

    def ranked_keywords(self, url: str, market, *, limit: int = 100) -> list[tuple[dict, dict]]:
        """Page-level reverse research: target must be an absolute URL (https://...) or DataForSEO returns the domain."""
        target = url if url.startswith(('http://', 'https://', 'www.')) else 'https://' + url.lstrip('/')
        result = self.post(EP_RANKED, {'target': target, 'limit': min(max(limit, 1), 1000),
                                       'item_types': ['organic'], **self._market(market)})
        out = []
        for item in self.items(result):
            serp_item = ((item.get('ranked_serp_element') or {}).get('serp_item')) or {}
            out.append((flat_keyword_item(item), serp_item))
        return out

    # ------------------------------------------------------------ SERP + trends
    def serp(self, keyword: str, market, *, depth: int = 10) -> dict | None:
        result = self.post(EP_SERP, {'keyword': keyword, 'depth': depth, **self._market(market)})
        return result[0] if result else None

    def trends_explore(self, keywords: list[str], market, *, time_range: str = 'past_12_months',
                       item_types=('google_trends_graph', 'google_trends_map')) -> dict | None:
        if len(keywords) > BATCH_LIMITS[EP_TRENDS]:
            raise ValueError('google trends compares at most 5 keywords')
        result = self.post(EP_TRENDS, {'keywords': keywords, 'location_code': market.location_code,
                                       'language_code': market.language_code, 'type': 'web',
                                       'time_range': time_range, 'item_types': list(item_types)})
        return result[0] if result else None
