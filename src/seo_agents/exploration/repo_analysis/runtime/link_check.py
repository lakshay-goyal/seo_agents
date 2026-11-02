"""Link status checks: every internal destination, a bounded sample of external ones.

HEAD first, GET on 405/501; redirects followed manually (max 6) so the chain is kept.
Parallel with a small thread pool; no cookies, no credentials, bounded timeouts.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

USER_AGENT = 'Mozilla/5.0 (compatible; seo-agents-linkcheck/1.0)'


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check_url(url, timeout=10):
    opener = build_opener(_NoRedirect())
    chain, current = [], url
    for _ in range(7):
        status = None
        for method in ('HEAD', 'GET'):
            request = Request(current, method=method, headers={'User-Agent': USER_AGENT, 'Accept': '*/*'})
            try:
                with opener.open(request, timeout=timeout) as response:
                    status, location = response.status, response.headers.get('location')
            except HTTPError as error:
                status, location = error.code, error.headers.get('location') if error.headers else None
            except (URLError, TimeoutError, OSError, ValueError) as error:
                return {'url': url, 'status': None, 'error': type(error).__name__, 'redirect_chain': chain}
            if status not in {405, 501}:
                break
        if status in {301, 302, 303, 307, 308} and location:
            target = urljoin(current, location)
            chain.append({'url': current, 'status': status, 'location': target})
            current = target
            continue
        return {'url': url, 'status': status, 'final_url': current, 'redirect_chain': chain,
                'ok': status is not None and status < 400, 'redirects': len(chain)}
    return {'url': url, 'status': None, 'error': 'redirect-limit', 'redirect_chain': chain}


def check_links(internal, external=(), *, external_sample=50, workers=8, timeout=10):
    internal = sorted(set(u for u in internal if urlsplit(u).scheme in {'http', 'https'}))
    external = sorted(set(u for u in external if urlsplit(u).scheme in {'http', 'https'}))
    sampled = external[:external_sample]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda u: check_url(u, timeout), internal + sampled))
    by_url = {row['url']: row for row in results}
    return {'results': by_url,
            'coverage': {'internal_total': len(internal), 'internal_checked': len(internal),
                         'external_total': len(external), 'external_checked': len(sampled),
                         'method': 'HEAD then GET; manual redirect chain; no cookies'}}
