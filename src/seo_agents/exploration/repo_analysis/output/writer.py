"""Sharded exploration output (doc §11): small planner files + per-category shards + lazy evidence.

    seo_reports/exploration/
      manifest.json        schema versions, snapshot sha, framework, hosting, canonical host, lens/degraded banner,
                           output file list (sha, size, role, token estimate), coverage ledger, cost, timings
      summary.json         ranked issues[] -> fact refs + fix target. PLANNER INPUT (<= ~4k tokens)
      site.json            served facts on the canonical host, redirects/rewrites, hosting, deployment config
      entity.json          Person/Organization: name, role, sameAs, email, logo, og:image candidates
      routes.json          [{route_id, url, template, discovered_by, render, head_scope}]
      pages/<route_id>.json head FACTs, headings, content outline, link_ids, image_ids, word/H1 counts
      images.json / links.json   deduplicated, used_on[] route ids, edit pointers
      metadata.json        per route head FACTs + add_target + head recipe for this stack
      structured_data.json present blocks, recommended-but-absent types, insertion target
      performance.json     lab runs per route or {status: failed}; large assets
      keywords.json        top 20 terms per route (served/visible copy only)
      evidence/<ev_id>.json snippets + alternatives for an EDIT; never loaded by default
      schemas/*.schema.json JSON Schema per file (versioned $id)

Rules: compact JSON, null/empty/zero-count omitted, content-hashed ids (r_, img_, lnk_, iss_, ev_), repo-relative
paths only, every EDIT = {kind, file, line, col, pointer, attr, scope, template_n, shared_by, verify, evidence}.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
from urllib.parse import urlsplit

from seo_agents.exploration.repo_analysis.output.issues import build_issues
from seo_agents.exploration.repo_analysis.output.schema import SCHEMA_VERSION, SCHEMAS, schema_for, validate
from seo_agents.log import log

SUMMARY_TOKEN_BUDGET = 4000
MAX_EVIDENCE = 3000
EDIT_KEYS = ('kind', 'file', 'line', 'col', 'end_line', 'end_col', 'pointer', 'attr', 'prop', 'i18n_key', 'via', 'anchor',
             'insert', 'scope', 'template_n', 'in_loop', 'src_hint', 'verify', 'resolved_by', 'composed_from')
SOCIAL = re.compile(r'^https?://(?:www\.)?(?:linkedin\.com/(?:in|company)/|github\.com/[^/]+/?$|(?:twitter|x)\.com/(?!intent|share)[^/]+/?$|'
                    r'instagram\.com/[^/]+/?$|facebook\.com/(?!sharer)[^/]+/?$|youtube\.com/(?:@|c/|channel/)|medium\.com/@|'
                    r'dev\.to/[^/]+/?$|mastodon\.[^/]+/@|bsky\.app/profile/|threads\.net/@|dribbble\.com/[^/]+/?$|behance\.net/[^/]+/?$)', re.I)
CHROME = {'header', 'nav', 'footer', 'aside'}
STOP = set('the and for with your you are from that this our has have was were will can not all but get more about into out '
           'its it’s i’m i am me my we us they them their there here what when where which who how also just than then so '
           'les des une pour dans avec sur der die das und mit für von'.split())
RECIPES = {
    'next': 'App Router: export const metadata / generateMetadata in the route (title.template in app/layout), '
            'alternates.canonical, opengraph-image.tsx, JSON-LD <script> in the page. Pages Router: next/head.',
    'nuxt': 'useSeoMeta / useHead in the page; app.head in nuxt.config for site defaults.',
    'sveltekit': '<svelte:head> in +page.svelte with data from +page.ts load.',
    'remix': 'export const meta in the route module.',
    'astro': 'Pass title/description props to the layout <head>; canonical from Astro.url.',
    'gatsby': 'export const Head in the page (Gatsby Head API).',
    'angular': 'Title and Meta services in the routed component (plus prerender for crawlers).',
    'react-spa': 'Pre-render routes (vite-ssg/Vike/react-snap), then react-helmet-async per route. index.html head is shared by every route.',
    'vue-spa': 'Pre-render routes (vite-ssg), then @unhead/vue useHead per route. index.html head is shared by every route.',
    'svelte-spa': 'Pre-render routes, then <svelte:head> per route. index.html head is shared by every route.',
    'static-html': 'Edit each HTML file\'s own <head>.',
    'hugo': 'Front matter (title/description) + layouts/partials head partial.',
    'jekyll': 'Front matter + jekyll-seo-tag / _includes/head.html.',
    'eleventy': 'Front matter + base layout <head>.',
    'shopify-theme': 'Shopify admin SEO fields; layout/theme.liquid <head> renders page_title/page_description.',
    'wordpress-theme': 'SEO plugin fields per post/page, or wp_head hook / header.php for theme-wide tags.',
    'wordpress-block-theme': 'SEO plugin fields per post/page, or wp_head hook.',
    'django': '{% block title %} / {% block meta %} in the route template.',
    'laravel': "@section('title') / @push('meta') in the Blade view.",
    'rails': 'content_for :title / provide(:title) in the view; layout reads yield(:title).',
}


# ---------------------------------------------------------------- helpers
def h(text, n=10):
    return hashlib.sha1(str(text).encode()).hexdigest()[:n]


def route_id(page):
    return 'r_' + h(page['url'])


def compact(value):
    """Drop None, '', [], {} and zero counts recursively."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            item = compact(item)
            if item is None or item == '' or item == [] or item == {}:
                continue
            if item == 0 and not isinstance(item, bool) and (key == 'count' or key.endswith('_count')):
                continue
            out[key] = item
        return out
    if isinstance(value, list):
        return [compact(v) for v in value if v is not None]
    return value


