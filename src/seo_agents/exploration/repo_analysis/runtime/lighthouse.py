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


def run_cli(url, form_factor, runs, timeout):
    command = lighthouse_command()
    results = []
    for _ in range(runs):
        with tempfile.NamedTemporaryFile(suffix='.json') as output:
            args = command + [url, '--output=json', '--output-path=' + output.name, '--quiet',
                              '--only-categories=performance,seo,accessibility,best-practices',
                              '--chrome-flags=--headless=new --no-sandbox --disable-dev-shm-usage']
            if form_factor == 'desktop':
                args.append('--preset=desktop')
            try:
                subprocess.run(args, capture_output=True, timeout=timeout, check=True)
                with open(output.name, encoding='utf-8') as stream:
                    results.append(parse_report(json.load(stream)))
            except (subprocess.SubprocessError, OSError, ValueError) as error:
                return {'status': 'failed', 'error': type(error).__name__ + ': ' + str(error)[:200], 'runs': len(results)}
    lcps = [r['metrics'].get('lcp_ms') for r in results if r['metrics'].get('lcp_ms') is not None]
    median = statistics.median(lcps) if lcps else None
    chosen = min(results, key=lambda r: abs((r['metrics'].get('lcp_ms') or 0) - (median or 0))) if results else {}
    chosen = dict(chosen)
    chosen['runs'] = len(results)
    chosen['lcp_ms_runs'] = lcps
    chosen['status'] = 'success'
    chosen['method'] = 'lab: lighthouse CLI median-by-LCP of ' + str(len(results)) + ' runs'
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


def public_url(url):
    host = (urlsplit(url).hostname or '').lower()
    return bool(host) and host not in {'localhost', '127.0.0.1', '0.0.0.0', '::1'} and not host.endswith('.local')


def measure_performance(url, *, runs=None, timeout=180):
    runs = runs or int(os.getenv('SEO_LIGHTHOUSE_RUNS', '3'))
    key = os.getenv('PAGESPEED_API_KEY')
    if lighthouse_command():
        return {'mobile': run_cli(url, 'mobile', runs, timeout), 'desktop': run_cli(url, 'desktop', runs, timeout)}
    if key and public_url(url):
        return {'mobile': run_psi(url, 'mobile', key, timeout), 'desktop': run_psi(url, 'desktop', key, timeout)}
    reason = ('Lighthouse CLI not installed (npm i -g lighthouse) and PAGESPEED_API_KEY not set'
              if not key else 'PageSpeed Insights needs a public URL; Lighthouse CLI not installed')
    return {'status': 'unavailable', 'reason': reason}


def performance_available(url):
    return bool(lighthouse_command()) or bool(os.getenv('PAGESPEED_API_KEY') and public_url(url))
