"""Check 19 (deterministic): Core Web Vitals and page speed - Lighthouse/CrUX metrics, opportunities, failed SEO audits,
compression (from Lighthouse `uses-text-compression` only), response-header caching, server response time, font-display (site gap),
JS-only rendering.

Advice only (speed fixes are judgement calls in code/config), except the site-wide compression `platform_config`.
`media_technical` owns image lazy/fetchpriority changes; this check only references the LCP element.
"""

from __future__ import annotations

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.checks.check_helpers import result
from seo_agents.onpage.checks.base_check import OnpageCheck
from seo_agents.onpage.checks.security_check import headers_where
from seo_agents.onpage.checks.technical_check import platform_change

CHECK = 'performance'
# metric -> (good threshold, poor threshold, label, unit)
THRESHOLDS = {'lcp_ms': (2500, 4000, 'Largest Contentful Paint', 'ms'), 'cls': (0.1, 0.25, 'Cumulative Layout Shift', ''),
              'inp_ms': (200, 500, 'Interaction to Next Paint', 'ms'), 'tbt_ms': (200, 600, 'Total Blocking Time', 'ms'),
              'ttfb_ms': (800, 1800, 'Time to First Byte', 'ms'), 'fcp_ms': (1800, 3000, 'First Contentful Paint', 'ms')}
# CrUX / PSI field metric names -> our keys (PSI reports CLS x100)
FIELD_NAMES = {'largest_contentful_paint': 'lcp_ms', 'largest_contentful_paint_ms': 'lcp_ms',
               'cumulative_layout_shift': 'cls', 'cumulative_layout_shift_score': 'cls',
               'interaction_to_next_paint': 'inp_ms', 'interaction_to_next_paint_ms': 'inp_ms',
               'experimental_time_to_first_byte': 'ttfb_ms', 'first_contentful_paint': 'fcp_ms', 'first_contentful_paint_ms': 'fcp_ms'}
METRIC_FIX = {'lcp_ms': 'serve the LCP element in the initial HTML, preload it, compress/resize it and cut render-blocking CSS/JS',
              'cls': 'reserve space for images/embeds/ads (width/height or aspect-ratio) and avoid injecting content above existing content',
              'inp_ms': 'break up long tasks, defer non-critical JS and keep event handlers light',
              'tbt_ms': 'split and defer JavaScript; remove unused libraries and heavy third-party scripts',
              'ttfb_ms': 'cache HTML at the CDN edge (static generation), reduce server work, avoid redirects',
              'fcp_ms': 'inline critical CSS, defer the rest, and reduce render-blocking resources'}
# Lighthouse 10-12 audits whose details.type is 'opportunity' (lighthouse_audit stores only those; diagnostics such as
# uses-long-cache-ttl, font-display, dom-size or third-party-summary never reach `opportunities`).
OPPORTUNITY_ADVICE = {
    'render-blocking-resources': 'Inline critical CSS and defer/async non-critical CSS and scripts in <head>',
    'unused-javascript': 'Code-split by route and drop unused JS (dynamic import(), tree-shaking)',
    'unused-css-rules': 'Remove unused CSS (purge utility classes, split CSS per route)',
    'unminified-javascript': 'Minify JavaScript in the production build',
    'unminified-css': 'Minify CSS in the production build',
    'uses-text-compression': 'Enable gzip or Brotli for HTML/CSS/JS/SVG on the server/CDN',
    'uses-responsive-images': 'Serve sized images with srcset/sizes instead of one large file',
    'offscreen-images': 'Lazy-load below-the-fold images (loading="lazy")',
    'modern-image-formats': 'Serve WebP/AVIF images (with fallbacks)',
    'uses-optimized-images': 'Compress images (e.g. quality 75-85 JPEG/WebP) in the build or image CDN',
    'efficient-animated-content': 'Replace animated GIFs with MP4/WebM video',
    'prioritize-lcp-image': 'Preload the LCP image / give it fetchpriority="high" and do not lazy-load it',
    'server-response-time': 'Reduce server response time: cache HTML at the edge or pre-render the route',
    'redirects': 'Avoid multiple page redirects; link straight to the final URL',
    'legacy-javascript': 'Stop shipping legacy polyfills/transpiled code to modern browsers (modern browserslist / module build)',
    'duplicated-javascript': 'Deduplicate JavaScript modules bundled more than once',
    'uses-rel-preconnect': 'Add <link rel="preconnect"> for critical third-party origins (fonts, CDN, API)',
}
SEO_AUDITS = {'document-title': 'missing <title>', 'meta-description': 'missing meta description', 'link-text': 'non-descriptive link text',
              'crawlable-anchors': 'links without a crawlable href', 'is-crawlable': 'page is blocked from indexing',
              'robots-txt': 'robots.txt is invalid', 'image-alt': 'images without alt text', 'hreflang': 'invalid hreflang',
              'canonical': 'invalid rel=canonical', 'tap-targets': 'tap targets too small/close',
              'font-size': 'text too small to read on mobile', 'http-status-code': 'unsuccessful HTTP status code',
              'structured-data': 'invalid structured data', 'plugins': 'browser plugins (Flash/Java) used'}


