"""Google Trends via DataForSEO keywords_data/google_trends/explore/live (the official Trends API is alpha-only).

At most 5 keywords per comparison. Values are relative (0-100) WITHIN one comparison batch: values from different
batches are not comparable, so each keyword's result carries its `batch` (the keywords it was compared with).
"""

from __future__ import annotations

from seo_agents.keyword_research.providers.dataforseo import EP_TRENDS, DataForSEOClient
from seo_agents.keyword_research.providers.http_cache import utc_now

BATCH = 5


class GoogleTrendsProvider:
    name = 'google_trends'

    def __init__(self, client: DataForSEOClient):
        self.client = client

    @property
    def available(self) -> bool:
        return self.client.available

    def compare(self, keywords: list[str], market, *, time_range: str = 'past_12_months') -> dict[str, dict]:
        """{keyword: {'interest_over_time': [{'date_from','date_to','value'}], 'average', 'region_interest':
        [{'region','geo_id','value'}], 'batch', 'provenance'}}. Missing data points are None, not 0."""
        out = {}
        keywords = list(dict.fromkeys(k.strip().lower() for k in keywords if k and k.strip()))
        for i in range(0, len(keywords), BATCH):
            batch = keywords[i:i + BATCH]
            result = self.client.trends_explore(batch, market, time_range=time_range) or {}
            provenance = {'provider': self.name, 'endpoint': EP_TRENDS, 'retrieved_at': utc_now(),
                          'location': market.location_code, 'language': market.language_code}
            entry = {k: {'interest_over_time': [], 'average': None, 'region_interest': [], 'batch': batch,
                         'provenance': provenance} for k in batch}
            for item in result.get('items') or []:
                item_keywords = [str(k).lower() for k in (item.get('keywords') or batch)]
                if item.get('type') == 'google_trends_graph':
                    averages = item.get('averages') or []
                    for idx, kw in enumerate(item_keywords):
                        if kw not in entry:
                            continue
                        entry[kw]['average'] = averages[idx] if idx < len(averages) else None
                        for point in item.get('data') or []:
                            values = point.get('values') or []
                            value = values[idx] if idx < len(values) and not point.get('missing_data') else None
                            entry[kw]['interest_over_time'].append(
                                {'date_from': point.get('date_from'), 'date_to': point.get('date_to'), 'value': value})
                elif item.get('type') == 'google_trends_map':
                    for idx, kw in enumerate(item_keywords):
                        if kw not in entry:
                            continue
                        for region in item.get('data') or []:
                            values = region.get('values') or []
                            entry[kw]['region_interest'].append({
                                'region': region.get('geo_name'), 'geo_id': region.get('geo_id'),
                                'value': values[idx] if idx < len(values) else None})
            out.update(entry)
        return out
