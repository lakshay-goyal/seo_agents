"""Check 8 (deterministic): canonical, robots, lang, viewport, host redirect, prerender/shared shell, soft 404, slug.

Canonical/robots edge cases and conflicts (duplicates, cross-domain, X-Robots-Tag only, robots.txt) belong to indexability."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.reports.exploration_reports import value
from seo_agents.onpage.changes.head_recipes import REDIRECT_WHERE, SOFT_404_WHERE
from seo_agents.onpage.checks.check_helpers import add_target, result
from seo_agents.onpage.checks.base_check import OnpageCheck
from seo_agents.onpage.checks.indexability_check import intentional_noindex, is_noindex, page_noindex, robots_directives

# ====================================================================== 8. technical (deterministic)
def platform_change(field, current, value, *, where, hosting, rationale, change_id, impact='high'):
    change = ops.make_change(None, 'technical', field, current, value, target=ops.platform_target(where, hosting), op='platform_config',
                             confidence=0.9, rationale=rationale, impact=impact, change_id_=change_id)
    change['scope'] = 'site'
    return change


TEMPORARY = (302, 303, 307)
LOCAL_HOST = re.compile(r'(?i)^(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[?::1\]?)$|\.local$')
MIN_MAX_SCALE = 5  # Lighthouse `meta-viewport`: maximum-scale below 5 blocks zooming
CONTROL_TOKENS = {'noindex', 'none', 'nofollow', 'index', 'follow', 'all'}


def slash_policy(served):
    """'with' | 'without' | None: the URL form that the site's 200 response uses when the other one redirects."""
    slash = served.get('trailing_slash') if isinstance(served.get('trailing_slash'), dict) else {}
    policy = slash.get('value')
    if policy == 'redirect-to-slash' or (slash.get('with_slash') == 200 and slash.get('without_slash') in (301, 302, 307, 308)):
        return 'with'
    if policy == 'redirect-to-no-slash' or (slash.get('without_slash') == 200 and slash.get('with_slash') in (301, 302, 307, 308)):
        return 'without'
    return None


def expected_path(url, policy):
    url = url or '/'
    if url == '/' or re.search(r'\.[a-z0-9]{2,5}$', url, re.I):
        return url
    return url.rstrip('/') + '/' if policy == 'with' else url.rstrip('/') if policy == 'without' else url


def same_url(found, expected, policy):
    """Host compared case-insensitively; trailing slash compared strictly only when the site redirects one form."""
    try:
        a, b = urlsplit(found), urlsplit(expected)
        if (a.scheme.lower(), (a.hostname or '').lower(), a.port, a.query, a.fragment) != \
                (b.scheme.lower(), (b.hostname or '').lower(), b.port, b.query, b.fragment):
            return False
    except ValueError:  # malformed port
        return False
    return a.path == b.path if policy else (a.path.rstrip('/') or '/') == (b.path.rstrip('/') or '/')


def canonical_change(ctx, served, noindex, changes, inputs):
    head = ctx.get('head') or {}
    canonical = value(head.get('canonical'))
    canonical = canonical.strip() if isinstance(canonical, str) and canonical.strip() else None
    policy = slash_policy(served)
    path = expected_path(ctx.get('url') or '/', policy)
    # A local/dev origin is not the production origin: treat it like an unknown origin.
    origin = None if ctx.get('origin_is_local') else ctx.get('origin')
    if noindex:
        return (origin + path) if origin else None  # noindex + canonical conflict is reported by indexability
    target = ops.target_of(head.get('canonical'), attr='href') or add_target(ctx, 'technical.canonical')
    op = 'set_attr' if canonical else 'insert_node'
    if origin:
        expected = origin.rstrip('/') + path
        if canonical and re.match(r'(?i)^https?://', canonical) and same_url(canonical, expected, policy):
            return expected
    else:
        absolute = canonical and re.match(r'(?i)^https?://', canonical)
        try:
            local_canonical = bool(absolute) and bool(LOCAL_HOST.search(urlsplit(canonical).hostname or ''))
        except ValueError:  # malformed port
            local_canonical = True
        if canonical and not local_canonical and (urlsplit(canonical).path if absolute else canonical.split('?')[0].split('#')[0]) in \
                ({path} if policy else {path, path.rstrip('/') or '/', path.rstrip('/') + '/'}):
            return canonical if absolute else None  # path is right; only the production origin is unknown
        if absolute and not local_canonical:
            parts = urlsplit(canonical)
            expected = f'{parts.scheme}://{parts.netloc}{path}'
        else:
            expected = '[VERIFY: production origin, e.g. https://www.example.com]' + path
            change = ops.make_change(ctx, 'technical', 'technical.canonical', canonical, expected, target=target, op=op,
                                     confidence=0.6, status='needs_input', html=f'<link rel="canonical" href="{expected}">',
                                     rationale='Self-referencing absolute canonical for THIS route; the production origin is unknown')
            changes.append(change)
            inputs.append(ops.client_input(ctx, 'Production origin (scheme + host) for absolute canonical URLs',
                                           change=change['id'], field='technical.canonical'))
            return None
    changes.append(ops.make_change(ctx, 'technical', 'technical.canonical', canonical, expected, target=target, op=op,
                                   confidence=0.9, html=f'<link rel="canonical" href="{expected}">',
                                   rationale='Self-referencing absolute canonical for THIS route on the canonical host'))
    return expected