def scrub(value, roots):
    if isinstance(value, dict):
        return {k: scrub(v, roots) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v, roots) for v in value]
    if isinstance(value, str):
        for root in roots:
            if root and root in value:
                value = value.replace(root + '/', '').replace(root, '.')
    return value


def dumps(obj, indent=None):
    if indent is None:
        return json.dumps(obj, ensure_ascii=False, separators=(',', ':'), default=str)
    return json.dumps(obj, ensure_ascii=False, indent=indent, default=str)


def tokens(text):
    return len(text) // 4


class Evidence:
    """Snippets for EDITs, deduplicated by file:line:pointer; ids are content hashes."""

    def __init__(self, files):
        self.files = files or {}
        self.records = {}

    def add(self, edit, alternatives=None):
        file, line = edit.get('file'), edit.get('line')
        if file not in self.files or not isinstance(line, int):
            return None
        ev = 'ev_' + h(f"{file}:{line}:{edit.get('pointer') or ''}")
        if ev not in self.records:
            if len(self.records) >= MAX_EVIDENCE:
                return None
            lines = self.files[file].splitlines()
            start = max(1, line - 3)
            self.records[ev] = {'ev_id': ev, 'file': file, 'line': line, 'start_line': start,
                                'snippet': [text[:300] for text in lines[start - 1:line + 3]],
                                'sha256': hashlib.sha256(self.files[file].encode()).hexdigest()[:16]}
        if alternatives:
            self.records[ev].setdefault('alternatives', alternatives[:10])
        return ev