def lab_of(perf):
    """(form factor, lab dict) preferring mobile (mobile-first indexing), then desktop."""
    for factor in ('mobile', 'desktop'):
        lab = perf.get(factor)
        if isinstance(lab, dict) and lab.get('status') != 'failed' and (lab.get('metrics') or lab.get('opportunities')):
            return factor, lab
    if isinstance(perf.get('metrics'), dict):
        return 'lab', perf
    return None, {}


def field_metrics(perf, lab):
    """Real-user p75 metrics (CrUX top-level field_data or PSI loadingExperience in the lab block) -> {key: value}."""
    field = perf.get('field_data') if isinstance(perf.get('field_data'), dict) else lab.get('field_data')
    if not isinstance(field, dict):
        return {}, None
    out = {}
    metrics = field.get('metrics') if isinstance(field.get('metrics'), dict) else {}
    for name, metric in metrics.items():
        key = FIELD_NAMES.get(str(name).lower())
        raw = metric.get('p75') if isinstance(metric, dict) else metric
        try:
            number = float(raw)
        except (TypeError, ValueError):
            continue
        if str(name).lower() == 'cumulative_layout_shift_score':
            number = number / 100  # PSI loadingExperience always reports CLS x100; CrUX / lab CLS is unitless
        if key:
            out[key] = number
    source = field.get('source') or ('PageSpeed Insights field data' if 'origin_fallback' in field else 'CrUX')
    scope = field.get('scope') or ('origin' if field.get('origin_fallback') else 'url')
    return out, f'{source} p75 ({scope})'


def metric_findings(ctx, perf, advice, data):
    factor, lab = lab_of(perf)
    lab_metrics = lab.get('metrics') if isinstance(lab.get('metrics'), dict) else {}
    field, field_source = field_metrics(perf, lab)
    data.update(form_factor=factor, metrics=lab_metrics, field_metrics=field or None)
    lcp_element = lab.get('lcp_element') if isinstance(lab.get('lcp_element'), dict) else None
    for key, (good, poor, label, unit) in THRESHOLDS.items():
        source, measured = (field_source, field.get(key)) if field.get(key) is not None else (f'lab ({factor})', lab_metrics.get(key))
        if not isinstance(measured, (int, float)) or measured <= good:
            continue
        impact = 'high' if measured > poor else 'medium'
        shown = f'{measured:.2f}' if key == 'cls' else f'{round(measured)}{unit}'
        evidence = {'metric': key, 'value': measured, 'good': good, 'poor': poor, 'source': source}
        if key == 'lcp_ms' and lcp_element:
            evidence['lcp_element'] = lcp_element
        advice.append(ops.advice(ctx, CHECK, f'performance.{key.split("_")[0]}', f'{label} is {shown} (good <= {good}{unit}, '
                                 f'{source}): {METRIC_FIX[key]}.', impact=impact, evidence=evidence))
    return lab