def robots_value(robots):
    """Drop noindex/none/nofollow (and index/follow/all), keep every other directive, lead with 'index, follow'."""
    kept = [t.strip() for t in str(robots or '').split(',') if t.strip() and t.strip().lower() not in CONTROL_TOKENS]
    return ', '.join(['index, follow'] + kept)


def viewport_params(viewport):
    """[(key, value)] in source order (key lower-cased, value as written)."""
    out = []
    for part in re.split(r'[,;]', str(viewport or '')):
        key, sep, val = part.partition('=')
        if key.strip():
            out.append((key.strip().lower(), val.strip() if sep else None))
    return out


def max_scale_low(val):
    try:
        return float(val) < MIN_MAX_SCALE
    except (TypeError, ValueError):
        return False


def viewport_problem(viewport):
    params = dict(viewport_params(viewport))
    problems = []
    if str(params.get('width') or '').lower() != 'device-width':
        problems.append('missing width=device-width')
    if str(params.get('user-scalable') or '').lower() in ('no', '0'):
        problems.append(f'user-scalable={params["user-scalable"]} blocks zooming')
    if 'maximum-scale' in params and max_scale_low(params['maximum-scale']):
        problems.append(f'maximum-scale={params["maximum-scale"]} (< {MIN_MAX_SCALE}) blocks zooming')
    return ', '.join(problems) or None


def viewport_value(viewport):
    """Fix only the offending params, keep the rest (e.g. viewport-fit=cover): width=device-width, initial-scale=1 first."""
    kept = [(k, v) for k, v in viewport_params(viewport)
            if k not in ('width', 'initial-scale')
            and not (k == 'user-scalable' and str(v or '').lower() in ('no', '0'))
            and not (k == 'maximum-scale' and max_scale_low(v))]
    return ', '.join(['width=device-width', 'initial-scale=1'] + [k if v is None else f'{k}={v}' for k, v in kept])


def scheme_only_hop(hop):
    """http://x/p -> https://x/p: owned by security (chg_http_to_https), not the host-redirect change."""
    try:
        src, dst = urlsplit(str(hop.get('url') or '')), urlsplit(str(hop.get('location') or hop.get('target') or ''))
    except ValueError:
        return False
    return bool(src.scheme and dst.scheme and src.scheme != dst.scheme and (src.hostname or '') == (dst.hostname or '')
                and (src.path or '/') == (dst.path or '/'))


def redirect_findings(ctx, served, advice, site_changes):
    chain = [r for r in (ctx.get('http') or {}).get('redirect_chain') or [] if isinstance(r, dict)]
    temporary = [f"{r.get('url')} -> {r.get('http_status')} -> {r.get('location') or r.get('target')}"
                 for r in chain if r.get('http_status') in TEMPORARY and not scheme_only_hop(r)]
    # http -> https (scheme) redirects belong to security (chg_http_to_https); a local origin describes a dev server.
    local = bool(ctx.get('origin_is_local'))
    host = served.get('canonical_host') if isinstance(served.get('canonical_host'), dict) and not local else {}
    hop = host.get('host_redirect') if isinstance(host.get('host_redirect'), dict) else None
    if hop and hop.get('permanent') is False:
        temporary.append(f"host redirect {hop.get('from') or ''} -> {hop.get('to') or ''} uses {hop.get('status')}".replace('  ', ' '))
    if temporary:
        hosting = ctx.get('hosting')
        site_changes.append(platform_change(
            'platform.redirect', temporary, 'Make every host/page redirect permanent (301 or 308)',
            where=REDIRECT_WHERE.get(hosting, 'hosting / web server redirect rules (make it permanent: 301 or 308)'), hosting=hosting,
            rationale='Host redirect is temporary; a permanent redirect consolidates signals on the canonical host',
            change_id='chg_redirect_permanent'))
    host_chain = [r for r in host.get('redirect_chain') or [] if isinstance(r, dict)]
    if len(host_chain) > 1:
        advice.append(ops.advice(ctx, 'technical', 'technical.redirect_chain', f'Reaching the canonical host takes {len(host_chain)} '
                                 'redirects (e.g. http://apex -> https://apex -> https://www); redirect every variant straight to the '
                                 'final origin in one 301/308 hop.', evidence={'chain': [f"{r.get('url')} -> {r.get('http_status')}"
                                                                                         for r in host_chain]}))


