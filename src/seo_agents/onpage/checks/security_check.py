"""Check 18 (deterministic): HTTPS, http->https redirect, HSTS, security headers, mixed content and insecure forms.

HTTPS is a (light) ranking signal and a trust signal; the other headers are trust/hygiene only. Site-wide fixes are
`platform_config` site changes; on a local/dev origin the served probes describe a dev server, so they become advice.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.changes.head_recipes import REDIRECT_WHERE
from seo_agents.onpage.checks.check_helpers import result
from seo_agents.onpage.checks.base_check import OnpageCheck
from seo_agents.onpage.checks.technical_check import platform_change

CHECK = 'security'
HSTS_VALUE = 'Strict-Transport-Security: max-age=31536000; includeSubDomains'
HSTS_MIN_AGE = 15768000  # 6 months
HEADERS_WHERE = {
    'vercel': 'vercel.json "headers": [{"source": "/(.*)", "headers": [{"key": ..., "value": ...}]}]',
    'netlify': 'netlify.toml [[headers]] for = "/*" [headers.values] (or a _headers file in the publish directory)',
    'cloudflare': 'Cloudflare Pages _headers file, or Rules > Transform Rules > Modify Response Header',
    'github-pages': 'not configurable on GitHub Pages; put Cloudflare (or another CDN) in front and set the header there',
    'firebase': 'firebase.json hosting.headers [{"source": "**", "headers": [{"key": ..., "value": ...}]}]',
    'nginx': 'nginx server block: add_header <Name> "<value>" always;',
    'apache': '.htaccess: Header always set <Name> "<value>"',
}
SECURITY_HEADERS = (('x-content-type-options', 'X-Content-Type-Options: nosniff'),
                    ('referrer-policy', 'Referrer-Policy: strict-origin-when-cross-origin'),
                    ('content-security-policy', 'Content-Security-Policy (start with Content-Security-Policy-Report-Only)'))


def headers_where(hosting):
    return HEADERS_WHERE.get(str(hosting or '').lower(), 'hosting / CDN / web server response-header configuration')


def page_headers(ctx):
    headers = (ctx.get('http') or {}).get('headers')
    return {str(k).lower(): v for k, v in headers.items()} if isinstance(headers, dict) else {}


def served_header(ctx, name):
    """Response header from this page's fetch, else from the site home probe."""
    found = page_headers(ctx).get(name)
    if found:
        return found
    served = ctx.get('site_served') or {}
    probe = served.get('security_headers') if isinstance(served.get('security_headers'), dict) else {}
    found = probe.get(name)
    if found:
        return found
    home = served.get('home_headers') if isinstance(served.get('home_headers'), dict) else {}
    return {str(k).lower(): v for k, v in home.items()}.get(name)


def host_of(url):
    try:
        return (urlsplit(url).hostname or '').lower() if isinstance(url, str) else ''
    except ValueError:
        return ''


def bare(host):
    return re.sub(r'^www\.', '', host or '')


def site_or_advice(ctx, local, site_changes, advice, topic, text, change):
    """Site change on a real origin; advice on a local/unknown origin (the probe describes a dev server)."""
    if local:
        advice.append(ops.advice(ctx, CHECK, topic, text + ' (measured on a local/unknown origin: verify on production).',
                                 impact='low', evidence={'origin': ctx.get('origin')}))
    else:
        site_changes.append(change)


def transport_findings(ctx, local, https, advice, site_changes):
    hosting = ctx.get('hosting')
    served = ctx.get('site_served') or {}
    gaps = (ctx.get('site_gaps') or {}).get('https_security_headers') or []
    if not https:
        site_or_advice(ctx, local, site_changes, advice, 'security.https', 'The site is served over http://; serve it over HTTPS',
                       platform_change('platform.https', ctx.get('origin'), 'Serve every URL over HTTPS with a valid certificate',
                                       where=f'{hosting or "hosting"}: enable HTTPS / a managed certificate for the domain',
                                       hosting=hosting, change_id='chg_https', impact='high',
                                       rationale='HTTPS is a ranking signal; browsers mark http pages "Not secure"'))
        return
    hsts_fact = served.get('hsts') if isinstance(served.get('hsts'), dict) else {}
    home_ok = bool(page_headers(ctx)) or hsts_fact.get('status') == 'measured'  # the home fetch answered
    redirect = served.get('http_to_https') if isinstance(served.get('http_to_https'), dict) else None
    measured = bool(redirect) and redirect.get('status') != 'failed' and \
        (redirect.get('status') == 'measured' or redirect.get('value') is not None)
    if measured and home_ok:
        missing = not redirect.get('value')
        temporary = bool(redirect.get('value')) and redirect.get('permanent') is False
        if missing or temporary:
            site_or_advice(ctx, local, site_changes, advice, 'security.http_to_https',
                           'http:// must redirect permanently (301/308) to https://',
                           platform_change('platform.http_to_https',
                                           {'redirects': bool(redirect.get('value')), 'http_status': redirect.get('http_status')},
                                           'http://<host>/* -> 301/308 -> https://<host>/*',
                                           where=REDIRECT_WHERE.get(hosting, 'hosting / web server redirect rules (301 or 308)'),
                                           hosting=hosting, change_id='chg_http_to_https', impact='high',
                                           rationale='Without a permanent http->https redirect both versions can be crawled and '
                                                     'signals split between them'))
    hsts = served_header(ctx, 'strict-transport-security') or hsts_fact.get('value')
    hsts_measured = bool(page_headers(ctx)) or (hsts_fact.get('status') == 'measured')
    # The repo-analysis 'no-hsts' gap is only a fallback when no response headers were measured, and never on a local origin.
    gap_only = not hsts_measured and 'no-hsts' in gaps and not ctx.get('origin_is_local')
    if not hsts and (hsts_measured or gap_only):
        site_or_advice(ctx, local, site_changes, advice, 'security.hsts', 'Send a Strict-Transport-Security header',
                       platform_change('platform.hsts', None, HSTS_VALUE, where=headers_where(hosting), hosting=hosting,
                                       change_id='chg_hsts', impact='medium',
                                       rationale='HSTS makes browsers use HTTPS directly (no http hop) and blocks downgrade attacks'))
    elif hsts:
        match = re.search(r'max-age\s*=\s*"?(\d+)', str(hsts), re.I)
        if match and int(match.group(1)) < HSTS_MIN_AGE:
            advice.append(ops.advice(ctx, CHECK, 'security.hsts_max_age', f'HSTS max-age is {match.group(1)}s; use at least 6 months '
                                     f'(15768000), ideally `{HSTS_VALUE.split(": ", 1)[1]}`.', impact='low', evidence={'hsts': hsts}))


