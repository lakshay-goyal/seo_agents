"""Check 8 (deterministic): canonical, robots, lang, viewport, host redirect, prerender/shared shell, soft 404, slug."""

from __future__ import annotations

import re

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.reports.exploration_reports import value
from seo_agents.onpage.changes.head_recipes import REDIRECT_WHERE, SOFT_404_WHERE
from seo_agents.onpage.checks.check_helpers import add_target, result
from seo_agents.onpage.checks.base_check import OnpageCheck

# ====================================================================== 8. technical (deterministic)
def platform_change(field, current, value, *, where, hosting, rationale, change_id, impact='high'):
    change = ops.make_change(None, 'technical', field, current, value, target=ops.platform_target(where, hosting), op='platform_config',
                             confidence=0.9, rationale=rationale, impact=impact, change_id_=change_id)
    change['scope'] = 'site'
    return change


def check_technical(ctx, prior=None, writer=None):
    changes, advice, site_changes = [], [], []
    head = ctx['head']
    canonical = value(head.get('canonical'))
    expected_canonical = ctx['absolute_url'] or ctx['url']
    if not canonical or canonical.rstrip('/') != expected_canonical.rstrip('/'):
        target = ops.target_of(head.get('canonical'), attr='href') or add_target(ctx, 'technical.canonical')
        changes.append(ops.make_change(ctx, 'technical', 'technical.canonical', canonical, expected_canonical, target=target,
                                       op='set_attr' if canonical else 'insert_node', confidence=0.9,
                                       html=f'<link rel="canonical" href="{expected_canonical}">',
                                       rationale='Self-referencing absolute canonical for THIS route on the canonical host'))
    robots = value(head.get('robots')) or ''
    x_robots = ((ctx['http'].get('headers') or {}).get('x-robots-tag')) or ''
    if re.search(r'\bnoindex\b', (robots + ' ' + x_robots).lower()):
        changes.append(ops.make_change(ctx, 'technical', 'technical.robots', robots or x_robots, 'index, follow',
                                       target=ops.target_of(head.get('robots'), attr='content'), op='set_attr', confidence=0.8,
                                       rationale='A page meant to rank is noindexed'))
    if not value(head.get('lang')):
        changes.append(ops.make_change(ctx, 'technical', 'technical.lang', None, ctx['lang'], target=add_target(ctx, 'technical.lang'),
                                       op='set_attr', confidence=0.8, rationale='Declare the content language on <html>'))
    viewport = value(head.get('viewport'))
    if not viewport or 'width=device-width' not in viewport:
        changes.append(ops.make_change(ctx, 'technical', 'technical.viewport', viewport, 'width=device-width, initial-scale=1',
                                       target=ops.target_of(head.get('viewport'), attr='content') or add_target(ctx, 'technical.viewport'),
                                       op='set_attr' if viewport else 'insert_node', confidence=0.85, rationale='Mobile-friendly viewport'))
    chain = ctx['http'].get('redirect_chain') or []
    temporary = [r for r in chain if r.get('http_status') in (302, 303, 307)]
    if temporary:
        host = ctx['hosting']
        site_changes.append(platform_change(
            'platform.redirect', [f"{r['url']} -> {r.get('http_status')} -> {r.get('location')}" for r in temporary],
            [f"{r['url']} -> 308 -> {r.get('location')}" for r in temporary],
            where=REDIRECT_WHERE.get(host, 'hosting / web server redirect rules (make it permanent: 301 or 308)'), hosting=host,
            rationale='Host redirect is temporary; a permanent redirect consolidates signals on the canonical host',
            change_id='chg_redirect_permanent'))
    rendered = ctx['rendering'].get('rendered_words') or ctx['main_words']
    raw = ctx['rendering'].get('raw_html_words')
    if raw is None and 'CSR' in (ctx['raw_source'].get('rendering_modes') or []):
        raw = ctx['raw_source'].get('word_count')
    csr = ctx['head_scope'] == 'shared-shell' or (rendered and raw is not None and raw < 0.5 * rendered)
    if csr:
        recipe = ctx['recipe']
        site_changes.append(platform_change(
            'platform.prerender', {'raw_html_words': raw, 'rendered_words': rendered, 'head_scope': ctx['head_scope']},
            'Serve each route\'s own title, description, canonical, H1, main content and JSON-LD in the initial HTML',
            where=recipe.get('precondition') or recipe.get('mechanism') or 'prerender/SSG or SSR', hosting=ctx['hosting'],
            change_id='chg_prerender',
            rationale='Every route is served the same client-side shell; crawlers that do not run JavaScript (social previews, '
                      'most AI crawlers) see one identical, almost empty page for all URLs. Per-route head changes depend on this.'))
        advice.append(ops.advice(ctx, 'technical', 'technical.prerender',
                                 f'Initial HTML has {raw if raw is not None else "~0"} words vs {rendered} after JavaScript, and the head is shared by '
                                 f'every route. Enable prerendering first ({recipe.get("precondition") or recipe.get("mechanism")}); the head changes on this page are '
                                 'blocked until then.', impact='high'))
    served = ctx['site_served'] or {}
    soft = served.get('soft_404') if isinstance(served.get('soft_404'), dict) else {}
    soft_flag = ctx['site'].get('soft_404') if not isinstance(ctx['site'].get('soft_404'), dict) else ctx['site']['soft_404'].get('soft_404')
    if soft.get('soft_404') or soft_flag is True:
        site_changes.append(platform_change(
            'platform.soft_404', 'unknown URLs return 200 with a client-side "not found" view',
            'Return HTTP 404 for unknown URLs (serve a static 404 page; limit SPA rewrites to real routes)',
            where=SOFT_404_WHERE.get(ctx['hosting'], 'hosting/server config: return 404 for unknown paths'), hosting=ctx['hosting'],
            rationale='Soft 404s waste crawl budget and can get thin error views indexed', change_id='chg_soft_404', impact='high'))
    slash = served.get('trailing_slash') or {}
    if slash.get('with_slash') == 200 and slash.get('without_slash') == 200:
        advice.append(ops.advice(ctx, 'technical', 'technical.trailing_slash', 'Both /path and /path/ return 200 (duplicate URLs); '
                                 'redirect one form permanently or rely on a self-referencing canonical per route.', impact='medium'))
    parity = ctx['mobile_parity'] or {}
    if parity.get('headings_missing_on_mobile') or parity.get('links_missing_on_mobile'):
        advice.append(ops.advice(ctx, 'technical', 'technical.mobile_parity', 'Mobile DOM is missing headings/links that desktop has; '
                                 'mobile-first indexing uses the mobile version.', evidence=parity))
    slug = ctx['url'].strip('/').split('/')[-1] if ctx['url'] != '/' else ''
    if slug and (slug != slug.lower() or '_' in slug or len(slug.split('-')) > 6 or re.search(r'\d{4,}', slug)):
        advice.append(ops.advice(ctx, 'technical', 'technical.slug', 'Readable, lowercase, hyphenated slug (changing it needs a 301)',
                                 impact='low', evidence={'url': ctx['url']}))
    return result(data={'expected_canonical': expected_canonical, 'csr_shell': bool(csr)}, changes=changes, advice=advice,
                  site_changes=site_changes)


class TechnicalOnpageCheck(OnpageCheck):
    """Check: Canonical, robots, lang, viewport, host redirect, prerender/shared shell, soft 404, trailing slash, slug (deterministic)."""

    name = 'technical'
    skill = 'technical-onpage'
    uses_model = False
    depends_on = ()

    def run(self, ctx, prior, writer):
        return check_technical(ctx, prior, writer)