class Builder:
    def __init__(self, model, files=None, deterministic=None):
        self.model = model
        self.site = model['site']
        self.pages = [p for p in model['pages'].values()]
        self.rid = {p['id']: route_id(p) for p in self.pages}
        self.evidence = Evidence(files)
        self.deterministic = deterministic or {}
        canonical = (self.site.get('served') or {}).get('canonical_host') or {}
        self.host = canonical.get('host') or urlsplit((self.site.get('serve') or {}).get('base_url') or '').hostname or 'site'
        fp = self.site.get('framework') or {}
        self.framework = fp.get('primary')

    # -- shapes
    def edit(self, edit, alternatives=None):
        if not isinstance(edit, dict) or not edit.get('kind'):
            return None
        out = {k: edit[k] for k in EDIT_KEYS if edit.get(k) not in (None, '', [], {})}
        if out['kind'] == 'cms':
            out['kind'] = 'platform'
        shared = [self.rid[p] for p in edit.get('shared_by') or [] if p in self.rid]
        if len(shared) > 1:
            out['shared_by'] = sorted(shared)
        ev = self.evidence.add(edit, alternatives)
        if ev:
            out['evidence'] = ev
        return out

    def method(self, method):
        if method == 'rendered':
            return 'rendered@' + self.host
        if method == 'raw-html':
            return 'raw@' + self.host
        if method in {'llm'}:
            return 'llm'
        return 'source' if method and method != 'none' else 'none'

    def fact(self, field, value_key='value'):
        if not isinstance(field, dict) or field.get(value_key) in (None, ''):
            return None
        out = {'value': field[value_key], 'method': self.method(field.get('method')),
               'conf': round(float(field.get('confidence') or 0), 2), 'edit': self.edit(field.get('edit'), field.get('candidates'))}
        if field.get('grounding'):
            out['grounding'] = field['grounding']
        return out

    # -- shards
    def routes(self):
        modes = {}
        for row in ((self.site.get('deterministic_signals') or {}).get('rendering_strategy') or {}).get('route_modes') or []:
            modes[row.get('route')] = row.get('modes') or []
        renderer = (self.site.get('framework') or {}).get('renderer')
        rows = []
        for page in sorted(self.pages, key=lambda p: p['url']):
            rendering = page.get('rendering') or {}
            route_modes = [m.lower() for m in modes.get(page.get('route'), [])]
            render = ('csr' if rendering.get('js_dependent') else next((m for m in ('ssg', 'isr', 'ssr', 'csr') if m in route_modes), None)
                      or {'csr': 'csr', 'ssr': 'ssr', 'ssg': 'ssg', 'static': 'static', 'hybrid': 'hybrid'}.get(renderer or '', 'unknown'))
            rows.append({'route_id': self.rid[page['id']], 'url': page['url'], 'route': page.get('route') if page.get('route') != page['url'] else None,
                         'template': page.get('template'), 'kind': page.get('kind'), 'instance_status': page.get('instance_status'),
                         'discovered_by': page.get('discovered_by'), 'render': render, 'head_scope': page.get('head_scope'),
                         'lens': page.get('lens'), 'http_status': (page.get('http') or {}).get('http_status'),
                         'click_depth': page['links'].get('click_depth'), 'in_count': page['links'].get('in_count'),
                         'orphan': page['links'].get('orphan_candidate') or None, 'file': 'pages/' + self.rid[page['id']] + '.json'})
        return {'schema': SCHEMA_VERSION, 'routes': rows}

    def images(self):
        by_key = {}
        page_ids = {}
        for page in self.pages:
            ids = []
            for image in page['images']:
                key = image.get('src') or (image.get('edit') or {}).get('file', '') + str((image.get('edit') or {}).get('line'))
                img = 'img_' + h(key)
                ids.append(img)
                record = by_key.get(img)
                if record is None:
                    alt = image.get('alt')
                    alt_edit = image.get('alt_edit') or {}
                    alt_status = ('missing' if alt is None else 'empty' if alt == '' else
                                  'expression' if alt_edit.get('kind') in {'computed', 'prop'} or image.get('alt_expression') else 'literal')
                    natural, rendered = image.get('natural') or [], image.get('rendered') or []
                    overserve = None
                    if natural and rendered and natural[0] and rendered[0]:
                        ratio = natural[0] / max(1, rendered[0])
                        overserve = {'1x': round(ratio, 2), '2x': round(ratio / 2, 2)}
                    record = {'img_id': img, 'src': image.get('src'), 'asset_file': image.get('asset_file'),
                              'bytes': image.get('transfer_bytes') or image.get('size_bytes'), 'natural': natural or image.get('intrinsic'),
                              'rendered': rendered, 'dpr_overserve': overserve, 'format': image.get('format'),
                              'loading': image.get('loading'), 'fetchpriority': image.get('fetchpriority'),
                              'dimensions_set': bool(image.get('width') and image.get('height')), 'srcset': image.get('srcset'),
                              'above_fold': image.get('above_fold'), 'alt': alt, 'alt_status': alt_status,
                              'alt_edit': self.edit(image.get('alt_edit')), 'src_edit': self.edit(image.get('edit')),
                              'section': image.get('section'), 'used_on': []}
                    by_key[img] = record
                if self.rid[page['id']] not in record['used_on']:
                    record['used_on'].append(self.rid[page['id']])
            page_ids[page['id']] = list(dict.fromkeys(ids))
        # LCP candidate: first above-the-fold image of a route (lazy-loading it delays LCP).
        for page in self.pages:
            first = next((i for i in page['images'] if i.get('above_fold')), None)
            record = by_key.get('img_' + h(first.get('src') or '')) if first is not None and first.get('src') else None
            if record is not None:
                record.setdefault('lcp_candidate_on', []).append(self.rid[page['id']])
        return {'schema': SCHEMA_VERSION, 'images': list(by_key.values())}, page_ids

    def link_kind(self, link):
        href = str(link.get('href') or '').strip()
        if href in {'', '#'} or href.startswith('javascript:'):
            return 'js-only'
        if href.startswith('#'):
            return 'anchor'
        if href.startswith(('mailto:', 'tel:', 'sms:')):
            return 'contact'
        if link.get('kind') == 'internal':
            return 'internal'
        host = (urlsplit(link.get('resolved') or href).hostname or '').lower()
        own = {self.host, ((self.site.get('served') or {}).get('canonical_host') or {}).get('requested', '').split('://')[-1]}
        if not host or host in own or host.removeprefix('www.') in {o.removeprefix('www.') for o in own if o}:
            return 'internal'
        return 'external'

    def links(self):
        by_key, page_ids = {}, {}
        for page in self.pages:
            ids = []
            for link in page['links']['out']:
                edit = link.get('edit') or {}
                key = f"{link.get('href')}|{link.get('anchor')}|{edit.get('file')}:{edit.get('line')}"
                lid = 'lnk_' + h(key)
                ids.append(lid)
                record = by_key.get(lid)
                if record is None:
                    record = {'link_id': lid, 'href': link.get('href'), 'resolved': link.get('resolved') if link.get('resolved') != link.get('href') else None,
                              'anchor': link.get('anchor'), 'kind': self.link_kind(link), 'rel': link.get('rel'), 'target': link.get('target'),
                              'status': link.get('status'), 'state': link.get('state'), 'redirect_chain': link.get('redirect_chain'), 'section': link.get('section'),
                              'in_main': link.get('in_main'), 'in_chrome': link.get('in_chrome'), 'href_edit': self.edit(edit), 'anchor_edit': self.edit(link.get('anchor_edit')),
                              'used_on': []}
                    by_key[lid] = record
                if self.rid[page['id']] not in record['used_on']:
                    record['used_on'].append(self.rid[page['id']])
            page_ids[page['id']] = list(dict.fromkeys(ids))
        return {'schema': SCHEMA_VERSION, 'links': list(by_key.values())}, page_ids

    def page(self, page, link_ids, image_ids):
        head = page['head']
        facts, absent = {}, []
        for key in ('title', 'description', 'canonical', 'robots', 'lang', 'viewport'):
            fact = self.fact(head.get(key))
            if fact:
                facts[key] = fact
            else:
                absent.append(key)
        outline = []
        for block in (page['content'].get('blocks') or [])[:80]:
            if block.get('tag', '').startswith('h') and block.get('tag')[1:].isdigit():
                continue
            outline.append({'tag': block.get('tag'), 'text': (block.get('text') or '')[:200], 'section': block.get('section'),
                            'edit': self.edit(block.get('edit'))})
        rendering = page.get('rendering') or {}
        return {'schema': SCHEMA_VERSION, 'route_id': self.rid[page['id']], 'url': page['url'], 'template': page.get('template'),
                'composition': page.get('composition'), 'head_scope': page.get('head_scope'), 'head': facts, 'absent': absent,
                'h1_count': sum(h.get('level') == 1 for h in page['headings']),
                'word_count': rendering.get('rendered_words') or page['content'].get('main_words'),
                'raw_html_words': rendering.get('raw_html_words'),
                'headings': [{'level': h_['level'], 'text': h_.get('text'), 'method': self.method(h_.get('method')),
                              'edit': self.edit(h_.get('edit'), h_.get('candidates')), 'grounding': h_.get('grounding')}
                             for h_ in page['headings']],
                'outline': outline, 'link_ids': link_ids, 'image_ids': image_ids,
                'rendering': {k: rendering.get(k) for k in ('method', 'js_dependent', 'title_requires_js', 'links_only_after_js')},
                'http': {k: (page.get('http') or {}).get(k) for k in ('http_status', 'final_url')},
                'eeat': {s['signal']: s['status'] for s in page.get('eeat') or []},
                'landmarks': page.get('landmarks'), 'eeat_assessment': page.get('eeat_assessment'), 'mobile_parity': page.get('mobile_parity'),
                'content_source': page.get('content_source'), 'limitations': page.get('limitations')}

    def recipe(self, page):
        try:
            from seo_agents.exploration.repo_analysis.tools.head_recipes import head_recipe
            value = head_recipe(self.site.get('framework') or {}, 'title', page)
            if value:
                return value
        except Exception:  # noqa: BLE001 - optional module owned elsewhere; fall back to the static table
            pass
        return RECIPES.get(self.framework or '', 'Add per-route tags in the page template <head>.')

    def metadata(self):
        routes = {}
        for page in self.pages:
            head = page['head']
            routes[self.rid[page['id']]] = {
                **{key: self.fact(head.get(key)) for key in ('title', 'description', 'canonical', 'robots', 'lang', 'viewport', 'keywords')},
                'og': {k: self.fact(v) for k, v in (head.get('og') or {}).items()},
                'twitter': {k: self.fact(v) for k, v in (head.get('twitter') or {}).items()},
                'hreflang': [{'hreflang': e.get('hreflang'), 'href': e.get('href'), 'edit': self.edit(e.get('edit'))} for e in head.get('hreflang') or []],
                'head_scope': page.get('head_scope'), 'add_target': self.edit(head.get('add_target')), 'recipe': self.recipe(page)}
        return {'schema': SCHEMA_VERSION, 'framework': self.framework, 'routes': routes}

    def structured_data(self):
        routes = {}
        for page in self.pages:
            present = [{'types': item.get('types'), 'valid': item.get('valid'), 'templated': item.get('templated'),
                        'edit': self.edit(item.get('edit'))} for item in page['head'].get('json_ld') or []]
            types = {t for item in present for t in item.get('types') or []}
            recommended = []
            if page['url'] == '/':
                recommended += ['WebSite'] + ([] if types & {'Person', 'Organization', 'LocalBusiness'} else ['Person|Organization'])
            if page['url'].count('/') >= 2:
                recommended.append('BreadcrumbList')
            if re.search(r'/(?:blog|posts?|articles?|news)/[^/]+$', page['url']):
                recommended.append('BlogPosting|Article')
            routes[self.rid[page['id']]] = {'present': present, 'absent_recommended': [t for t in recommended if not set(t.split('|')) & types],
                                            'insert': self.edit(page['head'].get('add_target')), 'head_scope': page.get('head_scope')}
        return {'schema': SCHEMA_VERSION, 'routes': routes,
                'rule': 'JSON-LD must mirror visible content; on a shared-shell head it applies to every route.'}

    def performance(self):
        routes = {}
        for page in self.pages:
            perf = page.get('performance')
            if perf:
                routes[self.rid[page['id']]] = perf
        large = ((self.deterministic.get('performance_web_vitals') or {}).get('large_assets') or [])[:30]
        ledger = (self.site.get('coverage') or {}).get('ledger') or {}
        states = Counter((row or {}).get('performance') for row in ledger.values())
        return {'schema': SCHEMA_VERSION, 'routes': routes, 'states': dict(states), 'large_assets': large,
                'method': 'lab (Lighthouse CLI median or PSI); missing routes were not measured'}

    def keywords(self):
        routes = {}
        for page in self.pages:
            parts = [page['head']['title'].get('value') or '', page['head']['description'].get('value') or '']
            parts += [h_.get('text') or '' for h_ in page['headings']]
            parts += [b.get('text') or '' for b in page['content'].get('blocks') or [] if b.get('section') not in CHROME]
            words = [w for w in re.findall(r"[^\W\d_][\w'’-]{2,}", ' '.join(parts).casefold()) if w not in STOP]
            grams = Counter(words) + Counter(' '.join(pair) for pair in zip(words, words[1:]))
            routes[self.rid[page['id']]] = [{'term': t, 'n': n} for t, n in grams.most_common(20) if n > 1 or len(grams) < 40][:20]
        return {'schema': SCHEMA_VERSION, 'routes': routes, 'method': 'lexical uni/bi-grams over title, description, headings and non-chrome copy'}

    def entity(self):
        names, roles, same_as, emails, logos, og_images = Counter(), Counter(), {}, {}, {}, {}
        for page in self.pages:
            head = page['head']
            for item in head.get('json_ld') or []:
                value = item.get('value') if isinstance(item.get('value'), dict) else {}
                if set(item.get('types') or []) & {'Person', 'Organization', 'LocalBusiness'} and value.get('name'):
                    names[value['name']] += 5
                    if value.get('jobTitle'):
                        roles[value['jobTitle']] += 5
                    for url in value.get('sameAs') or []:
                        same_as.setdefault(url, {'url': url, 'source': 'json-ld'})
            title = head['title'].get('value') or ''
            segments = [s.strip() for s in re.split(r'\s+[|\-–—·:]\s+', title) if s.strip()]
            if len(segments) > 1:
                names[segments[0]] += 1
                roles[segments[-1]] += 1
            site_name = (head.get('og') or {}).get('site_name') or {}
            if site_name.get('value'):
                names[site_name['value']] += 3
            image = (head.get('og') or {}).get('image') or {}
            if image.get('value'):
                og_images.setdefault(image['value'], {'src': image['value'], 'source': 'og:image', 'edit': self.edit(image.get('edit'))})
            for link in page['links']['out']:
                href = str(link.get('href') or '')
                if href.startswith('mailto:'):
                    emails.setdefault(href[7:].split('?')[0], {'email': href[7:].split('?')[0], 'edit': self.edit(link.get('edit'))})
                elif SOCIAL.search(href):
                    same_as.setdefault(href.rstrip('/'), {'url': href.rstrip('/'), 'source': 'link', 'anchor': link.get('anchor'),
                                                          'edit': self.edit(link.get('edit'))})
            for image in page['images']:
                src = str(image.get('src') or '')
                if re.search(r'logo|brand|avatar|portrait|profile|headshot', src, re.I):
                    logos.setdefault(src, {'src': src, 'kind': 'portrait' if re.search(r'avatar|portrait|profile|headshot', src, re.I) else 'logo',
                                           'natural': image.get('natural')})
                natural = image.get('natural') or []
                if natural and natural[0] and natural[0] >= 1200:
                    og_images.setdefault(src, {'src': src, 'source': 'large image', 'natural': natural,
                                               'note': 'og:image should be 1200x630; crop or generate one'})
        for row in ((self.deterministic.get('brand_entity_signals') or {}).get('same_as_urls') or [])[:20]:
            url = row if isinstance(row, str) else (row or {}).get('url')
            if url and SOCIAL.search(url):
                same_as.setdefault(url.rstrip('/'), {'url': url.rstrip('/'), 'source': 'json-ld/sameAs'})
        return {'schema': SCHEMA_VERSION, 'name': names.most_common(1)[0][0] if names else None,
                'name_candidates': [n for n, _ in names.most_common(5)], 'role_candidates': [r for r, _ in roles.most_common(3)],
                'same_as': list(same_as.values())[:20], 'email': list(emails.values())[:5], 'logo_candidates': list(logos.values())[:10],
                'og_image_candidates': list(og_images.values())[:10],
                'note': 'Candidates harvested deterministically; confirm identity with the owner before publishing schema.'}

    def site_shard(self):
        site = self.site
        keep = ('canonical_host', 'hosting', 'served', 'url_manifest', 'i18n', 'hreflang', 'duplicates', 'link_health', 'broken_links',
                'blocked_links', 'redirected_links', 'click_depth', 'orphan_candidates', 'crawl_errors', 'limitations', 'serve')
        out = {k: site.get(k) for k in keep}
        signals = site.get('deterministic_signals') or {}
        out['redirects'] = (signals.get('canonicals_redirects') or {}).get('redirects')
        out['deployment_config'] = (signals.get('code_exploration') or {}).get('deployment_config') or (site.get('hosting') or {}).get('deployment_config')
        out['rendering'] = {k: (signals.get('rendering_strategy') or {}).get(k) for k in ('project_modes', 'csr_only', 'interpretation')}
        served = dict(out.get('served') or {})
        served.pop('home_headers', None)  # kept on the in-memory model; security headers summarised below
        out['served'] = served
        out['schema'] = SCHEMA_VERSION
        return out

    def summary(self, issues):
        fp = self.site.get('framework') or {}
        body = {'schema': SCHEMA_VERSION, 'site': {'framework': fp.get('primary'), 'canonical_host': self.site.get('canonical_host'),
                                                   'hosting': (self.site.get('hosting') or {}).get('value'), 'routes': len(self.pages)},
                'issues': issues,
                'how_to_use': 'Assign issues by id. Each worker loads only the shard named in issue.facts (1-3k tokens). Apply an EDIT '
                              'only when edit.verify == "grounded"; otherwise open evidence/<edit.evidence>.json or escalate. '
                              'scope "shell"/"layout"/"template" edits change every route in shared_by.'}
        while tokens(dumps(compact(body))) > SUMMARY_TOKEN_BUDGET and len(body['issues']) > 5:
            body['issues'] = body['issues'][:-1]
            body['truncated'] = True
        return body


