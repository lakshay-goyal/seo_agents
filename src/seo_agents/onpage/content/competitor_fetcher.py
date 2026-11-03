"""Fetch competitor pages (and the live SERP that names them) for the content studio.

CompetitorFetcher  GET one public HTML page: robots.txt respected (per host, cached), redirects followed, HTML only,
                   size-capped, cached on disk for SEO_CONTENT_CACHE_TTL_DAYS (default 7: competitor content must be
                   fresh). Errors are returned as {'url', 'error'}; nothing raises.
SerpSource         top organic results + People Also Ask for a keyword through the keyword-research provider toolkit
                   (DataForSEO). Without provider credentials it falls back to WebSearch (keyless, robots-allowed
                   html.duckduckgo.com results page, cached); SEO_CONTENT_SEARCH=0 turns the fallback off.

Env:
    SEO_CONTENT_FETCH            '0' turns every network fetch off (default on)
    SEO_CONTENT_CACHE_DIR        cache directory (default .cache/onpage_content)
    SEO_CONTENT_CACHE_TTL_DAYS   cache lifetime in days (default 7)
    SEO_CONTENT_MAX_BYTES        largest HTML body read (default 2 MB)
"""

from __future__ import annotations

from datetime import datetime, timezone
import html as html_lib
import os
import re
import time
from urllib.parse import parse_qs, quote_plus
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from seo_agents.utils.step_logger import log

USER_AGENT = 'seo-agents-content-research/0.1 (+competitor content analysis)'
ROBOTS_AGENT = 'seo-agents-content-research'


def env_on(name, default=True):
    raw = (os.getenv(name) or '').strip().lower()
    return default if not raw else raw not in {'0', 'false', 'no', 'off'}


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


class CompetitorFetcher:
    """Tool: fetch(url) -> {'url', 'final_url', 'status', 'html', 'last_modified', 'fetched_at'} | {'url', 'error'}.

    `get(url) -> (status, final_url, headers, text)` can be injected (tests); the default uses httpx."""

    def __init__(self, get=None, *, cache=None, enabled=None, max_bytes=None, timeout=15.0):
        self.enabled = env_on('SEO_CONTENT_FETCH') if enabled is None else enabled
        self.max_bytes = int(max_bytes or os.getenv('SEO_CONTENT_MAX_BYTES') or 2_000_000)
        self.timeout = timeout
        self._get = get
        self._client = None
        self.cache = None if cache is False else (cache if cache is not None else self._default_cache())  # False = no cache
        self._robots = {}
        self.fetched = 0

    @staticmethod
    def _default_cache():
        from seo_agents.keyword_research.providers.http_cache import JsonCache, env_number
        return JsonCache(os.getenv('SEO_CONTENT_CACHE_DIR') or '.cache/onpage_content',
                         env_number('SEO_CONTENT_CACHE_TTL_DAYS', 7.0), True)

    # ---------------------------------------------------------------- transport
    def get(self, url):
        if self._get is not None:
            return self._get(url)
        import httpx
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout, follow_redirects=True,
                                        headers={'User-Agent': USER_AGENT, 'Accept': 'text/html,application/xhtml+xml'})
        with self._client.stream('GET', url) as response:
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size >= self.max_bytes:
                    break
            body = b''.join(chunks)[:self.max_bytes]
            charset = response.charset_encoding or 'utf-8'
            return (response.status_code, str(response.url), dict(response.headers),
                    body.decode(charset, errors='replace'))

    def allowed(self, url):
        """robots.txt check for our user agent (unreachable robots.txt = allowed, as crawlers do)."""
        parts = urlparse(url)
        origin = parts.scheme + '://' + parts.netloc
        if origin not in self._robots:
            parser = RobotFileParser()
            try:
                status, _, _, text = self.get(origin + '/robots.txt')
                parser.parse(text.splitlines() if status < 400 else [])
            except Exception:  # noqa: BLE001 - robots unreachable: treat as allow-all
                parser.parse([])
            self._robots[origin] = parser
        return self._robots[origin].can_fetch(ROBOTS_AGENT, url)

    # ---------------------------------------------------------------- fetch
    def fetch(self, url):
        if not isinstance(url, str) or not url.startswith(('http://', 'https://')):
            return {'url': url, 'error': 'not an absolute http(s) URL'}
        if not self.enabled:
            return {'url': url, 'error': 'fetching disabled (SEO_CONTENT_FETCH=0)'}
        key = self.cache.key('GET', url) if self.cache else None
        if key:
            hit = self.cache.get(key)
            if hit:
                return {**hit, 'cached': True}
        try:
            if not self.allowed(url):
                return {'url': url, 'error': 'disallowed by robots.txt'}
            status, final_url, headers, text = self.get(url)
        except Exception as error:  # noqa: BLE001 - one competitor must not stop the run
            return {'url': url, 'error': f'{type(error).__name__}: {str(error)[:160]}'}
        lowered = {k.lower(): v for k, v in (headers or {}).items()}
        if status >= 400:
            return {'url': url, 'error': f'HTTP {status}'}
        if 'html' not in (lowered.get('content-type') or 'text/html').lower():
            return {'url': url, 'error': 'not HTML: ' + str(lowered.get('content-type'))[:60]}
        page = {'url': url, 'final_url': final_url, 'status': status, 'html': text,
                'last_modified': lowered.get('last-modified'), 'fetched_at': now_iso()}
        self.fetched += 1
        if key:
            self.cache.set(key, page, endpoint=url.split('?')[0])
        return page