def header_findings(ctx, local, advice):
    if local:
        return
    probe = (ctx.get('site_served') or {}).get('security_headers')
    if not page_headers(ctx) and not (isinstance(probe, dict) and any(probe.values())):
        return  # nothing measured (the probe stores None for every header when the home fetch failed)
    missing = [label for name, label in SECURITY_HEADERS if not served_header(ctx, name)]
    csp = str(served_header(ctx, 'content-security-policy') or '')
    if not served_header(ctx, 'x-frame-options') and 'frame-ancestors' not in csp.lower():
        missing.append("X-Frame-Options: SAMEORIGIN (or CSP frame-ancestors 'self')")
    if missing and ctx.get('site_items_owner', ctx.get('is_home')):  # site-wide headers: reported once
        advice.append(ops.advice(ctx, CHECK, 'security.headers', 'Missing security headers (trust/hygiene, not a ranking factor): '
                                 + '; '.join(missing) + f'. Set them in {headers_where(ctx.get("hosting"))}.', impact='low',
                                 evidence={'missing': missing}))


def mixed_content_findings(ctx, origin_host, advice, changes):
    foreign = []
    for image in ctx.get('images') or []:
        if not isinstance(image, dict):
            continue
        src = image.get('src') if isinstance(image.get('src'), str) else ''
        resolved = image.get('resolved') if isinstance(image.get('resolved'), str) else ''
        insecure = src if src.lower().startswith('http://') else resolved if resolved.lower().startswith('http://') else None
        if not insecure:
            continue
        target = ops.target_of(image.get('edit'), attr='src') if src.lower().startswith('http://') else None
        if bare(host_of(insecure)) == bare(origin_host) and target:
            changes.append(ops.make_change(ctx, CHECK, 'images.src', src, 'https://' + src[len('http://'):], op='set_attr',
                                           target=target, confidence=0.85, impact='high',
                                           rationale='Mixed content: an http:// image on an https page is blocked or flagged by browsers'))
        else:
            foreign.append(insecure)
    resources = [l.get('href') for l in ctx.get('head_links') or [] if isinstance(l, dict) and isinstance(l.get('href'), str)
                 and l['href'].lower().startswith('http://')
                 and re.search(r'(?i)stylesheet|preload|modulepreload|icon|manifest', str(l.get('rel') or ''))]
    foreign += resources
    if foreign:
        advice.append(ops.advice(ctx, CHECK, 'security.mixed_content', 'Mixed content: resources loaded over http:// on an https '
                                 'page are blocked by browsers. Serve them over https (or self-host them).', impact='high',
                                 evidence={'urls': foreign[:10]}))
    insecure_links = [l for l in ctx.get('links') or [] if isinstance(l, dict) and isinstance(l.get('href'), str)
                      and l['href'].lower().startswith('http://')]
    forms = [l['href'] for l in insecure_links if l.get('kind') == 'action']
    if forms:
        advice.append(ops.advice(ctx, CHECK, 'security.insecure_form', 'A form submits to an http:// URL: data is sent unencrypted and '
                                 'browsers warn users. Use https.', impact='high', evidence={'actions': forms[:5]}))


def check_security(ctx, prior=None, writer=None):
    changes, advice, site_changes = [], [], []
    origin = ctx.get('origin')
    local = bool(ctx.get('origin_is_local')) or not origin
    https = urlsplit(origin).scheme.lower() == 'https' if isinstance(origin, str) and origin else None
    # Unknown origin: nothing was served/measured. Local dev server over http: http is expected there.
    if origin and not (local and not https):
        transport_findings(ctx, local, https, advice, site_changes)
    header_findings(ctx, local, advice)
    if https:
        mixed_content_findings(ctx, host_of(origin), advice, changes)
    return result(data={'https': https, 'local_origin': local}, changes=changes, advice=advice, site_changes=site_changes)


class SecurityCheck(OnpageCheck):
    """Check: HTTPS, permanent http->https redirect, HSTS, security headers, mixed content, insecure forms (deterministic)."""

    name = 'security'
    skill = 'https-security'
    uses_model = False
    depends_on = ()

    def run(self, ctx, prior, writer):
        return check_security(ctx, prior or {}, writer)