# Image findings are owned by media_technical (images.fetchpriority / images.loading / images.weight / srcset): not repeated here.
MEDIA_OWNED = {'prioritize-lcp-image', 'modern-image-formats', 'uses-responsive-images', 'uses-optimized-images', 'offscreen-images'}


def number(raw):
    try:
        return float(raw or 0)
    except (TypeError, ValueError):
        return 0.0


def opportunity_findings(ctx, lab, advice):
    for item in lab.get('opportunities') or []:
        if not isinstance(item, dict) or item.get('id') not in OPPORTUNITY_ADVICE or item.get('id') in MEDIA_OWNED:
            continue
        savings_ms, savings_bytes = number(item.get('savings_ms')), number(item.get('savings_bytes'))
        impact = 'high' if savings_ms >= 1000 else 'medium' if savings_ms >= 300 or savings_bytes >= 100_000 else 'low'
        text = OPPORTUNITY_ADVICE[item['id']]
        if item['id'] == 'uses-text-compression':
            continue  # compression_findings (site change / local advice)
        if item['id'] == 'prioritize-lcp-image' and lab.get('lcp_element'):
            text += f" (LCP element: {(lab['lcp_element'] or {}).get('selector') or (lab['lcp_element'] or {}).get('snippet')})"
        advice.append(ops.advice(ctx, CHECK, 'performance.' + item['id'], text + '.', impact=impact,
                                 evidence={'savings_ms': savings_ms, 'savings_bytes': savings_bytes,
                                           'items': [i.get('url') for i in item.get('items') or [] if isinstance(i, dict)][:5]}))
    for audit in lab.get('failed_seo_audits') or []:
        if isinstance(audit, str):
            advice.append(ops.advice(ctx, CHECK, 'performance.seo_audit.' + audit, f'Lighthouse SEO audit failed: '
                                     f'{SEO_AUDITS.get(audit, audit)} (`{audit}`).', impact='low', evidence={'audit': audit}))


def compression_findings(ctx, perf, advice, site_changes):
    """Compression only from Lighthouse `uses-text-compression` (any form factor): the HTTP probe sends no Accept-Encoding,
    so a missing Content-Encoding there proves nothing."""
    found = None
    for factor in ('mobile', 'desktop', None):
        lab = perf.get(factor) if factor else perf
        if not isinstance(lab, dict):
            continue
        found = next((o for o in lab.get('opportunities') or [] if isinstance(o, dict) and o.get('id') == 'uses-text-compression'), None)
        if found:
            break
    if not found:
        return
    evidence = {'savings_ms': found.get('savings_ms'), 'savings_bytes': found.get('savings_bytes'),
                'items': [i.get('url') for i in found.get('items') or [] if isinstance(i, dict)][:5]}
    hosting = ctx.get('hosting')
    if ctx.get('origin_is_local'):
        advice.append(ops.advice(ctx, CHECK, 'performance.compression', 'Lighthouse found uncompressed text resources on the local '
                                 'server; verify production serves Brotli or gzip.', impact='low', evidence=evidence))
        return
    site_changes.append(platform_change(
        'platform.compression', evidence, 'Content-Encoding: br (or gzip) for HTML, CSS, JS, SVG, JSON',
        where=f'{hosting or "server/CDN"}: enable Brotli/gzip compression ({headers_where(hosting)})', hosting=hosting,
        change_id='chg_compression', impact='medium',
        rationale='Lighthouse `uses-text-compression`: uncompressed HTML/CSS/JS is typically 3-5x larger and delays first paint'))


