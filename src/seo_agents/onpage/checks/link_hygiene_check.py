"""Check 15 (deterministic): link hygiene - internal nofollow, target=_blank without noopener, redirected / insecure /
non-canonical-host internal hrefs, hash-bang routes, self links, link counts, orphan pages and click depth."""

from __future__ import annotations

from urllib.parse import urljoin, urlsplit

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.validation import content_validators as v
from seo_agents.onpage.checks.check_helpers import result
from seo_agents.onpage.checks.base_check import OnpageCheck

# ====================================================================== 16. link hygiene (deterministic)
MAX_LINKS = 150
MAX_CLICK_DEPTH = 3


def rel_tokens(link):
    rel = (link or {}).get('rel')
    if isinstance(rel, (list, tuple)):
        rel = ' '.join(str(r) for r in rel if r)
    return [t for t in str(rel or '').lower().split() if t]


def safe_split(url):
    """urlsplit that never raises: malformed hrefs ('https://[object Object]/x') parse as an empty URL."""
    try:
        return urlsplit(url or '')
    except ValueError:
        return urlsplit('')


def bare_host(host):
    return (host or '').lower().removeprefix('www.')


def origin_parts(ctx):
    origin = ctx.get('origin') if isinstance(ctx.get('origin'), str) else ''
    parts = safe_split(origin) if origin else None
    return (parts.scheme.lower(), (parts.hostname or '').lower()) if parts and parts.hostname else (None, None)


def is_internal(link, ctx):
    """kind internal, or an absolute URL on the site's host (www / non-www and http / https count as the same site)."""
    if not isinstance(link, dict):
        return False
    if link.get('kind') == 'internal':
        return True
    href = link.get('href')
    if link.get('kind') == 'absolute' and isinstance(href, str):
        _, host = origin_parts(ctx)
        try:
            link_host = safe_split(href).hostname if href.startswith(('http://', 'https://', '//')) else None
        except ValueError:  # e.g. 'https://[object Object]/x'
            return False
        return bool(host and link_host and bare_host(link_host) == bare_host(host))
    return False


def needs_noopener(link):
    tokens = rel_tokens(link)
    return str((link or {}).get('target') or '').lower() == '_blank' and 'noopener' not in tokens and 'noreferrer' not in tokens


def internal_nofollow(link, ctx):
    return is_internal(link, ctx) and 'nofollow' in rel_tokens(link)


def path_of(url):
    try:
        parts = safe_split(url or '')
    except ValueError:
        return url or '/'
    return (parts.path or '/') + (('?' + parts.query) if parts.query else '') + (('#' + parts.fragment) if parts.fragment else '')


def same_page(href, ctx):
    path = safe_split(href or '').path or ''
    return bool(path) and path.rstrip('/') == str(ctx.get('url') or '').rstrip('/')


def final_location(link, ctx):
    """Final URL after the link's redirect chain (or the site pass's redirected_links), else None."""
    chain = [r for r in link.get('redirect_chain') or [] if isinstance(r, dict)]
    location = None
    if chain:
        base = chain[-1].get('url') or link.get('resolved') or link.get('href') or ''
        loc = chain[-1].get('location')
        location = urljoin(base, loc) if isinstance(loc, str) and loc else None
    if location:
        return location
    site = ctx.get('site') if isinstance(ctx.get('site'), dict) else {}
    keys = {k for k in (link.get('href'), link.get('resolved')) if isinstance(k, str) and k}
    for row in site.get('redirected_links') or []:
        if isinstance(row, str):  # site pass lists the redirecting URL only; the final URL is unknown
            continue
        if not isinstance(row, dict):
            continue
        source = next((row.get(k) for k in ('from', 'url', 'href', 'source') if isinstance(row.get(k), str)), None)
        target = next((row.get(k) for k in ('to', 'location', 'final', 'final_url', 'target') if isinstance(row.get(k), str)), None)
        if source and target and (source in keys or path_of(source) in keys):
            return target
    return None


def known_redirect(link, ctx):
    """True when the site pass lists this link's URL as redirecting but records no final URL (plain URL rows)."""
    site = ctx.get('site') if isinstance(ctx.get('site'), dict) else {}
    keys = {k for k in (link.get('href'), link.get('resolved')) if isinstance(k, str) and k}
    keys |= {path_of(k) for k in set(keys)}
    for row in site.get('redirected_links') or []:
        if isinstance(row, str) and (row in keys or path_of(row) in keys):
            return True
    return False


