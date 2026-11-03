"""Check 20 (deterministic): site crawlability - robots.txt rules, sitemap coverage, host consolidation, crawl errors,
orphans / click depth, redirected / blocked links, favicon, custom 404, pagination, AMP.

Site-wide items are emitted as `site_changes` with fixed change ids (site_level_builder dedupes them across pages) or as
advice emitted once per run (from the home page, or the first model URL when the home page is not in the model).
Per-page advice only for page-specific facts (sitemap coverage of this URL, pagination). `indexability` owns per-URL
robots.txt blocking; `link_hygiene` owns per-page orphan / depth notes; `site_level_builder` creates missing
robots.txt / sitemap.xml / llms.txt files.
"""

from __future__ import annotations

import re

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.reports.exploration_reports import value
from seo_agents.onpage.checks.check_helpers import result
from seo_agents.onpage.checks.base_check import OnpageCheck
from seo_agents.onpage.checks.technical_check import platform_change

CHECK = 'crawlability'
AI_BOTS = ('GPTBot', 'ClaudeBot', 'Claude-Web', 'anthropic-ai', 'PerplexityBot', 'Google-Extended', 'CCBot', 'Applebot-Extended')
# Google supports user-agent/allow/disallow/sitemap; crawl-delay/host/clean-param are other engines' extensions.
KNOWN_DIRECTIVES = {'user-agent', 'allow', 'disallow', 'sitemap', 'crawl-delay', 'host', 'clean-param'}
ASSET_RULE = re.compile(r'(?i)^/(?:_next|static|assets|build|dist|js|css)(?:/|$)|\.(?:js|css)(?:\$|\*|$)|\*\.(?:js|css)')
PAGINATED = re.compile(r'(?i)(?:[?&](?:page|p|pg)=(\d+)|/page/(\d+)/?$)')
FILE_WHERE = 'robots.txt in the static output (public/robots.txt or the hosting robots config)'


# ---------------------------------------------------------------- small, None-safe accessors
def _dict(obj):
    return obj if isinstance(obj, dict) else {}


def _list(obj):
    return obj if isinstance(obj, list) else []


def _state(entry):
    """present | missing | spa-shell-fallback | redirect | error | invalid | None."""
    if isinstance(entry, str):
        return entry
    entry = _dict(entry)
    for key in ('value', 'kind', 'state'):
        if isinstance(entry.get(key), str):
            return entry[key]
    status = entry.get('http_status') if isinstance(entry.get('http_status'), int) else entry.get('status')
    ctype = str(entry.get('content_type') or '').lower()
    if status == 200 and 'html' in ctype:
        return 'spa-shell-fallback'
    if status in (404, 410):
        return 'missing'
    return None


def _gaps(ctx, agent):
    return [g for g in _list(_dict(ctx.get('site_gaps')).get(agent)) if isinstance(g, str)]


def _head_links(ctx):
    return [l for l in _list(ctx.get('head_links')) if isinstance(l, dict)]


def _rels(link):
    rel = link.get('rel')
    rel = ' '.join(rel) if isinstance(rel, list) else rel if isinstance(rel, str) else ''
    return set(rel.lower().split())


def emits_site_items(ctx):
    """Site-wide advice once per run: on the ctx LoadReportsNode marked `site_items_owner` (home page if selected, else the
    first selected page). Fallback when the key is absent: the home page, or the first model URL when '/' is not in the model."""
    if 'site_items_owner' in ctx:
        return bool(ctx.get('site_items_owner'))
    if ctx.get('is_home'):
        return True
    pages = _dict(ctx.get('site_pages'))
    if '/' in pages or not pages:
        return False
    urls = sorted(u for u in pages if isinstance(u, str))
    return bool(urls) and ctx.get('url') == urls[0]


