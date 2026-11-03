"""Check 16 (deterministic): indexability - robots meta / X-Robots-Tag directives, canonical edge cases, robots.txt, redirects.

`technical` owns the plain `technical.canonical` / `technical.robots` fixes; this check covers the conflicts and edge cases
around them. `robots_allows(body, path, agent)` and `robots_directives(text, agent)` are pure helpers shared by other checks.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.reports.exploration_reports import value
from seo_agents.onpage.checks.check_helpers import result
from seo_agents.onpage.checks.base_check import OnpageCheck

# URLs that are normally noindexed on purpose: never force `index` on them.
INTENTIONAL_NOINDEX = re.compile(r'(?i)(?:^|/)(?:404|not-found|page-not-found|thank-?you|thanks|search|cart|basket|checkout|'
                                 r'account|my-account|login|log-in|signin|sign-in|logout|admin|wp-admin|staging)(?:[/?#.]|$)')
VALUED = {'max-snippet', 'max-image-preview', 'max-video-preview', 'unavailable_after'}
DIRECTIVES = {'all', 'index', 'follow', 'noindex', 'nofollow', 'none', 'noarchive', 'nocache', 'nosnippet', 'indexifembedded',
              'notranslate', 'noimageindex', 'noodp', 'noydir'} | VALUED
PREVIEW_HOSTS = ('.vercel.app', '.netlify.app', '.pages.dev', '.web.app', '.firebaseapp.com', '.onrender.com')
CHECK = 'indexability'


# ------------------------------------------------------------------ pure helpers
def intentional_noindex(url):
    """True for URLs that are usually noindexed on purpose (404, thank-you, search, cart, checkout, account, login, admin)."""
    return bool(isinstance(url, str) and INTENTIONAL_NOINDEX.search(url))


def robots_directives(text, agent='googlebot'):
    """Meta robots / X-Robots-Tag text -> {directive: value} for `agent` (UA-prefixed parts such as `googlebot: noindex`
    apply only to that crawler; unprefixed parts apply to all). Flags map to ''."""
    out, current_ua, last = {}, None, None
    agent = (agent or '').lower()
    for part in str(text or '').split(','):
        part = part.strip()
        if not part:
            continue
        match = re.match(r'^([a-z0-9_.-]+)\s*:\s*(.*)$', part, re.I)
        if match and match.group(1).lower() not in DIRECTIVES:
            current_ua, part = match.group(1).lower(), match.group(2).strip()
            if not part:
                continue
        if current_ua not in (None, agent, '*'):
            continue
        name, _, rest = part.partition(':')
        name = name.strip().lower()
        if name in DIRECTIVES:
            out[name] = rest.strip()
            last = name
        elif last == 'unavailable_after':  # an RFC 850 date contains a comma
            out[last] = (out[last] + ', ' + part).strip(', ')
    return out


def is_noindex(directives):
    return 'noindex' in directives or 'none' in directives


def is_nofollow(directives):
    return 'nofollow' in directives or 'none' in directives


def header_robots_text(ctx):
    headers = (ctx.get('http') or {}).get('headers') or {}
    raw = headers.get('x-robots-tag') if isinstance(headers, dict) else None
    if isinstance(raw, (list, tuple)):
        raw = ', '.join(str(r) for r in raw)
    return raw if isinstance(raw, str) else ''


def meta_robots_text(ctx, agent='googlebot'):
    """head.robots plus `<meta name="googlebot">` from the raw meta list."""
    parts = [value((ctx.get('head') or {}).get('robots')) or '']
    for meta in ctx.get('meta_tags') or []:
        if isinstance(meta, dict) and str(meta.get('name') or '').lower() in (agent, 'robots') and meta.get('content'):
            if str(meta.get('name')).lower() == agent or not parts[0]:
                parts.append(str(meta['content']))
    return ', '.join(p for p in parts if isinstance(p, str) and p)


def page_noindex(ctx):
    """(meta directives, header directives, effective noindex)."""
    meta = robots_directives(meta_robots_text(ctx))
    header = robots_directives(header_robots_text(ctx))
    return meta, header, is_noindex(meta) or is_noindex(header)


def parse_date(text):
    text = str(text or '').strip()
    if not text:
        return None
    for parse in (lambda t: parsedate_to_datetime(t), lambda t: datetime.fromisoformat(t.replace('Z', '+00:00'))):
        try:
            return parse(text).date()
        except (TypeError, ValueError, IndexError):
            pass
    for fmt in ('%d %b %Y %H:%M:%S %Z', '%d %b %Y', '%A, %d-%b-%y %H:%M:%S %Z', '%d-%b-%y', '%Y-%m-%d'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    match = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})', text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    match = re.search(r'(\d{1,2})[ -]([A-Za-z]{3})[a-z]*[ -](\d{2,4})', text)
    if match:
        year = int(match.group(3))
        year += 2000 if year < 100 else 0
        try:
            return datetime.strptime(f'{match.group(1)} {match.group(2).title()} {year}', '%d %b %Y').date()
        except ValueError:
            return None
    return None


def robots_rules(body, agent='googlebot'):
    """[(allow, pattern)] of the group that applies to `agent` (its own group(s) override `*`)."""
    groups, agents, rules, in_agents = [], [], [], False
    for raw in str(body or '').splitlines():
        line = raw.split('#', 1)[0].strip()
        if ':' not in line:
            continue
        key, _, val = line.partition(':')
        key, val = key.strip().lower(), val.strip()
        if key == 'user-agent':
            if not in_agents and agents:
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(val.lower())
            in_agents = True
        elif key in ('allow', 'disallow'):
            in_agents = False
            if agents:
                rules.append((key == 'allow', val))
    if agents:
        groups.append((agents, rules))
    agent = (agent or '*').lower()
    if any(agent in names for names, _ in groups if agent != '*'):
        return [r for names, rs in groups if agent in names for r in rs]
    return [r for names, rs in groups if '*' in names for r in rs]


def _rule_regex(pattern):
    anchored = pattern.endswith('$')
    body = pattern[:-1] if anchored else pattern
    return re.compile(''.join('.*' if c == '*' else re.escape(c) for c in body) + ('$' if anchored else ''))


def robots_allows(body, path, agent='googlebot'):
    """Is `path` (path + optional query) crawlable for `agent` under robots.txt `body`? Longest match wins; Allow wins ties;
    `*` and `$` wildcards; an empty Disallow allows everything."""
    path = str(path or '/')
    if not path.startswith('/'):
        path = urlsplit(path).path or '/' if '://' in path else '/' + path
    if path == '/robots.txt':
        return True
    best = None  # (length, allow)
    for allow, pattern in robots_rules(body, agent):
        if not pattern:
            continue
        try:
            if not _rule_regex(pattern).match(path):
                continue
        except re.error:
            continue
        length = len(pattern)
        if best is None or length > best[0] or (length == best[0] and allow and not best[1]):
            best = (length, allow)
    return True if best is None else best[1]


def norm_url(url):
    """(scheme, host, path-without-trailing-slash, query) for comparing absolute URLs; None when not absolute."""
    if not isinstance(url, str) or not re.match(r'(?i)^https?://', url.strip()):
        return None
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or '').lower()
    except ValueError:  # e.g. 'https://[object Object]/x'
        return None
    return parts.scheme.lower(), host, (parts.path.rstrip('/') or '/'), parts.query


def path_of(url):
    if not isinstance(url, str) or not url:
        return None
    try:
        path = urlsplit(url.strip()).path if '://' in url or url.startswith('//') else url.split('?')[0].split('#')[0]
    except ValueError:
        return None
    return path.rstrip('/') or '/'


def link_header_canonical(headers):
    raw = (headers or {}).get('link') if isinstance(headers, dict) else None
    for part in str(raw or '').split(','):
        match = re.search(r'<([^>]+)>\s*;.*\brel\s*=\s*"?canonical"?', part, re.I)
        if match:
            return match.group(1).strip()
    return None


def _platform_change(*args, **kwargs):
    from seo_agents.onpage.checks.technical_check import platform_change  # lazy: technical_check imports this module
    return platform_change(*args, **kwargs)


def _headers_where(hosting):
    from seo_agents.onpage.checks.security_check import headers_where  # lazy: avoids an import cycle via technical_check
    return headers_where(hosting)


def bare_host(host):
    return re.sub(r'^www\.', '', (host or '').lower())


def googlebot_meta_findings(ctx, intentional, advice, changes, data):
    """`<meta name="googlebot" content="noindex">` (not googlebot-news): technical only rewrites `<meta name="robots">`."""
    if intentional:
        return
    found = [m for m in ctx.get('meta_tags') or [] if isinstance(m, dict) and str(m.get('name') or '').strip().lower() == 'googlebot'
             and is_noindex(robots_directives(m.get('content')))]
    if not found:
        return
    data['googlebot_noindex'] = True
    content = str(found[0].get('content') or '')
    target = ops.target_of(found[0].get('edit'), attr='content') if found[0].get('edit') else None
    if target:
        kept = [t.strip() for t in content.split(',') if t.strip() and t.strip().lower() not in ('noindex', 'none', 'index', 'all')]
        changes.append(ops.make_change(ctx, CHECK, 'indexability.googlebot_meta', content, ', '.join(['index'] + kept), op='set_attr',
                                       target=target, confidence=0.8, impact='high',
                                       rationale='<meta name="googlebot"> says noindex: Google drops a page meant to rank'))
        return
    advice.append(ops.advice(ctx, CHECK, 'indexability.googlebot_meta', f'<meta name="googlebot" content="{content}"> keeps Google '
                             'from indexing this page (other engines still index it). Remove the noindex from the googlebot meta tag.',
                             impact='high', evidence={'googlebot': content}))


# ------------------------------------------------------------------ check
def robots_findings(ctx, url, advice, changes, site_changes, data):
    meta, header, noindex = page_noindex(ctx)
    data.update(noindex=noindex, nofollow=is_nofollow(meta) or is_nofollow(header),
                directives={'meta': meta, 'x_robots_tag': header})
    intentional = intentional_noindex(url)
    if noindex and intentional:
        data['intentional_noindex'] = True
    directives = {**header, **meta}
    if 'none' in directives and not intentional:
        advice.append(ops.advice(ctx, CHECK, 'indexability.none', '`none` means `noindex, nofollow`: the page is dropped from the '
                                 'index and its links are not followed. Remove it on pages meant to rank.', impact='high',
                                 evidence={'meta': meta_robots_text(ctx), 'x_robots_tag': header_robots_text(ctx)}))
    elif ('nofollow' in directives) and not intentional:
        advice.append(ops.advice(ctx, CHECK, 'indexability.nofollow', 'Page-level `nofollow` stops link equity flowing to every '
                                 'link on the page (including internal navigation). Use rel="nofollow" on individual links instead.',
                                 impact='high', evidence={'meta': meta_robots_text(ctx), 'x_robots_tag': header_robots_text(ctx)}))
    snippet = directives.get('max-snippet')
    if 'nosnippet' in directives or (snippet is not None and not re.fullmatch(r'-1|[1-9]\d*', snippet.strip())):
        advice.append(ops.advice(ctx, CHECK, 'indexability.snippet', 'Snippet is suppressed (`nosnippet` / `max-snippet:0` or an '
                                 'invalid value): search and AI answers cannot quote this page. Use `max-snippet:-1`.',
                                 evidence={'nosnippet': 'nosnippet' in directives, 'max-snippet': snippet}))
    preview = (directives.get('max-image-preview') or '').strip().lower()
    if preview in ('none', 'standard'):
        advice.append(ops.advice(ctx, CHECK, 'indexability.image_preview', f'`max-image-preview:{preview}` limits image previews in '
                                 'Discover and results; use `max-image-preview:large`.', impact='low'))
    if 'noimageindex' in directives:
        advice.append(ops.advice(ctx, CHECK, 'indexability.noimageindex', '`noimageindex` keeps this page\'s images out of image '
                                 'search; remove it unless that is intended.', impact='low'))
    if 'unavailable_after' in directives:
        expires = parse_date(directives['unavailable_after'])
        try:
            today = date.fromisoformat(str(ctx.get('run_date'))[:10])
        except ValueError:
            today = date.today()
        if expires is not None and expires <= today:
            advice.append(ops.advice(ctx, CHECK, 'indexability.unavailable_after', f'`unavailable_after: '
                                     f'{directives["unavailable_after"]}` is in the past, so the page is being removed from the '
                                     'index. Remove or update the directive.', impact='high', evidence={'date': str(expires)}))
    if meta and header and (is_noindex(meta) != is_noindex(header) or is_nofollow(meta) != is_nofollow(header)):
        advice.append(ops.advice(ctx, CHECK, 'indexability.robots_conflict', 'Meta robots and the X-Robots-Tag header disagree; '
                                 'Google applies the most restrictive. Make them match.',
                                 evidence={'meta': meta_robots_text(ctx), 'x_robots_tag': header_robots_text(ctx)}))
    googlebot_meta_findings(ctx, intentional, advice, changes, data)
    # Also when meta says noindex too: technical removes the meta, the header must go as well.
    if is_noindex(header) and not intentional:
        host = (urlsplit(ctx.get('origin') or '').hostname or '').lower()
        if host.endswith(PREVIEW_HOSTS) or ctx.get('origin_is_local'):
            advice.append(ops.advice(ctx, CHECK, 'indexability.x_robots_tag', f'The X-Robots-Tag header says noindex on {host or "this origin"} '
                                     '(preview/dev deployments add it). Verify the production domain does not send it.',
                                     evidence={'x_robots_tag': header_robots_text(ctx)}))
        else:
            hosting = ctx.get('hosting')
            site_changes.append(_platform_change(
                'platform.x_robots_tag', header_robots_text(ctx), 'Do not send `X-Robots-Tag: noindex` for pages meant to rank',
                where=_headers_where(hosting), hosting=hosting, change_id='chg_x_robots_tag', impact='high',
                rationale='The server/CDN adds a noindex header; the HTML has no noindex, so the fix is in the hosting config'))
    return meta, header, noindex, intentional


def canonical_findings(ctx, url, noindex, advice, changes, data):
    head = ctx.get('head') or {}
    canonical = value(head.get('canonical'))
    canonical = canonical.strip() if isinstance(canonical, str) else None
    origin = ctx.get('origin')
    origin_parts = norm_url(origin) if origin else None
    count = head.get('canonical_count')
    if isinstance(count, int) and count > 1:
        hrefs = [l.get('href') for l in ctx.get('head_links') or [] if isinstance(l, dict)
                 and str(l.get('rel') or '').strip().lower() == 'canonical']
        advice.append(ops.advice(ctx, CHECK, 'indexability.canonical_duplicate', f'The page has {count} rel=canonical tags '
                                 '(often a framework default plus an SEO plugin); Google ignores all of them when they disagree. '
                                 'Keep exactly one.', impact='high', evidence={'canonical_count': count, 'hrefs': hrefs[:10]}))
    if not canonical:
        return
    absolute = norm_url(canonical)
    # technical rewrites the canonical (technical.canonical) whenever the production origin is known and the page has no
    # meta noindex; form advice here only covers the cases it cannot fix.
    meta_noindex = is_noindex(robots_directives(meta_robots_text(ctx)))
    technical_fixes = bool(origin_parts) and not ctx.get('origin_is_local') and not meta_noindex  # local includes preview hosts
    if absolute is None and not technical_fixes:
        advice.append(ops.advice(ctx, CHECK, 'indexability.canonical_relative', 'The canonical URL is relative or protocol-relative; '
                                 'use an absolute https URL on the canonical host.', evidence={'canonical': canonical}))
    if ('?' in canonical or '#' in canonical) and not technical_fixes:
        advice.append(ops.advice(ctx, CHECK, 'indexability.canonical_query', 'The canonical carries a query string or fragment; '
                                 'point it at the clean URL.', evidence={'canonical': canonical}))
    if absolute and origin_parts and not ctx.get('origin_is_local') and not technical_fixes:
        if bare_host(absolute[1]) != bare_host(origin_parts[1]):
            advice.append(ops.advice(ctx, CHECK, 'indexability.canonical_cross_domain', f'The canonical points at another host '
                                     f'({absolute[1]}), handing this page\'s ranking to it. Only intended for syndicated copies.',
                                     impact='high', evidence={'canonical': canonical, 'origin': origin}))
        if absolute[0] == 'http' and origin_parts[0] == 'https':
            advice.append(ops.advice(ctx, CHECK, 'indexability.canonical_http', 'The canonical uses http:// while the site is served '
                                     'over https://; use the https URL.', impact='high', evidence={'canonical': canonical}))
    target_path = path_of(canonical)
    own_path = path_of(url)
    same_host = absolute is None or origin_parts is None or bare_host(absolute[1]) == bare_host(origin_parts[1])
    if target_path and own_path and target_path != own_path and same_host:
        if noindex:
            advice.append(ops.advice(ctx, CHECK, 'indexability.noindex_canonical', 'The page is noindex but canonicalises to another '
                                     'URL: conflicting signals. Use one: a canonical to the preferred URL (consolidate) or noindex.',
                                     evidence={'canonical': canonical}))
        pages = ctx.get('site_pages') if isinstance(ctx.get('site_pages'), dict) else {}
        entry = pages.get(target_path) or pages.get(target_path + '/') or pages.get(target_path.rstrip('/') or '/')
        if isinstance(entry, dict):
            status = entry.get('http_status')
            problems = [p for p in (f'returns {status}' if isinstance(status, int) and status != 200 else None,
                                    'is noindex' if entry.get('noindex') else None,
                                    f'redirects to {entry["redirects_to"]}' if entry.get('redirects_to') else None) if p]
            if problems:
                advice.append(ops.advice(ctx, CHECK, 'indexability.canonical_target', f'The canonical target {target_path} '
                                         f'{", ".join(problems)}; a canonical must point at an indexable 200 URL.', impact='high',
                                         evidence={'canonical': canonical, 'target': entry}))
    og_url = value(((head.get('og') or {}).get('url')) if isinstance(head.get('og'), dict) else None)
    if isinstance(og_url, str) and og_url.strip() and absolute and norm_url(og_url) and norm_url(og_url)[1:3] != absolute[1:3]:
        advice.append(ops.advice(ctx, CHECK, 'indexability.canonical_og_url', 'og:url differs from the canonical; keep them identical '
                                 'so shares consolidate on one URL.', impact='low', evidence={'canonical': canonical, 'og:url': og_url}))
    header_canonical = link_header_canonical((ctx.get('http') or {}).get('headers'))
    if header_canonical and path_of(header_canonical) != target_path:
        advice.append(ops.advice(ctx, CHECK, 'indexability.canonical_header', 'The HTTP Link rel=canonical header and the HTML '
                                 'canonical disagree; Google may ignore both.', evidence={'html': canonical, 'header': header_canonical}))
    data['canonical'] = canonical


def robots_txt_findings(ctx, url, noindex, advice, data):
    robots = (ctx.get('site_served') or {}).get('robots_txt') or {}
    if not isinstance(robots, dict) or robots.get('value') != 'present' or not isinstance(robots.get('body'), str):
        return
    allowed = robots_allows(robots['body'], url or '/', 'googlebot')
    data['robots_txt_allowed'] = allowed
    if allowed:
        return
    if noindex:
        advice.append(ops.advice(ctx, CHECK, 'indexability.robots_txt_noindex', 'robots.txt blocks this URL, so crawlers never see its '
                                 'noindex; the URL can still be indexed from links. Allow crawling if it must drop out of the index.',
                                 evidence={'url': url}))
    else:
        advice.append(ops.advice(ctx, CHECK, 'indexability.robots_txt_blocked', 'Blocked by robots.txt while indexable: Googlebot '
                                 'cannot crawl this page (it may appear without a snippet). Remove or narrow the Disallow rule.',
                                 impact='high', evidence={'url': url, 'agent': 'googlebot'}))


def redirect_findings(ctx, advice, data):
    http = ctx.get('http') or {}
    status = http.get('status')
    if status == 'redirect-limit':
        advice.append(ops.advice(ctx, CHECK, 'indexability.redirect_loop', 'The page redirect chain loops or exceeds the hop limit; '
                                 'crawlers give up. Redirect directly to the final URL.', impact='high',
                                 evidence={'redirect_chain': http.get('redirect_chain')}))
    elif status == 'redirect-without-location':
        advice.append(ops.advice(ctx, CHECK, 'indexability.redirect_no_location', 'The page answers with a redirect status but no '
                                 'Location header; crawlers cannot follow it.', impact='high', evidence={'http_status': http.get('http_status')}))
    chain = [r for r in http.get('redirect_chain') or [] if isinstance(r, dict)]
    data['redirect_hops'] = len(chain)
    if len(chain) > 1 and status != 'redirect-limit':
        advice.append(ops.advice(ctx, CHECK, 'indexability.redirect_chain', f'{len(chain)} redirect hops before the page; collapse '
                                 'them into a single 301/308 to the final URL and update internal links.',
                                 evidence={'redirect_chain': [f"{r.get('url')} -> {r.get('http_status')}" for r in chain]}))
    final = http.get('final_url')
    if isinstance(final, str) and final and ctx.get('absolute_url') and norm_url(final) and norm_url(ctx['absolute_url']) \
            and norm_url(final)[1:3] != norm_url(ctx['absolute_url'])[1:3]:
        data['final_url'] = final


def check_indexability(ctx, prior=None, writer=None):
    changes, advice, site_changes, data = [], [], [], {}
    url = ctx.get('url') or '/'
    _meta, _header, noindex, _intentional = robots_findings(ctx, url, advice, changes, site_changes, data)
    canonical_findings(ctx, url, noindex, advice, changes, data)
    robots_txt_findings(ctx, url, noindex, advice, data)
    redirect_findings(ctx, advice, data)
    data.setdefault('intentional_noindex', False)
    return result(data=data, changes=changes, advice=advice, site_changes=site_changes)


class IndexabilityCheck(OnpageCheck):
    """Check: robots meta / X-Robots-Tag directives, canonical edge cases, robots.txt blocking, redirect loops/chains (deterministic)."""

    name = 'indexability'
    skill = 'indexability'
    uses_model = False
    depends_on = ()

    def run(self, ctx, prior, writer):
        return check_indexability(ctx, prior or {}, writer)