def canonical_href(href, ctx):
    """An absolute same-site href on the wrong scheme/host -> a root-relative path; anything else unchanged."""
    scheme, host = origin_parts(ctx)
    if not isinstance(href, str) or not href.startswith(('http://', 'https://', '//')) or not host:
        return href
    parts = safe_split(href if not href.startswith('//') else (scheme or 'https') + ':' + href)
    if bare_host(parts.hostname) != bare_host(host):
        return href
    if (parts.hostname or '').lower() == host and parts.scheme.lower() == scheme:
        return href  # already absolute on the canonical host: fine
    return path_of(href)


def check_link_hygiene(ctx, prior=None, writer=None):
    changes, advice = [], []
    note = lambda topic, text, impact='medium', evidence=None: advice.append(  # noqa: E731
        ops.advice(ctx, 'link_hygiene', topic, text, impact=impact, evidence=evidence))
    links = [l for l in ctx.get('links') or [] if isinstance(l, dict)]
    scheme, host = origin_parts(ctx)
    done = set()

    unplaced, shared, redirects_unknown = [], [], []

    def add(link, field, current, new, attr, rationale, confidence=0.8):
        key = (link.get('id') or link.get('href'), field)
        if key in done:
            return
        done.add(key)
        target = ops.target_of(link.get('edit'), attr=attr, confidence=link.get('confidence'))
        row = {'field': field, 'href': link.get('href'), 'current': current, 'expected': new}
        if target is None:  # no edit location: one advice for all of them (field-only dedup keys would collapse)
            unplaced.append(row)
            return
        if target.get('scope') in ('template', 'data'):  # a shared line/map renders every item: per-link edit is wrong
            shared.append(dict(row, file=target.get('file')))
            return
        changes.append(ops.make_change(ctx, 'link_hygiene', field, current, new, op='remove' if new is None else 'set_attr',
                                       confidence=confidence, target=target, rationale=rationale,
                                       extra={'link_id': link.get('id'), 'href': link.get('href')}))

    insecure_external, hashbang, self_links, blank_no_opener = [], [], [], []
    external_follow = external_nofollow = 0
    for link in links:
        href = link.get('href') if isinstance(link.get('href'), str) else ''
        if link.get('renders') is False or not href:
            continue
        internal = is_internal(link, ctx)
        tokens = rel_tokens(link)
        # ---- rel: drop nofollow on internal links (no rel tokens left -> remove the attribute)
        if internal and 'nofollow' in tokens:
            new_tokens = [t for t in tokens if t != 'nofollow']
            add(link, 'links.rel', ' '.join(tokens), ' '.join(new_tokens) or None, 'rel',
                'Internal links should pass link equity (no nofollow)', confidence=0.85)
        # target=_blank without noopener: browsers imply noopener since 2021 -> low-impact advice only
        if needs_noopener(link):
            blank_no_opener.append(href)
        if not internal and link.get('kind') == 'absolute':
            if 'nofollow' in tokens or 'sponsored' in tokens or 'ugc' in tokens:
                external_nofollow += 1
            else:
                external_follow += 1
            if scheme == 'https' and href.startswith('http://'):
                insecure_external.append(href)
        # ---- hash-bang / hash routes (internal / fragment links only)
        if (internal or link.get('kind') == 'fragment') and ('#!' in href or href.startswith(('#/', '/#/')) or '/#/' in href):
            hashbang.append(href)
        if not internal:
            continue
        # ---- href: follow redirects, use the canonical host / https (one change per link)
        final = final_location(link, ctx)
        if final is None and known_redirect(link, ctx):
            redirects_unknown.append(href)
        target_href = canonical_href(final or href, ctx)
        if final and isinstance(target_href, str) and not href.startswith(('http://', 'https://', '//')) \
                and target_href.startswith(('http://', 'https://', '//')) and host \
                and bare_host(safe_split(target_href).hostname) == bare_host(host):
            target_href = path_of(target_href)  # keep a relative href relative
        if isinstance(target_href, str) and target_href and target_href != href and not ('#!' in href or '/#/' in href):
            redirected = final is not None
            add(link, 'links.href', href, target_href, 'href',
                'Internal link goes through a redirect; link to the final URL directly' if redirected else
                'Internal link points at a non-canonical host/scheme (www/http); use the canonical root-relative path',
                confidence=0.75 if redirected else 0.8)
        if link.get('in_main') and link.get('kind') != 'fragment' and same_page(href, ctx) and not safe_split(href).fragment:
            self_links.append(href)

    if unplaced:
        note('links.no_edit_location', f'{len(unplaced)} link fix(es) have no edit location in the exploration data; apply them '
             'where these links are defined.', 'medium', {'links': unplaced[:15]})
    if shared:
        note('links.shared_template', f'{len(shared)} link fix(es) target a shared template/data map (one line renders every '
             'item); fix the data item or the template once for all items.', 'medium', {'links': shared[:15]})
    if redirects_unknown:
        note('links.redirected', f'{len(redirects_unknown)} internal link(s) go through a redirect (final URL not recorded); '
             'point them at the final URL directly.', 'medium', {'hrefs': sorted(set(redirects_unknown))[:10]})
    if blank_no_opener:
        note('links.noopener', f'{len(blank_no_opener)} target="_blank" link(s) lack rel="noopener"; modern browsers imply it, '
             'add it only for older-browser support.', 'low', {'hrefs': sorted(set(blank_no_opener))[:10]})
    if insecure_external:
        note('links.insecure', f'{len(insecure_external)} external link(s) use http:// on an https site; switch them to https:// where '
             'the target supports it.', 'low', {'hrefs': sorted(set(insecure_external))[:10]})
    if hashbang:
        note('links.hash_routes', 'Links use hash routes (#/ or #!); search engines ignore the fragment, so every such '
             'route is one URL. Switch the router to history (path) mode.', 'high', {'hrefs': sorted(set(hashbang))[:10]})
    if self_links:
        note('links.self_link', 'Main content links to this same page; use that spot to link to a related page instead.', 'low',
             {'hrefs': sorted(set(self_links))[:5]})
    if len(links) > MAX_LINKS:
        note('links.too_many', f'{len(links)} links on the page; beyond ~{MAX_LINKS} each passes little value and important '
             'links get diluted.', 'low', {'links': len(links)})

    # ---- internal links from main content
    main_internal = [l for l in links if l.get('in_main') and is_internal(l, ctx) and l.get('renders') is not False]
    has_content = bool(links) or bool(ctx.get('main_words'))
    main_known = any(l.get('in_main') is not None for l in links)  # without main/chrome attribution we cannot tell
    if has_content and main_known and not main_internal:
        note('links.no_internal', 'Main content has no internal links; link to 2-3 related pages with descriptive anchors.',
             'medium')

    # ---- duplicate anchors to different URLs
    by_anchor = {}
    for link in links:
        anchor = v.normalize(link.get('anchor') or '')
        href = link.get('resolved') or link.get('href')
        if anchor and isinstance(href, str) and link.get('kind') in ('internal', 'absolute'):
            by_anchor.setdefault(anchor, set()).add(path_of(href).rstrip('/') if is_internal(link, ctx) else href)
    ambiguous = {a: sorted(t)[:5] for a, t in by_anchor.items() if len(t) > 1}
    if ambiguous:
        note('links.duplicate_anchor', 'The same anchor text points to different URLs; each target deserves its own '
             'descriptive anchor.', 'low', {'anchors': dict(list(ambiguous.items())[:8])})

    # ---- site graph: orphan / click depth
    graph = ctx.get('link_graph') if isinstance(ctx.get('link_graph'), dict) else {}
    site = ctx.get('site') if isinstance(ctx.get('site'), dict) else {}
    rendering = ctx.get('rendering') if isinstance(ctx.get('rendering'), dict) else {}
    depth_max = (site.get('click_depth') or {}).get('max') if isinstance(site.get('click_depth'), dict) else None
    graph_empty = 'click_depth' in site and not (isinstance(depth_max, int) and depth_max >= 1)  # nothing beyond home
    orphan = graph.get('orphan_candidate') is True and not rendering.get('links_only_after_js') and not graph_empty
    if orphan and not ctx.get('is_home'):
        note('links.orphan', 'No other crawled page links here (orphan candidate); add internal links from the navigation or '
             'related pages so crawlers and users can reach it.', 'high', {'in_count': graph.get('in_count')})
    depth = graph.get('click_depth')
    if isinstance(depth, int) and depth > MAX_CLICK_DEPTH:
        note('links.click_depth', f'The page is {depth} clicks from the home page; keep important pages within '
             f'{MAX_CLICK_DEPTH} clicks.', 'medium', {'click_depth': depth})

    data = {'links': len(links), 'internal_in_main': len(main_internal), 'external_follow': external_follow,
            'external_nofollow': external_nofollow, 'hash_routes': len(hashbang), 'in_count': graph.get('in_count'),
            'click_depth': depth}
    return result(data=data, changes=changes, advice=advice)


class LinkHygieneCheck(OnpageCheck):
    """Check: Internal nofollow, _blank without noopener, redirected/non-canonical hrefs, hash routes, orphans, depth (deterministic)."""

    name = 'link_hygiene'
    skill = 'link-hygiene'
    uses_model = False
    depends_on = ()

    def run(self, ctx, prior, writer):
        return check_link_hygiene(ctx, prior or {}, writer)