# ---------------------------------------------------------------- robots.txt
def parse_robots(body):
    """robots.txt -> {groups: [{agents, rules: [(directive, value)]}], sitemaps, crawl_delays, unknown, malformed}."""
    out = {'groups': [], 'sitemaps': [], 'crawl_delays': [], 'unknown': [], 'malformed': []}
    group, last_was_agent = None, False
    for number, raw in enumerate((body or '').splitlines(), 1):
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        if ':' not in line:
            out['malformed'].append({'line': number, 'text': raw.strip()[:120]})
            continue
        key, val = line.split(':', 1)
        key, val = key.strip().lower(), val.strip()
        if key not in KNOWN_DIRECTIVES:
            out['unknown'].append({'line': number, 'directive': key[:40], 'text': raw.strip()[:120]})
            last_was_agent = False
            continue
        if key == 'sitemap':
            out['sitemaps'].append(val)
            continue
        if key == 'user-agent':
            if group is None or not last_was_agent:
                group = {'agents': [], 'rules': []}
                out['groups'].append(group)
            group['agents'].append(val.lower())
            last_was_agent = True
            continue
        last_was_agent = False
        if group is None:
            out['malformed'].append({'line': number, 'text': raw.strip()[:120], 'issue': 'rule before any User-agent'})
            continue
        if key == 'crawl-delay':
            out['crawl_delays'].append({'agents': list(group['agents']), 'value': val})
        group['rules'].append((key, val))
    return out


def _group_for(parsed, bot):
    bot = bot.lower()
    exact = [g for g in parsed['groups'] if bot in g['agents']]
    if exact:
        return exact, True
    return [g for g in parsed['groups'] if '*' in g['agents']], False


def bot_policy(parsed, bot):
    """allowed | blocked | partial for a crawler, and whether it has its own group."""
    groups, explicit = _group_for(parsed, bot)
    rules = [r for g in groups for r in g['rules'] if r[0] in ('allow', 'disallow')]
    disallow = [v for k, v in rules if k == 'disallow' and v]
    allow_root = any(k == 'allow' and v in ('/', '/*') for k, v in rules)
    if any(v in ('/', '/*') for v in disallow) and not allow_root:
        return 'blocked', explicit
    return ('partial' if disallow else 'allowed'), explicit


def robots_findings(ctx, advice, site_changes, data, once):
    entry = _dict(_dict(ctx.get('site_served')).get('robots_txt'))
    state = _state(entry)
    data['robots_txt'] = {'state': state}
    body = entry.get('body')
    if state != 'present' or not isinstance(body, str) or not body.strip():
        return  # missing / SPA fallback robots.txt is created by site_level_builder (chg_robots_txt)
    parsed = parse_robots(body)
    hosting = ctx.get('hosting')
    star = [g for g in parsed['groups'] if '*' in g['agents']]
    star_policy = bot_policy(parsed, '*')[0] if star else 'allowed'
    blocked_assets = sorted({v for g in parsed['groups'] if '*' in g['agents'] or 'googlebot' in g['agents']
                             for k, v in g['rules'] if k == 'disallow' and v and ASSET_RULE.search(v)})
    ai = {'allowed': [], 'blocked': [], 'partial': [], 'explicit': []}
    for bot in AI_BOTS:
        policy, explicit = bot_policy(parsed, bot)
        ai[policy].append(bot)
        if explicit:
            ai['explicit'].append(bot)
    data['robots_txt'].update({'sitemaps': parsed['sitemaps'][:10], 'disallow_all': star_policy == 'blocked',
                               'blocked_assets': blocked_assets[:20], 'crawl_delay': parsed['crawl_delays'][:5],
                               'ai_crawlers': ai, 'unknown_directives': parsed['unknown'][:10], 'malformed': parsed['malformed'][:10]})
    if star_policy == 'blocked':
        site_changes.append(_site_change(
            'platform.robots_disallow_all', 'User-agent: *\nDisallow: /', 'User-agent: *\nAllow: /',
            where=FILE_WHERE, hosting=hosting, change_id='chg_robots_disallow_all', impact='high',
            rationale='robots.txt disallows the whole site for every crawler, so nothing new can be crawled or refreshed'
                      + (' (served by a local/dev origin: confirm production robots.txt)' if ctx.get('origin_is_local') else '')))
    if not once:
        return
    if not parsed['sitemaps']:
        advice.append(ops.advice(ctx, CHECK, 'crawl.robots_sitemap', 'robots.txt has no `Sitemap:` line; add '
                                 f"`Sitemap: {(ctx.get('origin') or '[VERIFY: origin]')}/sitemap.xml` so every crawler finds it.",
                                 impact='medium'))
    if blocked_assets:
        advice.append(ops.advice(ctx, CHECK, 'crawl.robots_assets', 'robots.txt disallows CSS/JS assets ('
                                 + ', '.join(blocked_assets[:6]) + '); Google needs them to render the page. Remove those rules.',
                                 impact='high', evidence={'rules': blocked_assets[:20]}))
    if parsed['crawl_delays']:
        advice.append(ops.advice(ctx, CHECK, 'crawl.robots_crawl_delay', 'robots.txt sets Crawl-delay; Google ignores it (Bing/Yandex '
                                 'slow down). Remove it unless the server truly needs throttling.', impact='low',
                                 evidence={'crawl_delay': parsed['crawl_delays'][:5]}))
    if 'no-ai-crawler-policy' in _gaps(ctx, 'ai_search_readiness'):
        advice.append(ops.advice(ctx, CHECK, 'crawl.ai_crawlers', 'robots.txt has no explicit AI crawler policy. Decide per bot '
                                 '(GPTBot, ClaudeBot, PerplexityBot, Google-Extended, CCBot, Applebot-Extended): allow for AI '
                                 'search visibility, or disallow to opt out of training; today they inherit the `*` rules.',
                                 impact='low', evidence={'ai_crawlers': ai}))
    odd = parsed['unknown'] + parsed['malformed']
    if odd:
        advice.append(ops.advice(ctx, CHECK, 'crawl.robots_syntax', 'robots.txt has lines Google does not understand (unknown directives '
                                 'or missing `key: value`); they are ignored. Fix or remove them.', impact='low',
                                 evidence={'lines': odd[:10]}))


