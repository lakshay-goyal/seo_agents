"""Core Web Vitals per URL: Lighthouse CLI (lab) or PageSpeed Insights API (lab + CrUX field).

Selection order:
1. `lighthouse` CLI on PATH (or `npx lighthouse` when SEO_LIGHTHOUSE=npx): mobile + desktop,
   SEO_LIGHTHOUSE_RUNS runs each (default 3), median by LCP;
2. PAGESPEED_API_KEY set and the URL is public: PSI v5 (also returns CrUX field data);
3. otherwise `unavailable` with the reason — never a fabricated number.
"""

from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess
import tempfile
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

AUDITS = {
    'lcp_ms': 'largest-contentful-paint', 'fcp_ms': 'first-contentful-paint', 'cls': 'cumulative-layout-shift',
    'tbt_ms': 'total-blocking-time', 'si_ms': 'speed-index', 'tti_ms': 'interactive', 'ttfb_ms': 'server-response-time',
    'inp_ms': 'interaction-to-next-paint',
}


def lighthouse_command():
    mode = os.getenv('SEO_LIGHTHOUSE', 'auto')
    if mode == '0':
        return None
    if shutil.which('lighthouse'):
        return ['lighthouse']
    if mode == 'npx' and shutil.which('npx'):
        return ['npx', '--yes', 'lighthouse@12']
    return None


def parse_report(report):
    audits = report.get('audits', {})
    metrics = {}
    for key, audit_id in AUDITS.items():
        value = audits.get(audit_id, {}).get('numericValue')
        if value is not None:
            metrics[key] = round(value, 4 if key == 'cls' else 1)
    categories = {name: (cat or {}).get('score') for name, cat in (report.get('categories') or {}).items()}
    lcp_element = None
    items = ((audits.get('largest-contentful-paint-element') or {}).get('details') or {}).get('items') or []
    for item in items:
        node = item.get('node') or next((row.get('node') for row in (item.get('items') or []) if row.get('node')), None)
        if node:
            lcp_element = {'selector': node.get('selector'), 'snippet': (node.get('snippet') or '')[:300],
                           'label': node.get('nodeLabel')}
            break
    opportunities = []
    for audit_id, audit in audits.items():
        details = audit.get('details') or {}
        if details.get('type') == 'opportunity' and (audit.get('score') is not None and audit['score'] < 0.9):
            opportunities.append({'id': audit_id, 'savings_ms': round(details.get('overallSavingsMs') or 0),
                                  'savings_bytes': details.get('overallSavingsBytes'),
                                  'items': [{k: row.get(k) for k in ('url', 'wastedBytes', 'wastedMs', 'totalBytes') if k in row}
                                            for row in (details.get('items') or [])[:5]]})
    opportunities.sort(key=lambda o: -(o['savings_ms'] or 0))
    failed_seo = [audit_id for audit_id, audit in audits.items()
                  if audit.get('score') == 0 and audit_id in {ref.get('id') for ref in
                      ((report.get('categories') or {}).get('seo') or {}).get('auditRefs', [])}]
    return {'metrics': metrics, 'scores': categories, 'lcp_element': lcp_element,
            'opportunities': opportunities[:12], 'failed_seo_audits': failed_seo,
            'tool': 'lighthouse ' + str(report.get('lighthouseVersion') or '')}


def chrome_flags():
    flags = '--headless=new --disable-dev-shm-usage'
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        flags += ' --no-sandbox'  # only needed when Chromium runs as root (sandbox container)
    return '--chrome-flags=' + flags


def run_cli(url, form_factor, runs, timeout):
    """`runs` Lighthouse runs; a failed run no longer discards the successful ones.
    status: success (all runs), partial (some), failed (none, with the last error)."""
    command = lighthouse_command()
    results, errors = [], []
    for _ in range(runs):
        with tempfile.NamedTemporaryFile(suffix='.json') as output:
            args = command + [url, '--output=json', '--output-path=' + output.name, '--quiet',
                              '--only-categories=performance,seo,accessibility,best-practices', chrome_flags()]
            if form_factor == 'desktop':
                args.append('--preset=desktop')
            try:
                subprocess.run(args, capture_output=True, timeout=timeout, check=True)
                with open(output.name, encoding='utf-8') as stream:
                    results.append(parse_report(json.load(stream)))
            except (subprocess.SubprocessError, OSError, ValueError) as error:
                errors.append(type(error).__name__ + ': ' + str(error)[:200])
    if not results:
        return {'status': 'failed', 'error': errors[-1] if errors else 'no runs', 'runs': 0, 'failed_runs': len(errors),
                'metrics': {}}
    lcps = [r['metrics'].get('lcp_ms') for r in results if r['metrics'].get('lcp_ms') is not None]
    median = statistics.median(lcps) if lcps else None
    chosen = dict(min(results, key=lambda r: abs((r['metrics'].get('lcp_ms') or 0) - (median or 0))))
    chosen['runs'] = len(results)
    chosen['failed_runs'] = len(errors)
    chosen['lcp_ms_runs'] = lcps
    chosen['status'] = 'success' if not errors else 'partial'
    if errors:
        chosen['errors'] = errors[-3:]
    chosen['method'] = 'lab: lighthouse CLI median-by-LCP of ' + str(len(results)) + ' successful runs'
    return chosen


