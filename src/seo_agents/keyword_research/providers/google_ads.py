"""Google Ads KeywordPlanIdeaService over REST (OAuth refresh-token flow).

    POST https://googleads.googleapis.com/{version}/customers/{id}:generateKeywordHistoricalMetrics
    POST https://googleads.googleapis.com/{version}/customers/{id}:generateKeywordIdeas

JSON uses the proto3 camelCase mapping; int64 fields (avgMonthlySearches, competitionIndex, *Micros, year) arrive as
strings. Ads `competition` is PAID-search competition: it is stored as ads_competition, never keyword_difficulty.

Env: GOOGLE_ADS_DEVELOPER_TOKEN, GOOGLE_ADS_CLIENT_ID, GOOGLE_ADS_CLIENT_SECRET, GOOGLE_ADS_REFRESH_TOKEN,
     GOOGLE_ADS_CUSTOMER_ID, optional GOOGLE_ADS_LOGIN_CUSTOMER_ID, optional GOOGLE_ADS_API_VERSION (default v26).
"""

from __future__ import annotations

import os

from seo_agents.keyword_research.providers.http_cache import HttpTransport, ProviderError

TOKEN_URL = 'https://oauth2.googleapis.com/token'
BASE_URL = 'https://googleads.googleapis.com'
DEFAULT_VERSION = 'v26'
HISTORICAL_BATCH = 1000
IDEA_SEED_LIMIT = 20

MONTHS = {name: i for i, name in enumerate(
    ['JANUARY', 'FEBRUARY', 'MARCH', 'APRIL', 'MAY', 'JUNE', 'JULY', 'AUGUST', 'SEPTEMBER', 'OCTOBER',
     'NOVEMBER', 'DECEMBER'], start=1)}

# Google Ads language constants (languageConstants/{id}) for common ISO codes; unknown -> no language filter.
LANGUAGE_IDS = {'en': 1000, 'de': 1001, 'fr': 1002, 'es': 1003, 'it': 1004, 'ja': 1005, 'da': 1009, 'nl': 1010,
                'fi': 1011, 'ko': 1012, 'no': 1013, 'pt': 1014, 'sv': 1015, 'zh': 1017, 'ar': 1019, 'hi': 1023,
                'pl': 1030, 'ru': 1031, 'tr': 1037}


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_metrics(metrics: dict | None) -> dict:
    """Ads keyword metrics -> plain values. Absent fields stay None (unknown)."""
    metrics = metrics or {}
    index = _int(metrics.get('competitionIndex'))
    low, high = _int(metrics.get('lowTopOfPageBidMicros')), _int(metrics.get('highTopOfPageBidMicros'))
    monthly = []
    for row in metrics.get('monthlySearchVolumes') or []:
        month = MONTHS.get(str(row.get('month', '')).upper())
        year = _int(row.get('year'))
        if month and year:
            monthly.append({'year': year, 'month': month, 'volume': _int(row.get('monthlySearches'))})
    return {
        'search_volume': _int(metrics.get('avgMonthlySearches')),
        'ads_competition': None if index is None else round(index / 100, 2),
        'ads_competition_level': metrics.get('competition') if metrics.get('competition') not in (None, 'UNSPECIFIED', 'UNKNOWN') else None,
        'bid_low': None if low is None else round(low / 1_000_000, 2),
        'bid_high': None if high is None else round(high / 1_000_000, 2),
        'monthly_searches': monthly or None,
    }


class GoogleAdsClient:
    name = 'google_ads'

    def __init__(self, transport: HttpTransport, *, developer_token=None, client_id=None, client_secret=None,
                 refresh_token=None, customer_id=None, login_customer_id=None, version=DEFAULT_VERSION):
        self.transport = transport
        self.developer_token = developer_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.customer_id = (customer_id or '').replace('-', '').strip() or None
        self.login_customer_id = (login_customer_id or '').replace('-', '').strip() or None
        self.version = version or DEFAULT_VERSION
        self._access_token = None

    @classmethod
    def from_env(cls, transport: HttpTransport):
        return cls(transport, developer_token=os.getenv('GOOGLE_ADS_DEVELOPER_TOKEN'),
                   client_id=os.getenv('GOOGLE_ADS_CLIENT_ID'), client_secret=os.getenv('GOOGLE_ADS_CLIENT_SECRET'),
                   refresh_token=os.getenv('GOOGLE_ADS_REFRESH_TOKEN'), customer_id=os.getenv('GOOGLE_ADS_CUSTOMER_ID'),
                   login_customer_id=os.getenv('GOOGLE_ADS_LOGIN_CUSTOMER_ID'),
                   version=os.getenv('GOOGLE_ADS_API_VERSION') or DEFAULT_VERSION)

    @property
    def available(self) -> bool:
        return all([self.developer_token, self.client_id, self.client_secret, self.refresh_token, self.customer_id])

    def endpoint(self, method: str) -> str:
        return BASE_URL + '/' + self.version + '/customers/' + str(self.customer_id) + ':' + method

    def _token(self) -> str:
        if self._access_token:
            return self._access_token
        fetched = self.transport.fetch('POST', TOKEN_URL, cacheable=False, data={
            'client_id': self.client_id, 'client_secret': self.client_secret,
            'refresh_token': self.refresh_token, 'grant_type': 'refresh_token'})
        token = (fetched.data or {}).get('access_token')
        if not token:
            raise ProviderError('google_ads: OAuth token refresh returned no access_token')
        self._access_token = token
        return token

    def _post(self, method: str, body: dict) -> dict:
        if not self.available:
            raise ProviderError('google_ads: credentials not configured')
        headers = {'Authorization': 'Bearer ' + self._token(), 'developer-token': self.developer_token}
        if self.login_customer_id:
            headers['login-customer-id'] = self.login_customer_id
        return self.transport.fetch('POST', self.endpoint(method), json_body=body, headers=headers).data or {}

    @staticmethod
    def _targeting(market) -> dict:
        body = {'geoTargetConstants': ['geoTargetConstants/' + str(market.location_code)],
                'keywordPlanNetwork': 'GOOGLE_SEARCH'}
        language = LANGUAGE_IDS.get((market.language_code or '').lower()[:2])
        if language:
            body['language'] = 'languageConstants/' + str(language)
        return body

    def historical_metrics(self, keywords: list[str], market) -> dict[str, dict]:
        out = {}
        for i in range(0, len(keywords), HISTORICAL_BATCH):
            body = {'keywords': keywords[i:i + HISTORICAL_BATCH], **self._targeting(market)}
            data = self._post('generateKeywordHistoricalMetrics', body)
            for row in data.get('results') or []:
                parsed = parse_metrics(row.get('keywordMetrics'))
                for text in [row.get('text')] + list(row.get('closeVariants') or []):
                    if text:
                        out.setdefault(text.lower(), parsed)
        return out

    def keyword_ideas(self, seeds: list[str], market, *, limit: int = 50) -> list[tuple[str, dict]]:
        body = {'keywordSeed': {'keywords': seeds[:IDEA_SEED_LIMIT]}, 'pageSize': max(1, min(limit, 10000)),
                **self._targeting(market)}
        data = self._post('generateKeywordIdeas', body)
        out = []
        for row in data.get('results') or []:
            if row.get('text'):
                out.append((row['text'].lower(), parse_metrics(row.get('keywordIdeaMetrics'))))
        return out[:limit]
