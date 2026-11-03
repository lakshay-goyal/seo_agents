"""ProviderResearchToolkit: implements contracts.ResearchToolkit by aggregating every configured provider.

Rules: a metric a provider did not return is None (unknown) with the provenance of the call that was asked; provider
failures are appended to KeywordEvidence.errors; volumes keep both provenances (search_volume = DataForSEO,
search_volume_ads = Google Ads; search_volume falls back to Ads only when DataForSEO is not configured or returned
nothing). Ads competition is paid competition (ads_competition) and never becomes keyword_difficulty.

Env (besides each provider's credentials): SEO_KEYWORD_ENRICH_SERP ('0' skips the per-keyword SERP call in enrich(),
which then leaves click_opportunity / serp_authority_share unknown).
"""

from __future__ import annotations

from collections import defaultdict

from seo_agents.keyword_research.contracts import (
    Intent, KeywordCandidate, KeywordEvidence, KeywordKind, Market, Metric, PageBrief, Provenance, SerpResult,
    SerpSnapshot, Source, Tail,
)
from seo_agents.keyword_research.providers import metrics_compute as mc
from seo_agents.keyword_research.providers.dataforseo import (
    EP_DIFFICULTY, EP_INTENT, EP_OVERVIEW, EP_RANKED, EP_RELATED, EP_SERP, EP_SUGGESTIONS, DataForSEOClient,
)
from seo_agents.keyword_research.providers.google_ads import GoogleAdsClient
from seo_agents.keyword_research.providers.http_cache import HttpTransport, ProviderError, env_flag, utc_now
from seo_agents.keyword_research.providers.questions import AutocompleteQuestions, is_question
from seo_agents.keyword_research.providers.search_console import SearchConsoleClient
from seo_agents.keyword_research.providers.trends import GoogleTrendsProvider
from seo_agents.utils.step_logger import log

def normalize(keyword: str) -> str:
    return ' '.join((keyword or '').lower().split())


def tail_of(keyword: str) -> Tail:
    return Tail.LONG if len(keyword.split()) >= 3 else Tail.SHORT


def intent_of(label) -> Intent | None:
    try:
        return Intent(str(label).lower()) if label else None
    except ValueError:
        return None