def slug_problems(url):
    path = (url or '/').split('?')[0].split('#')[0]
    if path in ('', '/') or re.search(r'[\[\]{}<>*:]', path):
        return []
    segments = [s for s in path.strip('/').split('/') if s]
    problems = []
    if any(s != s.lower() for s in segments):
        problems.append('uppercase characters')
    if '_' in path:
        problems.append('underscores instead of hyphens')
    if '%20' in path or ' ' in path:
        problems.append('spaces')
    if re.search(r'[^\x00-\x7f]|%[89a-f][0-9a-f]', path, re.I):
        problems.append('non-ASCII characters')
    if len(path) > 75:
        problems.append(f'long path ({len(path)} chars)')
    if len(segments) > 4:
        problems.append(f'{len(segments)} folders deep')
    if segments and len(segments[-1].split('-')) > 6:
        problems.append('slug longer than 6 words')
    if any(not re.fullmatch(r'(?:19|20)\d{2}', n) for n in re.findall(r'\d{4,}', path)):
        problems.append('long numeric id')
    return problems


def check_technical(ctx, prior=None, writer=None):
    changes, advice, site_changes, inputs = [], [], [], []
    head = ctx.get('head') or {}
    served = ctx.get('site_served') or {}
    url = ctx.get('url') or '/'
    meta, _header, noindex = page_noindex(ctx)
    # Only META noindex skips the canonical fix: header noindex is often added by preview hosts (*.vercel.app).
    expected_canonical = canonical_change(ctx, served, is_noindex(meta), changes, inputs)
    robots = value(head.get('robots')) or ''
    # Only the meta tag is changed here; a noindex sent only in X-Robots-Tag is a hosting fix (indexability).
    if is_noindex(robots_directives(robots)) and not intentional_noindex(url):
        changes.append(ops.make_change(ctx, 'technical', 'technical.robots', robots, robots_value(robots),
                                       target=ops.target_of(head.get('robots'), attr='content'), op='set_attr', confidence=0.8,
                                       rationale='A page meant to rank is noindexed'))
    if not value(head.get('lang')):
        headers = (ctx.get('http') or {}).get('headers') if isinstance((ctx.get('http') or {}).get('headers'), dict) else {}
        lang = str(headers.get('content-language') or '').split(',')[0].strip() or ctx.get('lang') or 'en'
        target = ops.target_of(head.get('lang'), attr='lang')
        status = 'proposed'
        if target is None:
            target = add_target(ctx, 'technical.lang')
            if target is not None:
                target = {**target, 'attr': 'lang', 'placement': '<html lang>'}
                target['key'] = (target.get('key') or '') + '|html-lang'
            status = 'needs_input'  # the head insertion point is not the <html> element
        changes.append(ops.make_change(ctx, 'technical', 'technical.lang', None, lang, target=target, op='set_attr', status=status,
                                       confidence=0.8, rationale='Declare the content language on the <html> element'))
    viewport = value(head.get('viewport'))
    problem = viewport_problem(viewport)
    if problem:
        changes.append(ops.make_change(ctx, 'technical', 'technical.viewport', viewport, viewport_value(viewport),
                                       target=ops.target_of(head.get('viewport'), attr='content') or add_target(ctx, 'technical.viewport'),
                                       op='set_attr' if viewport else 'insert_node', confidence=0.85,
                                       rationale=f'Mobile-friendly, zoomable viewport ({problem})'))
    redirect_findings(ctx, served, advice, site_changes)
    rendering = ctx.get('rendering') or {}
    raw_source = ctx.get('raw_source') or {}
    rendered = rendering.get('rendered_words') or ctx.get('main_words')
    raw = rendering.get('raw_html_words')
    if raw is None and 'CSR' in (raw_source.get('rendering_modes') or []):
        raw = raw_source.get('word_count')
    csr = ctx.get('head_scope') == 'shared-shell' or (rendered and raw is not None and raw < 0.5 * rendered)
    if csr:
        recipe = ctx.get('recipe') or {}
        site_changes.append(platform_change(
            'platform.prerender', {'raw_html_words': raw, 'rendered_words': rendered, 'head_scope': ctx.get('head_scope')},
            'Serve each route\'s own title, description, canonical, H1, main content and JSON-LD in the initial HTML',
            where=recipe.get('precondition') or recipe.get('mechanism') or 'prerender/SSG or SSR', hosting=ctx.get('hosting'),
            change_id='chg_prerender',
            rationale='Every route is served the same client-side shell; crawlers that do not run JavaScript (social previews, '
                      'most AI crawlers) see one identical, almost empty page for all URLs. Per-route head changes depend on this.'))
        advice.append(ops.advice(ctx, 'technical', 'technical.prerender',
                                 f'Initial HTML has {raw if raw is not None else "~0"} words vs {rendered} after JavaScript, and the head is shared by '
                                 f'every route. Enable prerendering first ({recipe.get("precondition") or recipe.get("mechanism")}); the head changes on this page are '
                                 'blocked until then.', impact='high'))
    soft = served.get('soft_404') if isinstance(served.get('soft_404'), dict) else {}
    site_soft = (ctx.get('site') or {}).get('soft_404')
    soft_flag = site_soft.get('soft_404') if isinstance(site_soft, dict) else site_soft
    if soft.get('soft_404') or soft_flag is True:
        site_changes.append(platform_change(
            'platform.soft_404', 'unknown URLs return 200 with a client-side "not found" view',
            'Return HTTP 404 for unknown URLs (serve a static 404 page; limit SPA rewrites to real routes)',
            where=SOFT_404_WHERE.get(ctx.get('hosting'), 'hosting/server config: return 404 for unknown paths'), hosting=ctx.get('hosting'),
            rationale='Soft 404s waste crawl budget and can get thin error views indexed', change_id='chg_soft_404', impact='high'))
    slash = served.get('trailing_slash') if isinstance(served.get('trailing_slash'), dict) else {}
    if not ctx.get('site_items_owner', ctx.get('is_home')):
        slash = {}  # a site-wide fact: reported once, on the page that owns site items
    slash_temporary = [s for s in slash.get('redirect_statuses') or [] if s in TEMPORARY]
    if slash.get('with_slash') == 200 and slash.get('without_slash') == 200:
        advice.append(ops.advice(ctx, 'technical', 'technical.trailing_slash', 'Both /path and /path/ return 200 (duplicate URLs); '
                                 'redirect one form permanently or rely on a self-referencing canonical per route.', impact='medium',
                                 evidence={'sample': slash.get('sample')}))
    elif slash.get('value') == 'inconsistent':
        advice.append(ops.advice(ctx, 'technical', 'technical.trailing_slash', 'Trailing-slash handling is inconsistent '
                                 f'(/x -> {slash.get("without_slash")}, /x/ -> {slash.get("with_slash")}); pick one form, serve it with '
                                 '200 and 301/308 the other.', impact='medium', evidence={'sample': slash.get('sample')}))
    elif slash_temporary:
        advice.append(ops.advice(ctx, 'technical', 'technical.trailing_slash', 'The trailing-slash redirect is temporary '
                                 f'({", ".join(str(s) for s in slash_temporary)}); make it 301/308.', impact='medium',
                                 evidence={'sample': slash.get('sample'), 'redirects': slash.get('redirects')}))
    parity = ctx.get('mobile_parity') or {}
    if isinstance(parity, dict) and (parity.get('headings_missing_on_mobile') or parity.get('links_missing_on_mobile')):
        advice.append(ops.advice(ctx, 'technical', 'technical.mobile_parity', 'Mobile DOM is missing headings/links that desktop has; '
                                 'mobile-first indexing uses the mobile version.', evidence=parity))
    problems = slug_problems(url)
    if problems:
        advice.append(ops.advice(ctx, 'technical', 'technical.slug', 'Readable, lowercase, hyphenated, short URL path (changing it '
                                 'needs a 301): ' + ', '.join(problems), impact='low', evidence={'url': url, 'problems': problems}))
    return result(data={'expected_canonical': expected_canonical, 'csr_shell': bool(csr), 'noindex': noindex,
                        'intentional_noindex': intentional_noindex(url)},
                  changes=changes, advice=advice, client_inputs=inputs, site_changes=site_changes)


class TechnicalOnpageCheck(OnpageCheck):
    """Check: Canonical, robots, lang, viewport, host redirect, prerender/shared shell, soft 404, trailing slash, slug (deterministic)."""

    name = 'technical'
    skill = 'technical-onpage'
    uses_model = False
    depends_on = ()

    def run(self, ctx, prior, writer):
        return check_technical(ctx, prior, writer)
