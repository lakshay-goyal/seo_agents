"""Site-wide on-page work after every page has run: platform changes (deduplicated across pages), cross-page
duplicates and shared-template changes, robots.txt / sitemap.xml / llms.txt, topic clusters."""

from __future__ import annotations

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.validation.content_validators import normalize


def qa_data(results):
    return ((results or {}).get('qa') or {}).get('data') or {}


def plan_value(results, key):
    return (((results or {}).get('page_plan') or {}).get('data') or {}).get(key)


def duplicates(per_page, key):
    seen = {}
    for page_id, results in per_page.items():
        text = plan_value(results, key)
        if text:
            seen.setdefault(normalize(text), []).append(page_id)
    return [{'value': k, 'pages': ids} for k, ids in seen.items() if len(ids) > 1]


SERVED_STATES = {'present', 'missing', 'spa-shell-fallback', 'redirect', 'error', 'invalid'}


def served_state(entry):
    """present | missing | spa-shell-fallback | redirect | error | invalid | None for a served robots.txt / sitemap.xml fact
    (site_served probe {value, http_status, content_type} or the older {status, content_type, kind} shape)."""
    if isinstance(entry, str):
        return entry if entry in SERVED_STATES else None
    if not isinstance(entry, dict):
        return None
    for key in ('value', 'kind', 'status_kind', 'state'):
        if isinstance(entry.get(key), str) and entry[key] in SERVED_STATES:
            return entry[key]
    status = entry.get('http_status') if isinstance(entry.get('http_status'), int) else entry.get('status')
    if not isinstance(status, int):
        status = entry.get('status_code') if isinstance(entry.get('status_code'), int) else None
    ctype = str(entry.get('content_type') or entry.get('content-type') or '').lower()
    if status == 200 and 'html' in ctype:
        return 'spa-shell-fallback'
    if status in (404, 410):
        return 'missing'
    if status in (301, 302, 303, 307, 308):
        return 'redirect'
    if isinstance(status, int) and status != 200:
        return 'error'
    return 'present' if status == 200 else None


def served_as_spa_html(entry):
    """robots.txt / sitemap.xml answered by the SPA catch-all (200 text/html) instead of the real file."""
    return served_state(entry) == 'spa-shell-fallback'


def sitemap_urls(contexts, any_ctx):
    """Selectable canonical URLs only: no route templates, no 3xx/4xx/5xx, no noindex, pages only (kind == 'page').
    When exploration measured any page (http_status set), only URLs measured as 200 are listed (guessed URLs that were
    never fetched are left out)."""
    pages = any_ctx.get('site_pages') if isinstance(any_ctx.get('site_pages'), dict) else {}
    measured = any(isinstance(r, dict) and r.get('http_status') is not None for r in pages.values())
    urls = set()
    for url, row in pages.items():
        row = row if isinstance(row, dict) else {}
        status = row.get('http_status')
        if not isinstance(url, str) or not url.startswith('/') or row.get('template') or row.get('noindex') \
                or row.get('redirects_to') or (isinstance(status, int) and status >= 300) or (measured and status != 200) \
                or (row.get('kind') or 'page') != 'page' \
                or any(ch in url for ch in '[]{}*:?#'):
            continue
        urls.add(url)
    if not urls:  # no site_pages in the context: the selected pages are already filtered the same way
        urls = {c['url'] for c in contexts.values() if isinstance(c.get('url'), str)
                and not ((c.get('site_pages') or {}).get(c['url']) or {}).get('noindex')}
    return sorted(urls)


def sitemap_elsewhere(robots_entries, contexts, any_ctx):
    """A sitemap exists at another location: robots.txt declares `Sitemap:` lines, or exploration discovered pages from a
    live sitemap. Then a 404 on /sitemap.xml is not a missing sitemap."""
    for entry in robots_entries:
        body = entry.get('body') if isinstance(entry, dict) else None
        if isinstance(body, str) and any(line.strip().lower().startswith('sitemap:') for line in body.splitlines()):
            return True
    pages = any_ctx.get('site_pages') if isinstance(any_ctx.get('site_pages'), dict) else {}
    if any(isinstance(r, dict) and r.get('in_sitemap') for r in pages.values()):
        return True
    return any('sitemap:live' in (c.get('discovered_by') or []) for c in contexts.values() if isinstance(c, dict))