def manifest(builder, files, schema_errors, ref_errors, lens):
    site = builder.site
    fp = site.get('framework') or {}
    ledger = (site.get('coverage') or {}).get('ledger') or {}
    by_field = {}
    for row in ledger.values():
        for field, state in (row or {}).items():
            by_field.setdefault(field, Counter())[state] += 1
    absent = Counter()
    for page in builder.pages:
        for key in ('title', 'description', 'canonical', 'robots', 'lang', 'viewport'):
            if not (page['head'].get(key) or {}).get('value'):
                absent[key] += 1
    usage = site.get('model_usage') or {}
    return {
        'schema': SCHEMA_VERSION, 'schemas': {name: s['$id'] for name, s in SCHEMAS.items()},
        'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'snapshot_sha256': site.get('snapshot_sha256'),
        'framework': {k: fp.get(k) for k in ('primary', 'family', 'version', 'router', 'renderer', 'package_manager')} |
                     {'candidates': [c.get('name') for c in fp.get('candidates') or []][:5]},
        'hosting': site.get('hosting'), 'canonical_host': site.get('canonical_host'), 'lens': lens,
        'coverage': {'routes': len(builder.pages), 'measured': (site.get('coverage') or {}).get('measured'),
                     'source_only': (site.get('coverage') or {}).get('source_only'),
                     'fields': {f: dict(c) for f, c in by_field.items()}, 'absent': dict(absent)},
        'validation': {'site_model_issues': (site.get('validation') or {}).get('issue_count'),
                       'edit_verification': (site.get('validation') or {}).get('edit_verification'),
                       'schema_errors': schema_errors[:50], 'ref_errors': ref_errors[:50]},
        'gap_workers': {k: (site.get('gap_workers') or {}).get(k) for k in ('status', 'total', 'resolved', 'applied', 'escalated_to_mid')},
        'model_usage': {k: usage.get(k) for k in ('calls', 'input_tokens', 'output_tokens', 'cost_usd')},
        'timings': site.get('timings'), 'evidence_files': len(builder.evidence.records),
        'files': files,
        'read_order': ['manifest.json', 'summary.json', 'routes.json', 'one shard per task', 'evidence/<ev_id>.json only if needed'],
    }


