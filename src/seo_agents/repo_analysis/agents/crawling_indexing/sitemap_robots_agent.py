"""Sitemap/robots sources (static files, generators, templates) plus parsed robots groups, sitemap URLs
and per-source robots directives (meta robots, X-Robots-Tag, framework metadata) with their values."""

import re

from seo_agents.repo_analysis.tools.parsing.safe_xml_parser import XMLParseError, safe_xml_fromstring
from seo_agents.repo_analysis.tools.signal_profiles.signal_profile_runner import records_of, run_signal_profile
from seo_agents.repo_analysis.agents.base_agent import RepoAnalysisAgent

TEMPLATE_SYNTAX = re.compile(r'\{\{|\{%|<\?php|<%|\A---\s*\n|\A\+\+\+\s*\n')


def parse_robots(text):
    groups, current = [], None
    sitemaps = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split('#', 1)[0].strip()
        match = re.match(r'^([\w-]+)\s*:\s*(.*)$', line)
        if not match:
            continue
        key, value = match.group(1).lower(), match.group(2).strip()
        if key == 'user-agent':
            if current is None or current['rules']:
                current = {'user_agents': [], 'rules': [], 'line': number}
                groups.append(current)
            current['user_agents'].append(value)
        elif key in {'allow', 'disallow', 'crawl-delay'} and current is not None:
            current['rules'].append({'directive': key, 'value': value, 'line': number})
        elif key == 'sitemap':
            sitemaps.append({'url': value, 'line': number})
    return {'groups': groups, 'sitemaps': sitemaps,
            'blocks_all': any('*' in g['user_agents'] and any(r['directive'] == 'disallow' and r['value'] == '/'
                                                             for r in g['rules']) for g in groups)}


def robots_policy_for(parsed, agent):
    """'allow' | 'disallow' | 'partial' | None for a user agent, from parsed robots groups (most specific group wins)."""
    agent = agent.lower()
    specific = [g for g in parsed['groups'] if any(u.lower() == agent for u in g['user_agents'])]
    groups = specific or [g for g in parsed['groups'] if '*' in g['user_agents']]
    if not groups:
        return None
    rules = [r for g in groups for r in g['rules'] if r['directive'] in {'allow', 'disallow'}]
    if any(r['directive'] == 'disallow' and r['value'] == '/' for r in rules):
        return 'disallow' if not any(r['directive'] == 'allow' and r['value'] for r in rules) else 'partial'
    if any(r['directive'] == 'disallow' and r['value'] for r in rules):
        return 'partial'
    return 'allow'


def parse_sitemap(text):
    root = safe_xml_fromstring(text)
    kind = root.tag.rsplit('}', 1)[-1]
    urls, children, lastmod, hreflang, images = [], [], 0, 0, 0
    for element in root.iter():
        tag = element.tag.rsplit('}', 1)[-1]
        if tag == 'loc' and element.text:
            urls.append(element.text.strip())
        elif tag == 'lastmod':
            lastmod += 1
        elif tag == 'link' and element.get('hreflang'):
            hreflang += 1
        elif tag == 'image':
            images += 1
    if kind == 'sitemapindex':
        children, urls = urls, []
    return {'type': kind, 'url_count': len(urls), 'urls': urls[:500], 'urls_truncated': len(urls) > 500,
            'children': children[:200], 'lastmod_entries': lastmod, 'hreflang_entries': hreflang, 'image_entries': images}


def robots_directives(result):
    """[{source, value, noindex, nofollow, file, line}] for every robots directive with a captured value."""
    directives = []
    for source, signal in (('meta-robots', 'meta-robots'), ('x-robots-tag', 'x-robots-tag'), ('metadata', 'metadata-robots')):
        for record in records_of(result, signal):
            value = (record.get('value') or '').strip()
            lowered = value.lower().replace(' ', '')
            noindex = 'noindex' in lowered or lowered in {'none'} or 'index:false' in lowered
            directives.append({'source': source, 'value': value or None, 'noindex': noindex,
                               'nofollow': 'nofollow' in lowered or 'follow:false' in lowered,
                               'file': record['location']['file'], 'line': record['location']['line_start']})
    return directives


def check_sitemap_and_robots(index):
    result = run_signal_profile(index, 'sitemap-robots')
    robots = []
    for record in records_of(result, 'robots-file'):
        path = record['location']['file']
        if path not in index.files:
            continue
        text = index.files[path]
        entry = {'file': path, 'record_id': record['id']}
        if TEMPLATE_SYNTAX.search(text):
            entry['generated'] = 'template'
        robots.append({**entry, **parse_robots(text)})
    sitemaps = []
    for record in records_of(result, 'sitemap-file'):
        path = record['location']['file']
        if path.endswith('.gz') or path not in index.files:
            sitemaps.append({'file': path, 'record_id': record['id'], 'compressed': path.endswith('.gz'),
                             'note': 'Compressed or binary sitemap; contents not parsed.'})
            continue
        text = index.files[path]
        if TEMPLATE_SYNTAX.search(text):
            sitemaps.append({'file': path, 'record_id': record['id'], 'generated': 'template',
                             'note': 'Sitemap is rendered from a template at build time; URLs come from the site content.'})
            continue
        try:
            sitemaps.append({'file': path, 'record_id': record['id'], **parse_sitemap(text)})
        except XMLParseError as error:
            sitemaps.append({'file': path, 'record_id': record['id'], 'parse_error': str(error)})
    result['robots_files'] = robots
    result['sitemap_files'] = sitemaps
    result['robots_directives'] = robots_directives(result)
    result['noindex_sources'] = [d for d in result['robots_directives'] if d['noindex']]
    return result


class SitemapRobotsAgent(RepoAnalysisAgent):
    """Agent: Sitemap/robots sources (static files, generators, templates) plus parsed robots groups, sitemap URLs and per-source robots directives (meta robots, X-Robots-Tag, framework metadata) with their values."""

    name = 'sitemap_robots'
    description = 'Sitemap/robots sources (static files, generators, templates) plus parsed robots groups, sitemap URLs and per-source robots directives (meta robots, X-Robots-Tag, framework metadata) with their values.'
    signal_profile = 'sitemap-robots'
    requires = ()

    def run(self, index, results):
        return check_sitemap_and_robots(index)