RESULT_LINK = re.compile(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)


class WebSearch:
    """Tool: keyless web search fallback -> [{'position', 'url', 'domain', 'title'}] (organic results only).

    Reads the html.duckduckgo.com results page (its robots.txt allows crawling); one request per keyword, cached by the
    CompetitorFetcher cache, 1.5 s between live requests, so a run makes only a handful of polite queries."""

    endpoint = 'https://html.duckduckgo.com/html/?q='

    def __init__(self, get=None, *, cache=None, delay=1.5):
        self.fetcher = CompetitorFetcher(get=get, cache=cache)
        self.delay = delay
        self._last = 0.0

    def search(self, keyword, limit=10):
        url = self.endpoint + quote_plus(keyword)
        cache = self.fetcher.cache
        key = cache.key('GET', url) if cache else None
        page = cache.get(key) if key else None
        if not page:
            wait = self.delay - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            page = self.fetcher.fetch(url)
            if key and page.get('html') and 'result__a' in page['html']:
                cache.set(key, page, endpoint='websearch')
        if page.get('error'):
            raise RuntimeError(page['error'])
        results = []
        for href, title in RESULT_LINK.findall(page.get('html') or ''):
            href = html_lib.unescape(href)
            target = parse_qs(href.split('?', 1)[1]).get('uddg', [None])[0] if 'uddg=' in href else href
            if not target or not target.startswith('http') or 'duckduckgo.com' in target:
                continue
            host = urlparse(target).netloc.lower()
            results.append({'position': len(results) + 1, 'url': target, 'domain': host[4:] if host.startswith('www.') else host,
                            'title': html_lib.unescape(re.sub(r'<[^>]+>', '', title)).strip()})
            if len(results) >= limit:
                break
        return results


AGGREGATORS = ('youtube.com', 'reddit.com', 'quora.com', 'wikipedia.org', 'linkedin.com', 'facebook.com', 'x.com',
               'twitter.com', 'pinterest.com', 'amazon.', 'stackoverflow.com', 'github.com')


class SerpSource:
    """Tool: top(keyword, market) -> {'results': [{position, url, domain, title}], 'people_also_ask', 'error'}.

    Uses the keyword-research ProviderResearchToolkit (DataForSEO SERP, shared on-disk cache with keyword research)."""

    def __init__(self, toolkit=None, *, enabled=None, web_search=None):
        self.enabled = env_on('SEO_CONTENT_FETCH') if enabled is None else enabled
        self._toolkit = toolkit
        self._loaded = toolkit is not None
        self.web_search = web_search if web_search is not None else (WebSearch() if env_on('SEO_CONTENT_SEARCH') else False)

    def fallback(self, keyword, own_domain, limit):
        if not self.web_search:
            return {'results': [], 'people_also_ask': [], 'error': 'no SERP provider (DataForSEO credentials not set)'}
        try:
            found = self.web_search.search(keyword, limit=limit + 6)
        except Exception as error:  # noqa: BLE001
            return {'results': [], 'people_also_ask': [], 'error': f'web search: {type(error).__name__}: {str(error)[:160]}'}
        own = (urlparse(own_domain).netloc or own_domain or '').lower().removeprefix('www.') if own_domain else None
        results = [{**r, 'aggregator': any(a in r['domain'] for a in AGGREGATORS)} for r in found
                   if not (own and r['domain'] == own)]
        return {'results': results[:limit], 'people_also_ask': [], 'features': [], 'error': None, 'provider': 'web_search'}

    @property
    def toolkit(self):
        if not self._loaded:
            self._loaded = True
            try:
                from seo_agents.keyword_research.providers.research_toolkit import ProviderResearchToolkit
                toolkit = ProviderResearchToolkit.from_env()
                self._toolkit = toolkit if getattr(toolkit, 'dataforseo', None) else None
            except Exception as error:  # noqa: BLE001
                log('content studio: SERP provider unavailable: ' + type(error).__name__)
                self._toolkit = None
        return self._toolkit

    def top(self, keyword, market=None, *, own_domain=None, limit=8):
        if not self.enabled or not keyword:
            return {'results': [], 'people_also_ask': [], 'error': 'SERP disabled' if not self.enabled else 'no keyword'}
        toolkit = self.toolkit
        if toolkit is None:
            return self.fallback(keyword, own_domain, limit)
        from seo_agents.keyword_research.contracts import Market
        from seo_agents.keyword_research.providers import metrics_compute as mc
        try:
            snapshot = toolkit.serp(keyword, market or Market())
        except Exception as error:  # noqa: BLE001
            return {'results': [], 'people_also_ask': [], 'error': f'{type(error).__name__}: {str(error)[:160]}'}
        if snapshot is None:
            return {'results': [], 'people_also_ask': [], 'error': 'SERP returned nothing'}
        own = mc.domain_of(own_domain) if own_domain else None
        results = []
        for r in snapshot.results:
            if r.result_type != 'organic' or not r.url or (own and mc.matches_domain(r.domain, [own])):
                continue
            results.append({'position': r.position, 'url': r.url, 'domain': r.domain, 'title': r.title,
                            'aggregator': mc.is_aggregator(r.domain)})
        return {'results': results[:limit], 'people_also_ask': list(snapshot.people_also_ask or [])[:10],
                'features': list(snapshot.features or []), 'error': None}