def llms_txt_needed(gaps, served, site_urls):
    """Propose llms.txt when it is absent; never overwrite an existing one (a /llms.txt URL in the model or served
    'present'). With the `no-llms-txt` gap always propose; with no ai_search_readiness gap list (absent/empty) propose
    too; otherwise only when the served probe says missing / SPA fallback."""
    if any(isinstance(u, dict) and str(u.get('url') or '').rstrip('/') == '/llms.txt' for u in site_urls or []):
        return False
    state = served_state((served or {}).get('llms_txt'))
    if state == 'present':
        return False
    ai = gaps.get('ai_search_readiness')
    if isinstance(ai, list) and 'no-llms-txt' in ai:
        return True
    if ai is None:  # repo analysis did not assess AI readiness; an empty list means it found nothing missing
        return True
    return state in ('missing', 'spa-shell-fallback')


def build_site(contexts, per_page, *, origin=None, run_date=None):
    dupes = {'title': duplicates(per_page, 'title'), 'description': duplicates(per_page, 'description'), 'h1': duplicates(per_page, 'h1')}
    field_of = {'title': 'head.title', 'description': 'head.description', 'h1': 'headings.h1'}
    for kind, groups in dupes.items():
        for group in groups:
            for page_id in group['pages']:
                data = qa_data(per_page[page_id])
                for change in (data.get('changes') or []) + (data.get('changes_more') or []):
                    if change['field'] == field_of[kind] and normalize(str(change['value'])) == group['value']:
                        change['status'] = 'needs_input'
                        change['conflicts_with'] = sorted(set(change['conflicts_with']) | {f'page:{p}' for p in group['pages'] if p != page_id})

    # platform changes: one per id across all pages
    platform, shared = {}, {}
    for page_id, results in per_page.items():
        data = qa_data(results)
        for change in data.get('site_changes') or []:
            entry = platform.setdefault(change['id'], {**change, 'pages': []})
            entry['pages'].append(contexts[page_id]['url'])
        for change in (data.get('changes') or []) + (data.get('changes_more') or []):
            if (change.get('target') or {}).get('scope') in ('template', 'layout', 'shell', 'data'):
                shared.setdefault(change['id'], set()).add(page_id)
    shared_changes = [{'id': cid, 'pages': sorted(pages)} for cid, pages in shared.items() if len(pages) > 1]

    clusters = {}
    for page_id, results in per_page.items():
        intent = (results.get('intent') or {}).get('data') or {}
        cluster = intent.get('cluster') or {}
        url = contexts[page_id]['url']
        key = cluster.get('pillar_url') or url
        clusters.setdefault(key, {'pillar': key, 'pages': []})['pages'].append(
            {'url': url, 'role': cluster.get('role'), 'primary_query': intent.get('primary_query'), 'intent': intent.get('intent')})

    any_ctx = next(iter(contexts.values()), {}) if contexts else {}
    gaps = any_ctx.get('site_gaps') or {}
    served = any_ctx.get('site_served') or {}
    site_facts = any_ctx.get('site') or {}
    # the canonical origin resolved in the context; a local/preview origin is never written into robots/sitemap/llms files
    base = '' if any_ctx.get('origin_is_local') else (any_ctx.get('origin') or origin or '')
    urls = sorted({u['url'] for c in contexts.values() for u in c['site_urls']})
    public = 'public/' if any_ctx.get('recipe', {}).get('stack') in ('spa', 'next-app', 'next-pages', 'nuxt', 'astro', 'remix', 'sveltekit', 'angular') else ''
    changes, inputs = [], []
    robots_entry = site_facts.get('robots') or served.get('robots_txt')
    sitemap_entry = site_facts.get('sitemap') or served.get('sitemap_xml')
    robots_state, sitemap_state = served_state(robots_entry), served_state(sitemap_entry)
    robots_spa, sitemap_spa = robots_state == 'spa-shell-fallback', sitemap_state == 'spa-shell-fallback'
    if robots_state != 'present' and ('no-robots' in (gaps.get('sitemap_robots') or []) or robots_state in ('spa-shell-fallback', 'missing')):
        note = (' Today /robots.txt is answered by the SPA catch-all (200 text/html), so crawlers get no rules; a real file '
                'in the static output is served before the rewrite.') if robots_spa else ''
        changes.append(ops.make_change(None, 'site', 'site.robots_txt', 'SPA HTML' if robots_spa else None,
                                       'User-agent: *\nAllow: /\n\nSitemap: ' + (base or '[VERIFY: origin]') + '/sitemap.xml\n',
                                       target={'file': public + 'robots.txt', 'kind': 'add', 'scope': 'platform', 'verified': True,
                                               'key': 'file:robots.txt'},
                                       op='insert_node', impact='medium', confidence=0.9, change_id_='chg_robots_txt',
                                       rationale='robots.txt with the sitemap location.' + note))
    sitemap_needed = sitemap_state != 'present' and ('no-sitemap' in (gaps.get('sitemap_robots') or [])
                                                     or sitemap_state in ('spa-shell-fallback', 'missing', 'invalid'))
    if sitemap_needed and sitemap_state not in ('spa-shell-fallback', 'invalid') \
            and sitemap_elsewhere((robots_entry, served.get('robots_txt')), contexts, any_ctx):
        sitemap_needed = False  # /sitemap.xml 404s but a sitemap is declared in robots.txt / was crawled elsewhere
    if sitemap_needed:
        loc_base = base or '[VERIFY: origin]'
        entries = ''.join(f'  <url><loc>{loc_base}{u}</loc></url>\n' for u in sitemap_urls(contexts, any_ctx))
        note = (' Today /sitemap.xml returns the SPA HTML shell.' if sitemap_spa else
                ' Today /sitemap.xml is not a valid urlset.' if sitemap_state == 'invalid' else '')
        changes.append(ops.make_change(None, 'site', 'site.sitemap_xml', 'SPA HTML' if sitemap_spa else None,
                                       '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                                       + entries + '</urlset>\n',
                                       target={'file': public + 'sitemap.xml', 'kind': 'add', 'scope': 'platform', 'verified': True,
                                               'key': 'file:sitemap.xml'},
                                       op='insert_node', impact='medium', confidence=0.85, change_id_='chg_sitemap_xml',
                                       rationale='XML sitemap of every indexable route found by exploration (no lastmod: there is no '
                                                 'reliable modification date in the JSON).' + note))
    name, lines = None, []
    want_llms = llms_txt_needed(gaps, served, [u for c in contexts.values() for u in c.get('site_urls') or []])
    for page_id, results in per_page.items():
        intent = (results.get('intent') or {}).get('data') or {}
        name = name or intent.get('brand_name')
        t = plan_value(results, 'title') or contexts[page_id]['title']
        d = plan_value(results, 'description') or contexts[page_id]['description']
        lines.append(f'- [{t}]({base}{contexts[page_id]["url"]})' + (f': {d}' if d else ''))
    for u in urls:
        if not any(contexts[p]['url'] == u for p in contexts):
            lines.append(f'- [{u.strip("/").title() or "Home"}]({base}{u})')
    home = next((pid for pid, c in contexts.items() if c['is_home']), None)
    summary = (plan_value(per_page.get(home, {}), 'description') if home else None) or (contexts[home]['description'] if home else '') or ''
    if want_llms:
        changes.append(ops.make_change(None, 'site', 'site.llms_txt', None, f'# {name or "Site"}\n\n> {summary}\n\n## Pages\n\n' + '\n'.join(lines) + '\n',
                                       target={'file': public + 'llms.txt', 'kind': 'add', 'scope': 'platform', 'verified': True, 'key': 'file:llms.txt'},
                                       op='insert_node', impact='low', confidence=0.7, change_id_='chg_llms_txt',
                                       rationale='llms.txt (proposed convention, not a ranking factor): a concise map of the site for LLM agents'))
    platform_changes = sorted(platform.values(), key=lambda c: (c['impact'] != 'high', c['id']))
    return {'changes': platform_changes + changes, 'shared_template_changes': shared_changes, 'duplicates': dupes,
            'topic_clusters': list(clusters.values()), 'client_inputs': inputs,
            'apply_order': [c['id'] for c in platform_changes] + ['pages/*: proposed changes', 'pages/*: blocked changes after their depends_on']}


class SiteLevelBuilder:
    """Tool: site-level changes (platform/prerender, redirects, sitemap/robots/llms.txt, duplicates) across pages."""

    def build(self, contexts, per_page, *, origin=None, run_date=None):
        return build_site(contexts, per_page, origin=origin, run_date=run_date)
