"""Google Search Console searchAnalytics.query (OAuth refresh token).

    POST https://www.googleapis.com/webmasters/v3/sites/{siteUrl}/searchAnalytics/query

Env: GSC_CLIENT_ID, GSC_CLIENT_SECRET, GSC_REFRESH_TOKEN, GSC_SITE_URL ('https://example.com/' or 'sc-domain:example.com').
Window: last 90 days ending 3 days ago (GSC data lag). A query absent from the rows means UNKNOWN, never zero searches.
Rows are site-wide (no country filter), recorded as location 'site-wide' in provenance.
"""

from __future__ import annotations

from datetime import date, timedelta
import os
from urllib.parse import quote

from seo_agents.keyword_research.providers.http_cache import HttpTransport, ProviderError

TOKEN_URL = 'https://oauth2.googleapis.com/token'
BASE_URL = 'https://www.googleapis.com/webmasters/v3/sites/'
WINDOW_DAYS = 90
LAG_DAYS = 3
ROW_LIMIT = 25000


class SearchConsoleClient:
    name = 'gsc'

    def __init__(self, transport: HttpTransport, *, client_id=None, client_secret=None, refresh_token=None,
                 site_url=None, today: date | None = None):
        self.transport = transport
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.site_url = site_url
        self.today = today
        self._access_token = None
        self._site_rows = None

    @classmethod
    def from_env(cls, transport: HttpTransport):
        return cls(transport, client_id=os.getenv('GSC_CLIENT_ID'), client_secret=os.getenv('GSC_CLIENT_SECRET'),
                   refresh_token=os.getenv('GSC_REFRESH_TOKEN'), site_url=os.getenv('GSC_SITE_URL'))

    @property
    def available(self) -> bool:
        return all([self.client_id, self.client_secret, self.refresh_token, self.site_url])

    @property
    def endpoint(self) -> str:
        return BASE_URL + quote(self.site_url or '', safe='') + '/searchAnalytics/query'

    def window(self) -> tuple[str, str]:
        end = (self.today or date.today()) - timedelta(days=LAG_DAYS)
        return (end - timedelta(days=WINDOW_DAYS)).isoformat(), end.isoformat()

    def _token(self):
        if self._access_token:
            return self._access_token
        fetched = self.transport.fetch('POST', TOKEN_URL, cacheable=False, data={
            'client_id': self.client_id, 'client_secret': self.client_secret,
            'refresh_token': self.refresh_token, 'grant_type': 'refresh_token'})
        token = (fetched.data or {}).get('access_token')
        if not token:
            raise ProviderError('gsc: OAuth token refresh returned no access_token')
        self._access_token = token
        return token

    def query(self, *, page_url: str | None = None, row_limit: int = ROW_LIMIT) -> list[dict]:
        if not self.available:
            raise ProviderError('gsc: credentials not configured')
        start, end = self.window()
        body = {'startDate': start, 'endDate': end, 'dimensions': ['query'], 'rowLimit': row_limit, 'type': 'web'}
        if page_url:
            body['dimensionFilterGroups'] = [{'filters': [
                {'dimension': 'page', 'operator': 'equals', 'expression': page_url}]}]
        data = self.transport.fetch('POST', self.endpoint, json_body=body,
                                    headers={'Authorization': 'Bearer ' + self._token()}).data or {}
        rows = []
        for row in data.get('rows') or []:
            keys = row.get('keys') or []
            if keys and keys[0]:
                rows.append({'query': keys[0].lower(), 'clicks': row.get('clicks'), 'impressions': row.get('impressions'),
                             'ctr': row.get('ctr'), 'position': row.get('position')})
        return rows

    def page_queries(self, page_url: str) -> list[dict]:
        return self.query(page_url=page_url)

    def site_queries(self) -> dict[str, dict]:
        """Site-wide query rows, fetched once per run."""
        if self._site_rows is None:
            self._site_rows = {row['query']: row for row in self.query()}
        return self._site_rows