def run_psi(url, strategy, key, timeout):
    endpoint = ('https://www.googleapis.com/pagespeedonline/v5/runPagespeed?url=' + quote(url, safe='') +
                '&strategy=' + strategy + '&category=PERFORMANCE&category=SEO&category=ACCESSIBILITY&category=BEST_PRACTICES&key=' + quote(key))
    try:
        with urlopen(Request(endpoint, headers={'User-Agent': 'seo-agents'}), timeout=timeout) as response:
            payload = json.loads(response.read())
    except (OSError, ValueError) as error:
        return {'status': 'failed', 'error': type(error).__name__}
    result = parse_report(payload.get('lighthouseResult') or {})
    field = {}
    for name, metric in ((payload.get('loadingExperience') or {}).get('metrics') or {}).items():
        field[name.lower()] = {'p75': metric.get('percentile'), 'category': metric.get('category')}
    result.update(status='success', runs=1, method='lab: PageSpeed Insights (single run)',
                  field_data={'origin_fallback': (payload.get('loadingExperience') or {}).get('origin_fallback'),
                              'metrics': field} if field else None)
    return result


def fetch_crux(url, key, timeout=20):
    """CrUX field data (p75 per metric) for the URL, falling back to its origin. None when absent."""
    endpoint = 'https://chromeuxreport.googleapis.com/v1/records:queryRecord?key=' + quote(key)
    parts = urlsplit(url)
    for scope, body in (('url', {'url': url}), ('origin', {'origin': parts.scheme + '://' + parts.netloc})):
        request = Request(endpoint, data=json.dumps({**body, 'formFactor': 'PHONE'}).encode(),
                          headers={'Content-Type': 'application/json', 'User-Agent': 'seo-agents'}, method='POST')
        try:
            with urlopen(request, timeout=timeout) as response:
                record = json.loads(response.read()).get('record') or {}
        except (OSError, ValueError):
            continue
        metrics = {name: {'p75': (metric.get('percentiles') or {}).get('p75')}
                   for name, metric in (record.get('metrics') or {}).items()}
        if metrics:
            return {'scope': scope, 'form_factor': 'PHONE', 'metrics': metrics, 'source': 'CrUX API'}
    return None


def public_url(url):
    host = (urlsplit(url).hostname or '').lower()
    return bool(host) and host not in {'localhost', '127.0.0.1', '0.0.0.0', '::1'} and not host.endswith('.local')


def measure_performance(url, *, runs=None, timeout=180):
    runs = runs or int(os.getenv('SEO_LIGHTHOUSE_RUNS', '3'))
    key = os.getenv('PAGESPEED_API_KEY')
    if lighthouse_command():
        result = {'mobile': run_cli(url, 'mobile', runs, timeout), 'desktop': run_cli(url, 'desktop', runs, timeout)}
        if key and public_url(url):
            result['field_data'] = fetch_crux(url, key)  # lab numbers alone miss real-user CWV
        result['status'] = overall_status(result)
        return result
    if key and public_url(url):
        result = {'mobile': run_psi(url, 'mobile', key, timeout), 'desktop': run_psi(url, 'desktop', key, timeout)}
        result['status'] = overall_status(result)
        return result
    reason = ('Lighthouse CLI not installed (npm i -g lighthouse) and PAGESPEED_API_KEY not set'
              if not key else 'PageSpeed Insights needs a public URL; Lighthouse CLI not installed')
    return {'status': 'unavailable', 'reason': reason}


def overall_status(result):
    """One status for a {mobile, desktop} result so ledgers never say "measured" for a failed run."""
    if not isinstance(result, dict):
        return 'failed'
    if 'status' in result and 'mobile' not in result:
        return result['status']
    states = {(result.get(k) or {}).get('status') for k in ('mobile', 'desktop')}
    if states == {'success'}:
        return 'success'
    if 'success' in states or 'partial' in states:
        return 'partial'
    return 'failed'


def measure_many(urls, *, runs=None, timeout=180, concurrency=None):
    """{url: result} for several URLs with a small pool (SEO_LIGHTHOUSE_CONCURRENCY, default 2).
    Each result also carries `status` (success | partial | failed | unavailable)."""
    from concurrent.futures import ThreadPoolExecutor
    concurrency = max(1, concurrency or int(os.getenv('SEO_LIGHTHOUSE_CONCURRENCY', '2')))
    urls = list(dict.fromkeys(urls))

    def one(url):
        result = measure_performance(url, runs=runs, timeout=timeout)
        result.setdefault('status', overall_status(result))
        return url, result
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        return dict(pool.map(one, urls))


def performance_available(url):
    return bool(lighthouse_command()) or bool(os.getenv('PAGESPEED_API_KEY') and public_url(url))


class LighthouseAuditor:
    """Tool: Lighthouse CLI (median of N runs) or PageSpeed Insights with CrUX field data."""

    name = 'lighthouse_audit'

    def available(self, url):
        return performance_available(url)

    def measure(self, url, *, runs=None, timeout=180):
        return measure_performance(url, runs=runs, timeout=timeout)

    def measure_many(self, urls, *, runs=None, timeout=180, concurrency=None):
        return measure_many(urls, runs=runs, timeout=timeout, concurrency=concurrency)
