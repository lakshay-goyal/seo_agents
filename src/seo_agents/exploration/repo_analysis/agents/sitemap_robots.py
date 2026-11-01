"""Sitemap/robots sources (static files and generators) plus parsed robots groups and sitemap URLs."""

import re
import xml.etree.ElementTree as ET

from seo_agents.exploration.repo_analysis.tools.skill_runner import records_of, run_skill


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


def check_sitemap_and_robots(index):
    result = run_skill(index, 'sitemap-robots')
    robots = []
    for record in records_of(result, 'robots-file'):
        path = record['location']['file']
        if path in index.files:
            robots.append({'file': path, 'record_id': record['id'], **parse_robots(index.files[path])})
    sitemaps = []
    for record in records_of(result, 'sitemap-file'):
        path = record['location']['file']
        if path not in index.files:
            continue
        try:
            root = ET.fromstring(index.files[path])
            urls = [e.text.strip() for e in root.iter() if e.tag.rsplit('}', 1)[-1] == 'loc' and e.text]
            sitemaps.append({'file': path, 'record_id': record['id'], 'type': root.tag.rsplit('}', 1)[-1],
                             'url_count': len(urls), 'urls': urls[:500]})
        except ET.ParseError as error:
            sitemaps.append({'file': path, 'record_id': record['id'], 'parse_error': str(error)})
    result['robots_files'] = robots
    result['sitemap_files'] = sitemaps
    return result