class ProviderResearchToolkit:
    def __init__(self, *, dataforseo: DataForSEOClient | None = None, google_ads: GoogleAdsClient | None = None,
                 search_console: SearchConsoleClient | None = None, autocomplete: AutocompleteQuestions | None = None,
                 trends: GoogleTrendsProvider | None = None, enrich_serp: bool = True, log_skipped: bool = True):
        self.dataforseo = dataforseo if dataforseo and dataforseo.available else None
        self.google_ads = google_ads if google_ads and google_ads.available else None
        self.search_console = search_console if search_console and search_console.available else None
        self.autocomplete = autocomplete if autocomplete and autocomplete.available else None
        if trends is None and self.dataforseo:
            trends = GoogleTrendsProvider(self.dataforseo)
        self.trends = trends if trends and trends.available else None
        self.enrich_serp = enrich_serp
        self._serp_cache: dict[tuple, SerpSnapshot | None] = {}
        if log_skipped:
            self._log_skipped()

    @classmethod
    def from_env(cls, transport: HttpTransport | None = None):
        transport = transport or HttpTransport.from_env()
        dataforseo = DataForSEOClient.from_env(transport)
        return cls(dataforseo=dataforseo, google_ads=GoogleAdsClient.from_env(transport),
                   search_console=SearchConsoleClient.from_env(transport),
                   autocomplete=AutocompleteQuestions.from_env(transport),
                   trends=GoogleTrendsProvider(dataforseo), enrich_serp=env_flag('SEO_KEYWORD_ENRICH_SERP', True))

    def _log_skipped(self):
        notes = {
            'dataforseo': (self.dataforseo, 'DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD not set'),
            'google_ads': (self.google_ads, 'GOOGLE_ADS_* credentials not set'),
            'gsc': (self.search_console, 'GSC_CLIENT_ID / GSC_CLIENT_SECRET / GSC_REFRESH_TOKEN / GSC_SITE_URL not set'),
            'google_trends': (self.trends, 'needs DataForSEO credentials'),
            'autocomplete': (self.autocomplete, 'disabled by SEO_KEYWORD_AUTOCOMPLETE=0'),
        }
        for name, (provider, why) in notes.items():
            if provider is None:
                log('keyword providers: ' + name + ' skipped (' + why + ')')
        log('keyword providers: active = ' + (', '.join(self.available_providers()) or 'none'))

    def available_providers(self) -> list[str]:
        pairs = [('dataforseo', self.dataforseo), ('google_ads', self.google_ads), ('gsc', self.search_console),
                 ('google_trends', self.trends), ('autocomplete', self.autocomplete)]
        return [name for name, provider in pairs if provider is not None]

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _prov(provider: str, endpoint: str, market: Market | None, *, location=None) -> Provenance:
        return Provenance(provider=provider, endpoint=endpoint, retrieved_at=utc_now(),
                          location=location if location is not None else (market.location_code if market else None),
                          language=market.language_code if market else None)

    @staticmethod
    def _fail(evidence: dict[str, KeywordEvidence], keys, provider: str, error: Exception):
        message = provider + ': ' + str(error)
        log('keyword providers: ' + message)
        for key in keys:
            evidence[key].errors.append(message)

    # ------------------------------------------------------------ enrich
    def enrich(self, keywords: list[str], market: Market, *, page: PageBrief | None = None) -> dict[str, KeywordEvidence]:
        keys = list(dict.fromkeys(normalize(k) for k in keywords if normalize(k)))
        evidence = {k: KeywordEvidence(keyword=k) for k in keys}
        if not keys:
            return evidence
        if self.dataforseo:
            self._enrich_dataforseo(keys, market, evidence)
        if self.google_ads:
            self._enrich_ads(keys, market, evidence)
        if self.search_console:
            self._enrich_gsc(keys, market, evidence)
        for key, ev in evidence.items():
            self._computed_trend(ev, market)
            if self.dataforseo and self.enrich_serp:
                try:
                    snapshot = self.serp(key, market, raise_errors=True)
                except ProviderError as error:
                    self._fail(evidence, [key], 'dataforseo', error)
                    snapshot = None
                if snapshot is not None:
                    ev.serp = snapshot
                    computed = self._prov('computed', 'metrics_compute.serp', market)
                    ev.metrics['click_opportunity'] = Metric(mc.click_opportunity(snapshot.features), computed)
                    ev.metrics['serp_authority_share'] = Metric(mc.serp_authority_share(snapshot.results), computed)
                    ev.competitor_urls = [r.url for r in snapshot.results if r.result_type == 'organic'
                                          and not mc.is_high_authority(r.domain) and not mc.is_aggregator(r.domain)
                                          and not (page and page.absolute_url
                                                   and mc.matches_domain(r.domain, [mc.domain_of(page.absolute_url)]))][:5]
            if page is not None:
                ev.metrics['content_alignment'] = Metric(
                    mc.content_alignment(key, page), self._prov('computed', 'metrics_compute.content_alignment', market))
        return evidence

    def _enrich_dataforseo(self, keys, market, evidence):
        try:
            overview = self.dataforseo.keyword_overview(keys, market)
        except ProviderError as error:
            self._fail(evidence, keys, 'dataforseo', error)
            return
        prov = self._prov('dataforseo', EP_OVERVIEW, market)
        missing_kd, missing_intent = [], []
        for key in keys:
            item = overview.get(key) or {}
            info = item.get('keyword_info') or {}
            props = item.get('keyword_properties') or {}
            intent = intent_of((item.get('search_intent_info') or {}).get('main_intent'))
            monthly = [{'year': m.get('year'), 'month': m.get('month'), 'volume': m.get('search_volume')}
                       for m in info.get('monthly_searches') or []] or None
            m = evidence[key].metrics
            m['search_volume'] = Metric(info.get('search_volume'), prov)
            m['keyword_difficulty'] = Metric(props.get('keyword_difficulty'), prov)
            m['cpc'] = Metric(info.get('cpc'), prov)
            m['ads_competition'] = Metric(info.get('competition'), prov)
            m['provider_intent'] = Metric(intent.value if intent else None, prov)
            m['monthly_searches'] = Metric(monthly, prov)
            if props.get('keyword_difficulty') is None:
                missing_kd.append(key)
            if intent is None:
                missing_intent.append(key)
        if missing_kd:
            try:
                kd = self.dataforseo.bulk_keyword_difficulty(missing_kd, market)
                prov_kd = self._prov('dataforseo', EP_DIFFICULTY, market)
                for key in missing_kd:
                    if kd.get(key) is not None:
                        evidence[key].metrics['keyword_difficulty'] = Metric(kd[key], prov_kd)
            except ProviderError as error:
                self._fail(evidence, missing_kd, 'dataforseo', error)
        if missing_intent:
            try:
                intents = self.dataforseo.search_intent(missing_intent, market)
                prov_in = Provenance('dataforseo', EP_INTENT, utc_now(), None, market.language_code)
                for key in missing_intent:
                    intent = intent_of((intents.get(key) or {}).get('label'))
                    if intent is not None:
                        evidence[key].metrics['provider_intent'] = Metric(intent.value, prov_in)
            except ProviderError as error:
                self._fail(evidence, missing_intent, 'dataforseo', error)

    def _enrich_ads(self, keys, market, evidence):
        try:
            ads = self.google_ads.historical_metrics(keys, market)
        except ProviderError as error:
            self._fail(evidence, keys, 'google_ads', error)
            return
        prov = self._prov('google_ads', 'KeywordPlanIdeaService.generateKeywordHistoricalMetrics', market)
        for key in keys:
            row = ads.get(key) or {}
            m = evidence[key].metrics
            m['search_volume_ads'] = Metric(row.get('search_volume'), prov)
            m['ads_competition_ads'] = Metric(row.get('ads_competition'), prov)
            m['ads_competition_level'] = Metric(row.get('ads_competition_level'), prov)
            m['bid_low'] = Metric(row.get('bid_low'), prov)
            m['bid_high'] = Metric(row.get('bid_high'), prov)
            # Fallbacks only when DataForSEO gave nothing; provenance shows the value came from Ads.
            for name, value in (('search_volume', row.get('search_volume')),
                                ('ads_competition', row.get('ads_competition')),
                                ('monthly_searches', row.get('monthly_searches'))):
                if value is not None and evidence[key].value(name) is None:
                    m[name] = Metric(value, prov)

    def _enrich_gsc(self, keys, market, evidence):
        try:
            rows = self.search_console.site_queries()
        except ProviderError as error:
            self._fail(evidence, keys, 'gsc', error)
            return
        prov = self._prov('gsc', 'searchAnalytics.query', market, location='site-wide')
        for key in keys:
            row = rows.get(key)
            if row is None:
                continue   # absent row = unknown, not zero
            for field in ('impressions', 'clicks', 'ctr', 'position'):
                evidence[key].metrics['gsc_' + field] = Metric(row.get(field), prov)

    def _computed_trend(self, ev: KeywordEvidence, market):
        monthly = ev.value('monthly_searches')
        if not monthly:
            return
        direction, peaks = mc.trend_analysis(monthly)
        prov = self._prov('computed', 'metrics_compute.trend_analysis', market)
        ev.metrics['trend_direction'] = Metric(direction, prov)
        ev.metrics['seasonal_peak_months'] = Metric(peaks if direction == 'seasonal' else None, prov)

    # ------------------------------------------------------------ SERP
    def serp(self, keyword: str, market: Market, *, raise_errors: bool = False) -> SerpSnapshot | None:
        if not self.dataforseo:
            return None
        key = (normalize(keyword), market.location_code, market.language_code)
        if key in self._serp_cache:
            return self._serp_cache[key]
        try:
            raw = self.dataforseo.serp(key[0], market)
        except ProviderError as error:
            if raise_errors:
                raise
            log('keyword providers: dataforseo serp: ' + str(error))
            return None
        snapshot = parse_serp(key[0], raw, self._prov('dataforseo', EP_SERP, market)) if raw else None
        self._serp_cache[key] = snapshot
        return snapshot

    def competitor_urls(self, keywords: list[str], market: Market, *, own_domain: str | None,
                        limit: int = 5) -> list[str]:
        own = mc.domain_of(own_domain) if own_domain else None
        counts, positions = defaultdict(int), defaultdict(list)
        for keyword in keywords:
            snapshot = self.serp(keyword, market)
            for r in (snapshot.results if snapshot else []):
                if r.result_type != 'organic' or not r.url:
                    continue
                if (own and mc.matches_domain(r.domain, [own])) or mc.is_high_authority(r.domain) \
                        or mc.is_aggregator(r.domain):
                    continue
                counts[r.url] += 1
                positions[r.url].append(r.position)
        ranked = sorted(counts, key=lambda u: (-counts[u], sum(positions[u]) / len(positions[u]), u))
        return ranked[:limit]

    # ------------------------------------------------------------ candidate sources
    def expand(self, seeds: list[str], market: Market, *, limit: int = 50) -> list[KeywordCandidate]:
        seeds = [normalize(s) for s in seeds if normalize(s)]
        seen = set(seeds)
        out: list[KeywordCandidate] = []
        if not seeds:
            return out
        per_seed = max(5, limit // max(1, len(seeds)))

        def add(text, source, intent, rationale):
            text = normalize(text)
            if text and text not in seen and len(out) < limit:
                seen.add(text)
                out.append(KeywordCandidate(keyword=text, page_id='', kind=KeywordKind.SECONDARY, source=source,
                                            tail=tail_of(text), hypothesized_intent=intent, rationale=rationale))

        if self.dataforseo:
            for seed in seeds:
                for endpoint, call in ((EP_SUGGESTIONS, self.dataforseo.keyword_suggestions),
                                       (EP_RELATED, self.dataforseo.related_keywords)):
                    try:
                        items = call(seed, market, limit=per_seed)
                    except ProviderError as error:
                        log('keyword providers: dataforseo expand: ' + str(error))
                        continue
                    for item in items:
                        info = item.get('keyword_info') or {}
                        add(item.get('keyword'), Source.RELATED_EXPANSION,
                            intent_of((item.get('search_intent_info') or {}).get('main_intent')),
                            'dataforseo ' + endpoint.split('/')[-2] + ' of "' + seed + '" (volume '
                            + str(info.get('search_volume')) + ', kd '
                            + str((item.get('keyword_properties') or {}).get('keyword_difficulty')) + ')')
        if self.google_ads:
            try:
                for text, metrics in self.google_ads.keyword_ideas(seeds, market, limit=limit):
                    add(text, Source.ADS_IDEAS, None, 'google ads keyword ideas (avg monthly searches '
                        + str(metrics.get('search_volume')) + ', paid competition '
                        + str(metrics.get('ads_competition_level')) + ')')
            except ProviderError as error:
                log('keyword providers: google_ads ideas: ' + str(error))
        return out

    def questions(self, seeds: list[str], market: Market, *, limit: int = 30) -> list[KeywordCandidate]:
        seeds = [normalize(s) for s in seeds if normalize(s)]
        seen = set(seeds)
        out: list[KeywordCandidate] = []

        def add(text, rationale):
            text = normalize(text).rstrip('?').strip()
            if text and text not in seen:
                seen.add(text)
                kind = KeywordKind.QUESTION if is_question(text) else KeywordKind.SECONDARY
                out.append(KeywordCandidate(keyword=text, page_id='', kind=kind, source=Source.QUESTION_EXPANSION,
                                            tail=tail_of(text), rationale=rationale))

        for seed in seeds:   # People-Also-Ask first: real questions Google shows for the seed
            snapshot = self.serp(seed, market) if self.dataforseo else None
            for question in (snapshot.people_also_ask if snapshot else []):
                add(question, 'people also ask for "' + seed + '"')
        if self.autocomplete:
            per_seed = max(5, limit // max(1, len(seeds)))
            for seed in seeds:
                try:
                    for suggestion in self.autocomplete.expand(seed, market, limit=per_seed):
                        add(suggestion, 'google autocomplete for "' + seed + '"')
                except ProviderError as error:
                    log('keyword providers: autocomplete: ' + str(error))
        out.sort(key=lambda c: c.kind != KeywordKind.QUESTION)
        return out[:limit]

    def competitor_keywords(self, url: str, market: Market, *, limit: int = 100) -> list[KeywordCandidate]:
        if not self.dataforseo:
            return []
        try:
            rows = self.dataforseo.ranked_keywords(url, market, limit=limit)
        except ProviderError as error:
            log('keyword providers: dataforseo ranked_keywords: ' + str(error))
            return []
        out, seen = [], set()
        for item, serp_item in rows:
            text = normalize(item.get('keyword'))
            if not text or text in seen:
                continue
            seen.add(text)
            info = item.get('keyword_info') or {}
            out.append(KeywordCandidate(
                keyword=text, page_id='', kind=KeywordKind.SECONDARY, source=Source.COMPETITOR_REVERSE,
                tail=tail_of(text), hypothesized_intent=intent_of((item.get('search_intent_info') or {}).get('main_intent')),
                rationale='competitor page ranks #' + str(serp_item.get('rank_group')) + ' (volume '
                          + str(info.get('search_volume')) + ', kd '
                          + str((item.get('keyword_properties') or {}).get('keyword_difficulty')) + ') via '
                          + EP_RANKED,
                competitor_url=url))
        return out[:limit]

    def page_queries(self, page_url: str, market: Market) -> list[KeywordCandidate]:
        if not self.search_console:
            return []
        try:
            rows = self.search_console.page_queries(page_url)
        except ProviderError as error:
            log('keyword providers: gsc: ' + str(error))
            return []
        rows.sort(key=lambda r: -(r.get('impressions') or 0))
        return [KeywordCandidate(keyword=r['query'], page_id='', kind=KeywordKind.SECONDARY, source=Source.GSC,
                                 tail=tail_of(r['query']),
                                 rationale='search console (90d): ' + str(r.get('impressions')) + ' impressions, '
                                           + str(r.get('clicks')) + ' clicks, avg position ' + str(r.get('position')))
                for r in rows]

    def compare_trends(self, keywords: list[str], market: Market) -> dict[str, dict]:
        if not self.trends:
            return {}
        out = {}
        keys = list(dict.fromkeys(normalize(k) for k in keywords if normalize(k)))
        for i in range(0, len(keys), 5):
            batch = keys[i:i + 5]
            try:
                out.update(self.trends.compare(batch, market))
            except ProviderError as error:
                log('keyword providers: google_trends: ' + str(error))
                for k in batch:
                    out[k] = {'error': 'google_trends: ' + str(error)}
        return out


def parse_serp(keyword: str, raw: dict, provenance: Provenance) -> SerpSnapshot:
    results, paa = [], []
    features = [t for t in (raw.get('item_types') or []) if t != 'organic']
    for item in raw.get('items') or []:
        kind = item.get('type') or 'other'
        if kind not in features and kind != 'organic':
            features.append(kind)
        if kind == 'people_also_ask':
            for element in item.get('items') or []:
                if element.get('title'):
                    paa.append(element['title'].strip())
            continue
        if not item.get('url'):
            continue
        position = item.get('rank_group') if kind == 'organic' else item.get('rank_absolute')
        results.append(SerpResult(position=int(position or 0), url=item['url'],
                                  domain=mc.domain_of(item.get('domain') or item['url']), title=item.get('title') or '',
                                  result_type=kind, page_type_guess=mc.page_type_guess(item['url'], item.get('title'))))
    return SerpSnapshot(keyword=keyword, results=results, features=features, people_also_ask=paa,
                        provenance=provenance)
