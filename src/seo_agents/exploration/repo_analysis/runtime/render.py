"""Rendered and raw lenses for one URL.

- `fetch_raw(url)`: the initial HTTP response (no JavaScript) through the isolated
  stdlib worker (`tools/http_probe.py`): status, headers, redirect chain, timing and
  the `dom.summarize_html` summary of the served HTML.
- `render_url(url, device)`: the DOM after JavaScript, via Playwright Chromium when
  installed (`pip install playwright && python -m playwright install chromium`).
  Same summary keys as the raw lens, plus browser-only facts: rendered image sizes,
  natural sizes, above-the-fold position, transfer bytes, visibility and console errors.

When Playwright is missing the rendered lens is reported as unavailable — it is
never silently replaced by the raw lens.
"""

from __future__ import annotations

import os
import time

from seo_agents.exploration.repo_analysis.runtime.dom import finalize, summarize_html
from seo_agents.exploration.repo_analysis.tools.runtime import fetch_baseline

DEVICES = {
    'desktop': {'viewport': {'width': 1366, 'height': 900}, 'is_mobile': False, 'device_scale_factor': 1,
                'user_agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 seo-agents'},
    'mobile': {'viewport': {'width': 390, 'height': 844}, 'is_mobile': True, 'has_touch': True, 'device_scale_factor': 3,
               'user_agent': 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36 seo-agents'},
}

# Runs inside the page. Mirrors dom.py's summary keys; adds layout/browser facts.
EXTRACT_JS = r"""
() => {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const SEM = new Set(['MAIN','NAV','HEADER','FOOTER','ASIDE','ARTICLE','SECTION']);
  const CHROME = new Set(['HEADER','NAV','FOOTER','ASIDE']);
  const section = el => { for (let n = el.parentElement; n; n = n.parentElement) if (SEM.has(n.tagName)) return n.tagName.toLowerCase(); return 'body'; };
  const inMain = el => { for (let n = el.parentElement; n; n = n.parentElement) if (CHROME.has(n.tagName)) return false; return true; };
  const visible = el => { const s = getComputedStyle(el); const r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity) !== 0 && (r.width > 0 || r.height > 0); };
  const path = el => { const parts = []; for (let n = el; n && n.nodeType === 1 && parts.length < 7; n = n.parentElement) {
      let i = 1; for (let s = n.previousElementSibling; s; s = s.previousElementSibling) if (s.tagName === n.tagName) i++;
      parts.unshift(n.tagName.toLowerCase() + ':nth-of-type(' + i + ')'); } return parts.join(' > '); };
  const fold = innerHeight;
  const out = {url: location.href, lang: document.documentElement.getAttribute('lang'), title: document.title ? norm(document.title) : null,
    meta: [...document.querySelectorAll('meta')].map(m => { const o = {}; for (const k of ['name','property','content','http-equiv','charset','itemprop']) if (m.hasAttribute(k)) o[k] = m.getAttribute(k); return o; }),
    head_links: [...document.querySelectorAll('link')].map(l => { const o = {}; for (const k of ['rel','href','hreflang','type','sizes','as','media']) if (l.hasAttribute(k)) o[k] = l.getAttribute(k); return o; }),
    json_ld: [...document.querySelectorAll('script[type="application/ld+json"]')].map(s => s.textContent),
    headings: [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].map(h => ({level: +h.tagName[1], text: norm(h.innerText || h.textContent), section: section(h), path: path(h), visible: visible(h)})),
    links: [...document.querySelectorAll('a[href]')].map(a => ({href: a.getAttribute('href'), resolved: a.href, text: norm(a.innerText || a.textContent),
      aria_label: a.getAttribute('aria-label'), title: a.getAttribute('title'), rel: a.getAttribute('rel'), target: a.getAttribute('target'),
      section: section(a), in_main: inMain(a), path: path(a), visible: visible(a),
      img_alt: [...a.querySelectorAll('img[alt]')].map(i => i.getAttribute('alt')).filter(Boolean)})),
    images: [...document.querySelectorAll('img')].map(img => { const r = img.getBoundingClientRect(); return {
      src: img.getAttribute('src'), resolved: img.currentSrc || img.src, alt: img.hasAttribute('alt') ? img.getAttribute('alt') : null,
      width: img.getAttribute('width'), height: img.getAttribute('height'), loading: img.getAttribute('loading'), decoding: img.getAttribute('decoding'),
      fetchpriority: img.getAttribute('fetchpriority'), srcset: img.getAttribute('srcset'), sizes: img.getAttribute('sizes'),
      natural: [img.naturalWidth, img.naturalHeight], rendered: [Math.round(r.width), Math.round(r.height)],
      above_fold: r.top < fold && r.bottom > 0, section: section(img), in_main: inMain(img), path: path(img), visible: visible(img)}; }),
    blocks: [...document.querySelectorAll('p,li,h1,h2,h3,h4,h5,h6,blockquote,td,th,dd,dt,figcaption,summary,caption,pre,label,button')]
      .filter(el => !el.parentElement.closest('p,li,blockquote,td,th,dd,dt,figcaption,summary,caption,pre,label,button'))
      .map(el => ({tag: el.tagName.toLowerCase(), text: norm(el.innerText || el.textContent).slice(0, 2000), section: section(el), in_main: inMain(el), path: path(el), visible: visible(el)}))
      .filter(b => b.text),
    iframes: document.querySelectorAll('iframe').length, scripts: document.scripts.length,
    stylesheets: document.querySelectorAll('link[rel~="stylesheet"]').length};
  const countWords = t => (t.match(/[\p{L}\p{N}]+(?:['’][\p{L}\p{N}]+)*/gu) || []).length;
  const body = document.body ? document.body.innerText : '';
  const clone = document.body ? document.body.cloneNode(true) : null;
  if (clone) clone.querySelectorAll('header,nav,footer,aside,script,style,noscript').forEach(n => n.remove());
  out.word_count = countWords(body);
  out.main_word_count = clone ? countWords(clone.innerText || clone.textContent || '') : 0;
  out.resources = performance.getEntriesByType('resource').map(r => ({name: r.name, type: r.initiatorType, transfer: r.transferSize, encoded: r.encodedBodySize}));
  return out;
}
"""

def playwright_available():
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


def close_browsers():
    """Kept for API compatibility: every render session now closes its own browser."""
    return None


def render_devices(url, devices=('desktop', 'mobile'), *, timeout=30):
    """One Playwright session per URL (safe in any worker thread): every requested device."""
    if not playwright_available():
        return {device: {'status': 'unavailable', 'reason': 'playwright-not-installed', 'device': device} for device in devices}
    from playwright.sync_api import Error as PlaywrightError, sync_playwright
    results = {}
    try:
        with sync_playwright() as manager:
            browser = manager.chromium.launch(headless=True, args=['--no-sandbox', '--disable-dev-shm-usage'])
            try:
                for device in devices:
                    results[device] = _render_one(browser, url, device, timeout, PlaywrightError)
            finally:
                browser.close()
    except (PlaywrightError, OSError) as error:
        for device in devices:
            results.setdefault(device, {'status': 'failed', 'device': device, 'error': type(error).__name__ + ': ' + str(error)[:300]})
    return results


def render_url(url, device='desktop', *, timeout=30):
    return render_devices(url, (device,), timeout=timeout)[device]


def _render_one(browser, url, device, timeout, PlaywrightError):
    from seo_agents.exploration.repo_analysis.runtime.dom import parse_json_ld
    profile = DEVICES[device]
    begun = time.perf_counter()
    context = None
    try:
        context = browser.new_context(**profile, java_script_enabled=True)
        page = context.new_page()
        console_errors, failed = [], []
        page.on('console', lambda message: console_errors.append(message.text[:300]) if message.type == 'error' and len(console_errors) < 20 else None)
        page.on('requestfailed', lambda request: failed.append(request.url[:300]) if len(failed) < 20 else None)
        response = page.goto(url, wait_until='load', timeout=timeout * 1000)
        try:  # analytics beacons / long polling never go idle: idle is best effort
            page.wait_for_load_state('networkidle', timeout=min(8000, timeout * 1000))
        except PlaywrightError:
            pass
        page.wait_for_timeout(300)
        summary = page.evaluate(EXTRACT_JS)
        summary['json_ld'] = [parse_json_ld(raw) for raw in summary.get('json_ld', [])]
        finalize(summary)
        transfers = {r['name']: r for r in summary.pop('resources', [])}
        for image in summary['images']:
            entry = transfers.get(image.get('resolved') or '')
            if entry:
                image['transfer_bytes'] = entry.get('transfer') or entry.get('encoded') or None
        summary.update(status='success', device=device, http_status=response.status if response else None,
                       final_url=page.url, console_errors=console_errors, failed_requests=failed,
                       render_ms=round((time.perf_counter() - begun) * 1000, 1))
        return summary
    except (PlaywrightError, TimeoutError, OSError) as error:
        return {'status': 'failed', 'device': device, 'error': type(error).__name__ + ': ' + str(error)[:300]}
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:  # noqa: BLE001 - closing must not mask the result
                pass


def fetch_raw(url, *, timeout=12, max_body_bytes=2_000_000):
    """Initial HTML (no JS) with headers/redirects, summarised with the shared DOM shape."""
    result = fetch_baseline(url, timeout=timeout, max_body_bytes=max_body_bytes)
    html = result.pop('initial_html', None)
    if html is not None:
        result['summary'] = summarize_html(html, result.get('final_url') or url)
    return result


def measure_url(url, *, render=True, devices=('desktop', 'mobile'), timeout=30):
    """Every lens available for one URL; each lens reports its own status."""
    lenses = {'raw': fetch_raw(url, timeout=min(timeout, 30))}
    if render:
        lenses.update(render_devices(url, devices, timeout=timeout))
    else:
        for device in devices:
            lenses[device] = {'status': 'skipped', 'reason': 'rendering disabled (pass render=True / SEO_RENDER=1)'}
    raw = (lenses['raw'].get('summary') or {})
    desktop = lenses.get('desktop', {})
    rendering = {'raw_html_words': raw.get('main_word_count'), 'rendered_words': desktop.get('main_word_count')
                 if desktop.get('status') == 'success' else None}
    if rendering['raw_html_words'] is not None and rendering['rendered_words'] is not None:
        rendered = rendering['rendered_words'] or 0
        rendering['js_dependent'] = rendered > 0 and (rendering['raw_html_words'] or 0) < 0.5 * rendered
        rendering['raw_to_rendered_ratio'] = round((rendering['raw_html_words'] or 0) / rendered, 3) if rendered else None
        raw_titles, rendered_title = raw.get('title'), desktop.get('title')
        rendering['title_requires_js'] = bool(rendered_title) and raw_titles != rendered_title
        raw_links = {l.get('resolved') for l in raw.get('links', [])}
        rendering['links_only_after_js'] = len([l for l in desktop.get('links', []) if l.get('resolved') not in raw_links])
    else:
        rendering['js_dependent'] = None
    if lenses.get('mobile', {}).get('status') == 'success' and desktop.get('status') == 'success':
        lenses['mobile_parity'] = mobile_parity(desktop, lenses['mobile'])
    return {'url': url, 'lenses': lenses, 'rendering': rendering}


def mobile_parity(desktop, mobile):
    """Content present on desktop but missing/hidden on mobile (Google indexes mobile)."""
    def visible(rows, key):
        return {(r.get(key) or '').strip() for r in rows if r.get('visible', True) and (r.get(key) or '').strip()}
    missing_headings = sorted(visible(desktop['headings'], 'text') - visible(mobile['headings'], 'text'))[:20]
    missing_links = sorted(visible(desktop['links'], 'resolved') - visible(mobile['links'], 'resolved'))[:30]
    return {'desktop_words': desktop.get('main_word_count'), 'mobile_words': mobile.get('main_word_count'),
            'headings_missing_on_mobile': missing_headings, 'links_missing_on_mobile': missing_links,
            'viewport_meta': mobile.get('viewport'),
            'title_differs': desktop.get('title') != mobile.get('title')}


def render_enabled(explicit=None):
    if explicit is not None:
        return bool(explicit)
    return os.getenv('SEO_RENDER', '0') == '1'