def referential_errors(shards, evidence_ids):
    routes = {row['route_id'] for row in shards['routes.json']['routes']}
    links = {row['link_id'] for row in shards['links.json']['links']}
    images = {row['img_id'] for row in shards['images.json']['images']}
    errors = []

    def walk(obj, path):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in {'used_on', 'shared_by', 'routes', 'lcp_candidate_on'} and isinstance(value, list):
                    errors.extend(f'{path}/{key}: unknown route {r}' for r in value if isinstance(r, str) and r.startswith('r_') and r not in routes)
                elif key == 'evidence' and isinstance(value, str) and value not in evidence_ids:
                    errors.append(f'{path}/evidence: missing {value}')
                else:
                    walk(value, f'{path}/{key}')
        elif isinstance(obj, list):
            for index, value in enumerate(obj):
                walk(value, f'{path}/{index}')
    for name, shard in shards.items():
        walk(shard, name)
        if name.startswith('pages/'):
            errors.extend(f'{name}: unknown link {i}' for i in shard.get('link_ids') or [] if i not in links)
            errors.extend(f'{name}: unknown image {i}' for i in shard.get('image_ids') or [] if i not in images)
    for rid in routes:
        if f'pages/{rid}.json' not in shards:
            errors.append(f'routes.json: no page shard for {rid}')
    return errors