# ---------------------------------------------------------------- sitemap
def sitemap_findings(ctx, advice, data, once):
    entry = _dict(_dict(ctx.get('site_served')).get('sitemap_xml'))
    state = _state(entry)
    discovered = [d for d in _list(ctx.get('discovered_by')) if isinstance(d, str)]
    pages = _dict(ctx.get('site_pages'))
    me = _dict(pages.get(ctx.get('url')))
    in_sitemap = 'sitemap:live' in discovered
    data['sitemap'] = {'state': state, 'page_in_live_sitemap': in_sitemap if discovered else None}
    # invalid / spa-shell-fallback / missing sitemaps: site_level_builder proposes chg_sitemap_xml, so no advice here.
    status = me.get('http_status')
    bad = bool(me.get('noindex')) or bool(me.get('redirects_to')) or (isinstance(status, int) and status != 200)
    # a live sitemap is only trusted when it is served AND exploration actually discovered URLs from it
    live = state == 'present' and (in_sitemap or any(_dict(r).get('in_sitemap') for r in pages.values()))
    if live and discovered and not in_sitemap and not bad:
        advice.append(ops.advice(ctx, CHECK, 'crawl.sitemap_coverage', f"{ctx.get('url')} is not in the live sitemap (found via "
                                 + ', '.join(discovered[:3]) + '); add it so crawlers discover and refresh it.', impact='medium'))
    if in_sitemap and bad:
        advice.append(ops.advice(ctx, CHECK, 'crawl.sitemap_bad_entry', f"{ctx.get('url')} is listed in the sitemap but is "
                                 + ('noindex' if me.get('noindex') else f"redirected to {me.get('redirects_to')}" if me.get('redirects_to')
                                    else f'HTTP {status}') + '; list only canonical, indexable 200 URLs.', impact='high'))
    if once and live:
        listed_bad = [{'url': u, 'http_status': r.get('http_status'), 'noindex': r.get('noindex'), 'redirects_to': r.get('redirects_to')}
                      for u, r in pages.items() if isinstance(r, dict) and r.get('in_sitemap') and u != ctx.get('url')
                      and (r.get('noindex') or r.get('redirects_to') or (isinstance(r.get('http_status'), int) and r['http_status'] != 200))]
        if listed_bad:
            advice.append(ops.advice(ctx, CHECK, 'crawl.sitemap_bad_entries', f'{len(listed_bad)} sitemap URL(s) are noindex, redirected '
                                     'or not 200; list only canonical, indexable URLs.', impact='high', evidence={'urls': listed_bad[:30]}))


