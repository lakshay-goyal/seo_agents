"""Serial per-concrete-URL HTTP baselines, with an honest coverage ledger."""

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import subprocess
import sys
from urllib.parse import urlsplit

from seo_agents.exploration.checks.agents.context import SourceHTML
from seo_agents.exploration.checks.evidence import origin


def fetch_baseline(url, *, timeout=12, max_body_bytes=512_000):
    worker = Path(__file__).parents[1] / 'agents' / 'http_probe.py'
    try:
        response = subprocess.run([sys.executable, '-I', '-B', str(worker)],
            input=json.dumps({'url': url, 'timeout': timeout, 'max_body_bytes': max_body_bytes}),
            text=True, capture_output=True, env={}, timeout=timeout + 1)
    except subprocess.TimeoutExpired:
        return {'status': 'timeout', 'error': 'Whole URL observation exceeded wall time budget'}
    if response.returncode:
        return {'status': 'failed', 'error': 'HTTP worker failed'}
    try:
        return json.loads(response.stdout)
    except ValueError:
        return {'status': 'failed', 'error': 'HTTP worker returned invalid JSON'}


def validate_runtime_config(site_url, max_pages, timeout):
    if type(max_pages) is not int or max_pages < 0 or not 0 < timeout <= 60:
        raise ValueError('max_pages must be nonnegative; timeout must be in (0, 60] seconds')
    if site_url:
        p = urlsplit(site_url)
        if p.scheme not in {'http', 'https'} or not p.hostname or p.username or p.password:
            raise ValueError('site_url must be an HTTP(S) URL without embedded credentials')
        p.port  # Validate port syntax before spending model work.


def observe_runtime(bundle, *, site_url=None, max_pages=100, timeout=12, fetcher=None):
    validate_runtime_config(site_url, max_pages, timeout)
    ledger, responses = [], []
    fetcher = fetcher or fetch_baseline
    attempts = 0
    for concrete in bundle['concrete_urls']:
        url = concrete['url']
        row = {'url': url, 'profile': 'http-initial-response', 'status': 'pending',
               'reason': 'runtime URL not supplied' if not site_url else 'request budget',
               'observation': 'unmeasured', 'lab_or_field': 'synthetic-http-baseline',
               'runs': 0, 'metrics': None}
        ledger.append(row)
        if not site_url or attempts >= max_pages:
            continue
        parsed = urlsplit(url)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password
                or origin(url) != origin(site_url)):
            row.update(status='excluded', reason='No concrete HTTP URL within the explicit authorized origin')
            continue
        attempts += 1
        observed_at = datetime.now(timezone.utc).isoformat()
        try:
            result = fetcher(url, timeout=timeout)
        except (OSError, TimeoutError, ValueError) as error:
            result = {'status': 'timeout' if isinstance(error, TimeoutError) else 'failed', 'error': type(error).__name__}
        row.update(status=result['status'], reason=None,
                   observation='initial-response' if result.get('http_status') is not None else 'request-attempt',
                   runs=1, observed_at=observed_at,
                   response_ref='/runtime/responses/' + str(len(responses)),
                   metrics={key: result.get(key) for key in ('request_to_headers_ms', 'request_to_first_body_byte_ms',
                       'bounded_body_read_ms', 'body_bytes_read')})
        response = {'requested_url': url, 'observed_at': observed_at, **result}
        if result.get('initial_html') is not None:
            parser = SourceHTML(url)
            parser.feed(result['initial_html'])
            parser.close()
            response['initial_html_evidence'] = [{**record, 'method': 'initial-response-html', 'url': url,
                'response_ref': row['response_ref']} for record in parser.records]
        responses.append(response)
        concrete['fetch'] = result['status']
    counts = Counter(row['status'] for row in ledger)
    bundle['runtime'] = {'ledger': ledger, 'responses': responses,
        'configuration': {'site_url': site_url, 'max_pages': max_pages, 'timeout_seconds': timeout,
            'concurrency': 1, 'repeats': 1, 'cache_policy': 'fresh worker, no cookies; Cache-Control: no-cache (origin/CDN cache not controlled)',
            'tool': 'Python stdlib HTTP baseline v1', 'python_version': platform.python_version(),
            'environment': 'user-supplied runtime; build mode and deployment identity unverified'},
        'limitations': ['Single-run baseline; socket/network/TLS time included. A truncated body read is not a full navigation timing.',
            'No JavaScript rendering, device/network emulation, LCP/CLS/FCP/TBT/INP, Lighthouse scores or field data.',
            'No crawl expansion or remote sitemap fetch; this measures the enumerated concrete frontier only.',
            'Redirects stay within the explicit origin. External destinations are not requested.',
            'HTTP evidence is recomputed; it is not reused indefinitely from source checkpoints.']}
    bundle['coverage']['performance'] = {'eligible_concrete_urls': len(ledger), 'attempted': attempts,
        'states': dict(counts), 'pending': counts['pending'], 'unresolved_route_families': len(bundle['unresolved_route_families']),
        'all_known_urls_attempted': bool(ledger) and not counts['pending'],
        'all_known_urls_measured': bool(ledger) and counts['success'] == len(ledger),
        'browser_performance_complete': False}
    return bundle