def build_exploration(model, *, files=None, deterministic=None, roots=()):
    """Return ({relative path: object}, {ev_id: object}, manifest-without-files) for the sharded layout."""
    builder = Builder(model, files=files, deterministic=deterministic)
    images, image_ids = builder.images()
    links, link_ids = builder.links()
    shards = {'routes.json': builder.routes(), 'images.json': images, 'links.json': links, 'metadata.json': builder.metadata(),
              'structured_data.json': builder.structured_data(), 'performance.json': builder.performance(),
              'keywords.json': builder.keywords(), 'entity.json': builder.entity(), 'site.json': builder.site_shard()}
    for page in builder.pages:
        shards[f"pages/{builder.rid[page['id']]}.json"] = builder.page(page, link_ids[page['id']], image_ids[page['id']])
    shards['summary.json'] = builder.summary(build_issues(model, builder.rid, hosting=model['site'].get('hosting')))
    cleaned = {}
    for name, shard in shards.items():
        out = compact(scrub(shard, roots))
        for key in ('issues', 'routes', 'images', 'links'):
            if key in shard and key not in out:
                out[key] = [] if isinstance(shard[key], list) else {}
        cleaned[name] = out
    shards = cleaned
    evidence = {ev: compact(scrub(record, roots)) for ev, record in builder.evidence.records.items()}
    return shards, evidence, builder


