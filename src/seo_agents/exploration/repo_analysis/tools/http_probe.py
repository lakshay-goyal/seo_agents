"""Stdlib HTTP baseline worker; one URL, no browser, cookies or inherited secrets."""

import json
import time
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def site_origin(url):
    p = urlsplit(url)
    return p.scheme, p.hostname, p.port or (443 if p.scheme == 'https' else 80)


def probe(config):
    url, requested = config['url'], config['url']
    opener = build_opener(NoRedirect())
    redirects = []
    begun = time.perf_counter()
    for _ in range(6):
        request = Request(url, headers={'User-Agent': 'seo-agents-http-baseline/1.0',
                                       'Cache-Control': 'no-cache', 'Accept': 'text/html'})
        try:
            response = opener.open(request, timeout=config['timeout'])
        except HTTPError as error:
            response = error
        except (URLError, TimeoutError, OSError) as error:
            return {'status': 'timeout' if isinstance(error, TimeoutError) or isinstance(getattr(error, 'reason', None), TimeoutError)
                    else 'network-error', 'error': type(error).__name__, 'redirect_chain': redirects}
        with response:
            status = response.code
            headers = {key.lower(): value for key, value in response.headers.items()
                       if key.lower() in {'content-type', 'content-language', 'x-robots-tag', 'cache-control', 'location'}}
            if status in {301, 302, 303, 307, 308}:
                location = headers.get('location')
                if not location:
                    return {'status': 'redirect-without-location', 'http_status': status, 'redirect_chain': redirects}
                target = urljoin(url, location)
                redirects.append({'url': url, 'http_status': status, 'location': location, 'target': target})
                if site_origin(target) != site_origin(requested):
                    return {'status': 'redirect-out-of-scope', 'http_status': status, 'redirect_chain': redirects, 'final_url': target}
                url = target
                continue
            header_time = (time.perf_counter() - begun) * 1000
            first_byte = response.read(1)
            first_byte_time = (time.perf_counter() - begun) * 1000 if first_byte else None
            rest = response.read(config['max_body_bytes'])
            body = first_byte + rest
            truncated = len(body) > config['max_body_bytes']
            body = body[:config['max_body_bytes']]
            content_type = headers.get('content-type', '')
            outcome = ('access-blocked' if status in {401, 403, 429} else 'http-error' if status >= 400
                       else 'non-html' if 'text/html' not in content_type and 'application/xhtml+xml' not in content_type
                       else 'success')
            return {'status': outcome, 'http_status': status, 'final_url': url,
                    'redirect_chain': redirects, 'headers': headers,
                    'request_to_headers_ms': round(header_time, 3),
                    'request_to_first_body_byte_ms': round(first_byte_time, 3) if first_byte_time is not None else None,
                    'bounded_body_read_ms': round((time.perf_counter() - begun) * 1000, 3),
                    'body_bytes_read': len(body), 'body_truncated': truncated,
                    'initial_html': body.decode('utf-8', errors='replace') if 'html' in content_type else None,
                    'encoding_note': 'UTF-8 with replacement; declared encodings are not interpreted'}
    return {'status': 'redirect-limit', 'redirect_chain': redirects}


if __name__ == '__main__':
    try:
        result = probe(json.loads(sys.stdin.read()))
    except Exception as error:
        result = {'status': 'failed', 'error': type(error).__name__}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
