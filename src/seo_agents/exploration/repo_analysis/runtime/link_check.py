"""Link status checks: every internal destination, a bounded sample of external ones.

HEAD first; GET when HEAD is refused or unreliable (403/404/405/429/501: many CDNs and WAFs
answer HEAD differently from GET). Redirects are followed manually (max 6) so the chain is kept.
Bot walls (LinkedIn's 999, Cloudflare/Akamai challenges, 403/429 from social networks) are
reported as `blocked`: the link may well work for people, so it is neither broken nor redirected.
Parallel with a small thread pool; no cookies, no credentials, bounded timeouts.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

USER_AGENT = 'Mozilla/5.0 (compatible; seo-agents-linkcheck/1.0)'
RETRY_WITH_GET = {403, 404, 405, 429, 501}
REDIRECTS = {301, 302, 303, 307, 308}
# Hosts that routinely refuse automated clients; a 4xx/999 from them says nothing about the link.
BOT_WALL_HOSTS = ('linkedin.com', 'twitter.com', 'x.com', 'instagram.com', 'facebook.com', 'medium.com',
                  'quora.com', 'glassdoor.com', 'indeed.com', 'tiktok.com', 'reddit.com', 'amazon.', 'g2.com',
                  'crunchbase.com', 'cloudflare.com')
BOT_WALL_STATUS = {999, 401, 403, 429, 503}
CHALLENGE_HEADERS = ('cf-mitigated', 'x-amzn-waf-action', 'x-datadome', 'x-px-blocked')


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def bot_wall(url, status, headers=None):
    host = (urlsplit(url).hostname or '').lower()
    if status == 999:
        return True
    if headers is not None and any(headers.get(name) for name in CHALLENGE_HEADERS):
        return True
    return status in BOT_WALL_STATUS and any(host == h or host.endswith('.' + h) or (h.endswith('.') and h in host)
                                             for h in BOT_WALL_HOSTS)


def _request(opener, url, method, timeout):
    request = Request(url, method=method, headers={'User-Agent': USER_AGENT, 'Accept': '*/*'})
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.headers.get('location'), response.headers
    except HTTPError as error:
        headers = error.headers
        try:
            error.close()
        except Exception:  # noqa: BLE001
            pass
        return error.code, headers.get('location') if headers else None, headers


def check_url(url, timeout=10, opener=None):
    opener = opener or build_opener(_NoRedirect())
    chain, current = [], url
    for _ in range(7):
        try:
            status, location, headers = _request(opener, current, 'HEAD', timeout)
            if status in RETRY_WITH_GET or status == 999:
                status, location, headers = _request(opener, current, 'GET', timeout)
        except (URLError, TimeoutError, OSError, ValueError) as error:
            return {'url': url, 'status': None, 'error': type(error).__name__, 'redirect_chain': chain,
                    'state': 'error'}
        if bot_wall(current, status, headers):
            return {'url': url, 'status': status, 'final_url': current, 'redirect_chain': chain, 'ok': None,
                    'blocked': True, 'state': 'blocked', 'redirects': 0,
                    'note': 'automated request refused (bot wall); not evidence of a broken link'}
        if status in REDIRECTS and location:
            target = urljoin(current, location)
            chain.append({'url': current, 'status': status, 'location': target})
            current = target
            continue
        ok = status is not None and status < 400
        return {'url': url, 'status': status, 'final_url': current, 'redirect_chain': chain,
                'ok': ok, 'redirects': len(chain), 'state': 'ok' if ok else 'broken'}
    return {'url': url, 'status': None, 'error': 'redirect-limit', 'redirect_chain': chain, 'state': 'error'}


def sample_external(external, limit, counts=None):
    """Most-linked first, round-robin across hosts so one domain cannot fill the sample; ties broken
    by a stable hash (never "first N alphabetically")."""
    counts = counts or {}
    hosts = {}
    for url in sorted(set(external), key=lambda u: (-counts.get(u, 0), hashlib.sha1(u.encode()).hexdigest())):
        hosts.setdefault(urlsplit(url).hostname or '', []).append(url)
    order = sorted(hosts.values(), key=lambda urls: (-counts.get(urls[0], 0), hashlib.sha1(urls[0].encode()).hexdigest()))
    picked = []
    while len(picked) < limit and any(order):
        for urls in order:
            if urls and len(picked) < limit:
                picked.append(urls.pop(0))
    return picked


def check_links(internal, external=(), *, external_sample=50, workers=8, timeout=10, external_counts=None):
    """external may be a list (duplicates count as extra occurrences) or pass external_counts={url: n}."""
    external = [u for u in external if urlsplit(u).scheme in {'http', 'https'}]
    counts = dict(external_counts or {})
    if not counts:
        for url in external:
            counts[url] = counts.get(url, 0) + 1
    internal = sorted(set(u for u in internal if urlsplit(u).scheme in {'http', 'https'}))
    unique_external = sorted(set(external))
    sampled = sample_external(unique_external, external_sample, counts)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda u: check_url(u, timeout), internal + sampled))
    by_url = {row['url']: row for row in results}
    return {'results': by_url,
            'coverage': {'internal_total': len(internal), 'internal_checked': len(internal),
                         'external_total': len(unique_external), 'external_checked': len(sampled),
                         'blocked': sum(1 for row in results if row.get('state') == 'blocked'),
                         'method': 'HEAD, GET on 403/404/405/429/501; manual redirect chain; bot walls = blocked; '
                                   'external sample most-linked first, hash-spread across hosts; no cookies'}}