# ---------------------------------------------------------------- host / errors / graph / links
def host_findings(ctx, advice, site_changes, data, once):
    served = _dict(ctx.get('site_served'))
    host = _dict(served.get('canonical_host'))
    redirect = _dict(host.get('host_redirect'))
    data['host'] = {'canonical': host.get('value') or _dict(ctx.get('site')).get('canonical_host'), 'host_redirect': redirect or None}
    if ctx.get('origin_is_local'):
        return
    # a temporary host redirect is owned by technical_check (chg_redirect_permanent); only the record is kept here
    alternate = _dict(host.get('alternate') or served.get('alternate_host') or served.get('www_variant'))
    if once and alternate.get('http_status') == 200 and not alternate.get('redirect_chain') and not redirect:
        advice.append(ops.advice(ctx, CHECK, 'crawl.host_consolidation', 'Both www and non-www hosts answer 200 with no redirect; pick one '
                                 'and 301/308 the other to it.', impact='high', evidence={'alternate': alternate}))


def site_findings(ctx, advice, site_changes, data):
    site = _dict(ctx.get('site'))
    errors = [e for e in _list(site.get('crawl_errors')) if isinstance(e, dict)]
    if errors:
        rows = [{'url': e.get('url'), 'http_status': e.get('http_status'), 'status': e.get('status'),
                 'linked_from': _list(e.get('linked_from'))[:5]} for e in errors[:40]]
        server = any(isinstance(e.get('http_status'), int) and e['http_status'] >= 500 for e in errors)
        advice.append(ops.advice(ctx, CHECK, 'crawl.errors', f'{len(errors)} discovered URL(s) failed to load (4xx/5xx or network '
                                 'errors). Fix the target, or update/remove the links that point to it (see linked_from).',
                                 impact='high' if server or any(r['linked_from'] for r in rows) else 'medium', evidence={'urls': rows}))
    depth = _dict(site.get('click_depth'))
    orphans = [o for o in _list(site.get('orphan_candidates')) if isinstance(o, str)]
    deep = depth.get('pages_deeper_than_3')
    data['graph'] = {'max_click_depth': depth.get('max'), 'pages_deeper_than_3': deep, 'orphan_candidates': len(orphans)}
    if orphans:
        advice.append(ops.advice(ctx, CHECK, 'site.orphans', f'{len(orphans)} page(s) have no internal links pointing at them; link them '
                                 'from navigation, hubs or related content (or remove them from the sitemap if retired).',
                                 impact='medium', evidence={'urls': orphans[:30]}))
    if isinstance(deep, int) and deep > 0:
        advice.append(ops.advice(ctx, CHECK, 'site.click_depth', f'{deep} page(s) sit more than 3 clicks from the home page (max '
                                 f"{depth.get('max')}); add hub/category links so important pages are within 3 clicks.",
                                 impact='medium', evidence=depth))
    redirected = [u for u in _list(site.get('redirected_links')) if isinstance(u, (str, dict))]
    if redirected:
        advice.append(ops.advice(ctx, CHECK, 'crawl.redirected_links', f'{len(redirected)} linked URL(s) redirect; point internal links '
                                 'straight at the final URL to save crawl hops.', impact='low', evidence={'urls': redirected[:30]}))
    blocked = [u for u in _list(site.get('blocked_links')) if isinstance(u, (str, dict))]
    if blocked:
        advice.append(ops.advice(ctx, CHECK, 'crawl.blocked_links', f'{len(blocked)} linked URL(s) blocked the link checker (403/429/'
                                 'bot protection); verify they open for users and crawlers.', impact='low', evidence={'urls': blocked[:30]}))


