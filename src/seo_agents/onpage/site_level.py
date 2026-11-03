"""Site-wide on-page work after every page has run: platform changes (deduplicated across pages), cross-page
duplicates and shared-template changes, robots.txt / sitemap.xml / llms.txt, topic clusters."""

from __future__ import annotations

from seo_agents.onpage import ops
from seo_agents.onpage.validators import normalize


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


def served_as_spa_html(entry):
    """robots.txt / sitemap.xml answered by the SPA catch-all (200 text/html) instead of the real file."""
    if not isinstance(entry, dict):
        return entry == 'spa-shell-fallback'
    kind = str(entry.get('kind') or entry.get('status_kind') or '')
    ctype = str(entry.get('content_type') or entry.get('content-type') or '')
    return kind == 'spa-shell-fallback' or (entry.get('status') == 200 and 'html' in ctype.lower())


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
    base = origin or any_ctx.get('origin') or ''
    urls = sorted({u['url'] for c in contexts.values() for u in c['site_urls']})
    public = 'public/' if any_ctx.get('recipe', {}).get('stack') in ('spa', 'next-app', 'next-pages', 'nuxt', 'astro', 'remix', 'sveltekit', 'angular') else ''
    changes, inputs = [], []
    robots_entry = site_facts.get('robots') or served.get('robots_txt')
    sitemap_entry = site_facts.get('sitemap') or served.get('sitemap_xml')
    robots_spa, sitemap_spa = served_as_spa_html(robots_entry), served_as_spa_html(sitemap_entry)
    if 'no-robots' in (gaps.get('sitemap_robots') or []) or robots_spa:
        note = (' Today /robots.txt is answered by the SPA catch-all (200 text/html), so crawlers get no rules; a real file '
                'in the static output is served before the rewrite.') if robots_spa else ''
        changes.append(ops.make_change(None, 'site', 'site.robots_txt', 'SPA HTML' if robots_spa else None,
                                       'User-agent: *\nAllow: /\n\nSitemap: ' + (base or '[VERIFY: origin]') + '/sitemap.xml\n',
                                       target={'file': public + 'robots.txt', 'kind': 'add', 'scope': 'platform', 'verified': True,
                                               'key': 'file:robots.txt'},
                                       op='insert_node', impact='medium', confidence=0.9, change_id_='chg_robots_txt',
                                       rationale='robots.txt with the sitemap location.' + note))
    if 'no-sitemap' in (gaps.get('sitemap_robots') or []) or sitemap_spa:
        entries = ''.join(f'  <url><loc>{base}{u}</loc></url>\n' for u in urls)
        note = ' Today /sitemap.xml returns the SPA HTML shell.' if sitemap_spa else ''
        changes.append(ops.make_change(None, 'site', 'site.sitemap_xml', 'SPA HTML' if sitemap_spa else None,
                                       '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                                       + entries + '</urlset>\n',
                                       target={'file': public + 'sitemap.xml', 'kind': 'add', 'scope': 'platform', 'verified': True,
                                               'key': 'file:sitemap.xml'},
                                       op='insert_node', impact='medium', confidence=0.85, change_id_='chg_sitemap_xml',
                                       rationale='XML sitemap of every indexable route found by exploration (no lastmod: there is no '
                                                 'reliable modification date in the JSON).' + note))
    name, lines = None, []
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
    changes.append(ops.make_change(None, 'site', 'site.llms_txt', None, f'# {name or "Site"}\n\n> {summary}\n\n## Pages\n\n' + '\n'.join(lines) + '\n',
                                   target={'file': public + 'llms.txt', 'kind': 'add', 'scope': 'platform', 'verified': True, 'key': 'file:llms.txt'},
                                   op='insert_node', impact='low', confidence=0.7, change_id_='chg_llms_txt',
                                   rationale='llms.txt (proposed convention, not a ranking factor): a concise map of the site for LLM agents'))
    platform_changes = sorted(platform.values(), key=lambda c: (c['impact'] != 'high', c['id']))
    return {'changes': platform_changes + changes, 'shared_template_changes': shared_changes, 'duplicates': dupes,
            'topic_clusters': list(clusters.values()), 'client_inputs': inputs,
            'apply_order': [c['id'] for c in platform_changes] + ['pages/*: proposed changes', 'pages/*: blocked changes after their depends_on']}