def write_exploration(model, out_dir, *, root=None, files=None, deterministic=None):
    """Write the sharded exploration layout; returns the manifest dict. Validates every file against its
    JSON Schema and checks referential integrity across shards (problems are recorded in manifest.json)."""
    directory = Path(out_dir)
    roots = [str(Path(root).resolve()), str(root)] if root else []
    roots += [str(directory.parent.parent.resolve())] if root is None else []
    shards, evidence, builder = build_exploration(model, files=files, deterministic=deterministic, roots=[r for r in roots if len(r) > 1])
    schema_errors = []
    for name, shard in shards.items():
        schema = schema_for(name)
        if schema:
            schema_errors += [f'{name}{e}' for e in validate(shard, schema)]
    ref_errors = referential_errors(shards, set(evidence))
    if directory.exists():
        for sub in ('pages', 'evidence', 'schemas'):
            shutil.rmtree(directory / sub, ignore_errors=True)
    for sub in ('pages', 'evidence', 'schemas'):
        (directory / sub).mkdir(parents=True, exist_ok=True)
    listed = []

    def put(name, obj, role, indent=None):
        text = dumps(obj, indent) + '\n'
        (directory / name).write_text(text)
        listed.append({'path': name, 'sha256': hashlib.sha256(text.encode()).hexdigest()[:16], 'bytes': len(text.encode()),
                       'tokens': tokens(text), 'role': role})
    for name, shard in sorted(shards.items()):
        role = 'planner' if name in {'summary.json', 'routes.json'} else 'page' if name.startswith('pages/') else 'shard'
        put(name, shard, role, 1 if role == 'planner' else None)
    for name, schema in SCHEMAS.items():
        put(f'schemas/{name}.schema.json', schema, 'schema', 1)
    for ev, record in evidence.items():
        (directory / 'evidence' / (ev + '.json')).write_text(dumps(record) + '\n')
    served = (model['site'].get('serve') or {}).get('mode') or 'source-only'
    measured = (model['site'].get('coverage') or {}).get('measured') or 0
    lens = {'mode': served, 'measured_routes': measured, 'degraded': not measured,
            'banner': None if measured else ('DEGRADED: source-only. Nothing was served or rendered, so values are what the '
                                              'source declares (conditionals, mapped lists and runtime data are not observed). '
                                              'Treat every fact as a lead; re-run with --site-url or --build.')}
    data = compact(scrub(manifest(builder, listed, schema_errors, ref_errors, lens), roots))
    schema_errors += [f'manifest.json{e}' for e in validate(data, SCHEMAS['manifest'])]
    data.setdefault('validation', {})['schema_errors'] = schema_errors[:50]
    (directory / 'manifest.json').write_text(dumps(data, 1) + '\n')
    log(f'exploration output written: {directory} ({len(builder.pages)} routes, {len(shards)} files, {len(evidence)} evidence, '
        f'{len(schema_errors)} schema / {len(ref_errors)} reference errors)')
    return data


def load_exploration(directory, *, include=None):
    """Load the planner view (manifest + summary + routes) and, optionally, named shards ('links.json', 'pages/r_x.json').
    Evidence is never loaded here; read evidence/<ev_id>.json on demand."""
    directory = Path(directory)
    data = {}
    for name in ['manifest.json', 'summary.json', 'routes.json'] + list(include or []):
        path = directory / name
        if path.exists():
            data[name] = json.loads(path.read_text())
    return data


def load_evidence(directory, ev_id):
    path = Path(directory) / 'evidence' / (ev_id + '.json')
    return json.loads(path.read_text()) if path.exists() else None