def header_findings(ctx, advice):
    http = ctx.get('http') or {}
    headers = http.get('headers') if isinstance(http.get('headers'), dict) else {}
    headers = {str(k).lower(): v for k, v in headers.items()}
    if headers and 'html' in str(headers.get('content-type') or 'text/html').lower():
        cache = str(headers.get('cache-control') or '').lower()
        if not ctx.get('origin_is_local') and (not cache or 'no-store' in cache):
            advice.append(ops.advice(ctx, CHECK, 'performance.cache_control', 'HTML has no Cache-Control (or no-store); allow CDN '
                                     'caching, e.g. `public, max-age=0, s-maxage=300, stale-while-revalidate=86400`.', impact='low',
                                     evidence={'cache-control': headers.get('cache-control')}))
    ttfb = http.get('request_to_headers_ms')
    redirected = bool(http.get('redirect_chain'))  # the timing then includes redirect hops
    if isinstance(ttfb, (int, float)) and ttfb > 800 and not ctx.get('origin_is_local') and not redirected:
        advice.append(ops.advice(ctx, CHECK, 'performance.server_response', f'The server took {round(ttfb)}ms to send headers '
                                 '(good <= 800ms): cache the HTML at the edge or pre-render the route.',
                                 impact='high' if ttfb > 1800 else 'medium', evidence={'request_to_headers_ms': ttfb}))


def check_performance(ctx, prior=None, writer=None):
    advice, site_changes, data = [], [], {}
    perf = ctx.get('performance') if isinstance(ctx.get('performance'), dict) else {}
    lab = metric_findings(ctx, perf, advice, data) if perf else {}
    data['measured'] = bool(lab)
    if lab:
        opportunity_findings(ctx, lab, advice)
    else:
        data['reason'] = perf.get('reason') if perf else 'no performance measurement in the exploration output'
    compression_findings(ctx, perf, advice, site_changes)
    header_findings(ctx, advice)
    gaps = (ctx.get('site_gaps') or {}).get('performance_web_vitals') or []
    if 'no-font-display' in gaps and ctx.get('site_items_owner', ctx.get('is_home')):  # site-wide: once
        advice.append(ops.advice(ctx, CHECK, 'performance.font-display', 'Web fonts have no font-display; add `font-display: swap` '
                                 '(or `optional`) to each @font-face so text is visible while fonts load.', impact='low'))
    rendering = ctx.get('rendering') or {}
    rendered, raw = rendering.get('rendered_words') or ctx.get('main_words'), rendering.get('raw_html_words')
    if raw is None and 'CSR' in ((ctx.get('raw_source') or {}).get('rendering_modes') or []):  # same fallback as technical
        raw = (ctx.get('raw_source') or {}).get('word_count')
    prerender_owned = ctx.get('head_scope') == 'shared-shell' or (isinstance(rendered, (int, float)) and isinstance(raw, (int, float))
                                                                 and rendered and raw < 0.5 * rendered)
    if (rendering.get('title_requires_js') or rendering.get('links_only_after_js')) and not prerender_owned:  # else technical.prerender
        what = [w for w, on in (('the <title>', rendering.get('title_requires_js')),
                                ('the internal links', rendering.get('links_only_after_js'))) if on]
        advice.append(ops.advice(ctx, CHECK, 'performance.js_dependency', f'{" and ".join(what).capitalize()} only exist after '
                                 'JavaScript runs; crawlers that do not execute JS (most AI crawlers, social previews) miss them and '
                                 'Google defers them to the render queue. Put them in the initial HTML (SSR/prerender).',
                                 impact='high', evidence={k: rendering.get(k) for k in ('title_requires_js', 'links_only_after_js')}))
    return result(data=data, advice=advice, site_changes=site_changes)


class PerformanceCheck(OnpageCheck):
    """Check: Core Web Vitals (field > lab), Lighthouse opportunities and SEO audits, compression, caching, TTFB (deterministic)."""

    name = 'performance'
    skill = 'performance'
    uses_model = False
    depends_on = ()

    def run(self, ctx, prior, writer):
        return check_performance(ctx, prior or {}, writer)