def platform_findings(ctx, site_changes, data):
    hosting = ctx.get('hosting')
    links = _head_links(ctx)
    has_icon = any(_rels(l) & {'icon', 'shortcut', 'apple-touch-icon'} for l in links)
    data['favicon'] = has_icon if links else None
    if 'no-favicon' in _gaps(ctx, 'mobile_parity') or (links and not has_icon):
        site_changes.append(_site_change(
            'platform.favicon', None, '<link rel="icon" href="/favicon.ico" sizes="any"> + <link rel="icon" type="image/svg+xml" '
            'href="/icon.svg"> + <link rel="apple-touch-icon" href="/apple-touch-icon.png"> (files in the static output)',
            where='static output (public/) + the shared <head>', hosting=hosting, change_id='chg_favicon', impact='low',
            rationale='No favicon: search results and browser tabs show a generic icon (Google shows the site favicon in results)'))
    if 'no-404' in _gaps(ctx, 'canonicals_redirects') and not _soft_404(ctx):
        site_changes.append(_site_change(
            'platform.custom_404', None, 'A custom 404 page (helpful links + search) served with HTTP status 404 for unknown URLs',
            where='static output 404.html / framework not-found route + hosting fallback', hosting=hosting,
            change_id='chg_custom_404', impact='medium',
            rationale='No custom 404 page: lost visitors get a bare error and unknown URLs may be served as soft 404s'))


def _soft_404(ctx):
    """Soft 404 measured (technical_check owns that fix as chg_soft_404, which already ships a real 404 page)."""
    soft = _dict(_dict(ctx.get('site_served')).get('soft_404'))
    site_soft = _dict(ctx.get('site')).get('soft_404')
    flag = site_soft.get('soft_404') if isinstance(site_soft, dict) else site_soft
    return bool(soft.get('soft_404')) or flag is True or soft.get('value') in ('soft-404', 'soft_404', True)


def page_findings(ctx, advice, data):
    links = _head_links(ctx)
    pagination = [{'rel': sorted(_rels(l) & {'next', 'prev', 'previous'})[0], 'href': l.get('href')} for l in links
                  if _rels(l) & {'next', 'prev', 'previous'}]
    amp = [l.get('href') for l in links if 'amphtml' in _rels(l)]
    if amp:
        data['amp'] = amp[:3]
    if pagination:
        data['pagination'] = pagination[:4]
        advice.append(ops.advice(ctx, CHECK, 'crawl.pagination_rel', 'rel="next"/"prev" are present; Google no longer uses them for '
                                 'indexing. Keep them if harmless, but rely on plain crawlable <a href> links between pages.',
                                 impact='low', evidence={'links': pagination[:4]}))
    url = ctx.get('url') or ''
    match = PAGINATED.search(url)
    number = next((int(g) for g in (match.groups() if match else ()) if g), None)
    if number and number > 1:
        canonical = value(_dict(ctx.get('head')).get('canonical'))
        if isinstance(canonical, str) and canonical:
            path = re.sub(r'^(?:https?:)?//[^/]+', '', canonical) or '/'
            first = PAGINATED.sub('', url) or '/'
            if path.rstrip('/') == first.rstrip('/') and not PAGINATED.search(path):
                advice.append(ops.advice(ctx, CHECK, 'crawl.pagination_canonical', f'Paginated page {url} canonicalises to page 1 '
                                         f'({canonical}); items on deeper pages drop out of the index. Each paginated page must '
                                         'self-canonicalise.', impact='high', evidence={'canonical': canonical, 'page': number}))


def _site_change(field, current, value_, *, where, hosting, change_id, impact, rationale):
    change = platform_change(field, current, value_, where=where, hosting=hosting, rationale=rationale, change_id=change_id,
                             impact=impact)
    change['check'] = CHECK
    return change


def check_crawlability(ctx, prior=None, writer=None):
    advice, site_changes, data = [], [], {}
    once = emits_site_items(ctx)
    robots_findings(ctx, advice, site_changes, data, once)
    sitemap_findings(ctx, advice, data, once)
    host_findings(ctx, advice, site_changes, data, once)
    if once:
        site_findings(ctx, advice, site_changes, data)
    platform_findings(ctx, site_changes, data)
    page_findings(ctx, advice, data)
    return result(data=data, advice=advice, site_changes=site_changes)


class CrawlabilityCheck(OnpageCheck):
    """Check: robots.txt rules, sitemap coverage, host redirect, crawl errors, orphans/depth, favicon, 404, pagination (deterministic)."""

    name = 'crawlability'
    skill = 'crawlability'
    uses_model = False
    depends_on = ()

    def run(self, ctx, prior, writer):
        return check_crawlability(ctx, prior or {}, writer)
