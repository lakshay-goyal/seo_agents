"""Render a validated article as Markdown (frontmatter + body) and JSON-LD (BlogPosting, FAQPage, BreadcrumbList).

Formatting is produced here, never by the model: bullets are always "- item", numbered steps "1. item", tables are
pipe tables with a header separator, diagrams are fenced ```mermaid blocks, images are ![alt](src "caption"), and
[S#] citations become numbered links to the Sources list.
"""

from __future__ import annotations

from datetime import date
import json
import re

from seo_agents.onpage.content.article_rules import CITATION

LINK = re.compile(r'<a\s+href="([^"]+)"\s*>(.*?)</a>', re.I | re.S)


def inline(text, numbering, sources):
    text = LINK.sub(lambda m: f'[{m.group(2).strip()}]({m.group(1)})', text or '')
    text = re.sub(r'</?strong>', '**', text)
    text = re.sub(r'</?em>', '*', text)
    text = re.sub(r'</?code>', '`', text)

    def cite(match):
        url = sources.get(match.group(1), {}).get('url', '#sources')   # one number per source URL, not per passage
        if url not in numbering:
            numbering[url] = len(numbering) + 1
        return f'[[{numbering[url]}]]({url})'
    return CITATION.sub(cite, text).strip()


def human_date(iso):
    d = date.fromisoformat(iso[:10])
    return d.strftime('%B ') + str(d.day) + d.strftime(', %Y')


def block_md(b, numbering, sources):
    tag = b['tag']
    if tag in ('p',):
        return inline(b['text'], numbering, sources)
    if tag == 'blockquote':
        return '> ' + inline(b['text'], numbering, sources)
    if tag == 'h3':
        return '### ' + b['text']
    if tag == 'ul':
        return '\n'.join('- ' + inline(i, numbering, sources) for i in b['items'])
    if tag == 'ol':
        return '\n'.join(f'{n}. ' + inline(i, numbering, sources) for n, i in enumerate(b['items'], 1))
    if tag == 'table':
        head = '| ' + ' | '.join(inline(h, numbering, sources) for h in b['header']) + ' |'
        sep = '| ' + ' | '.join('---' for _ in b['header']) + ' |'
        rows = ['| ' + ' | '.join(inline(c, numbering, sources) for c in row) + ' |' for row in b['rows']]
        return '\n'.join([f'*{inline(b["caption"], numbering, sources)}*', '', head, sep] + rows)
    if tag == 'figure':
        return f'![{b["alt"]}]({b["src"]} "{b["caption"]}")\n*{inline(b["caption"], numbering, sources)}*'
    if tag == 'image_brief':
        return (f'<!-- IMAGE TO CREATE: /images/{b["filename"]} - {b["prompt"]} -->\n'
                f'![{b["alt"]}](/images/{b["filename"]} "{b["caption"]}")\n*{inline(b["caption"], numbering, sources)}*')
    if tag == 'diagram':
        return f'```mermaid\n{b["code"]}\n```\n*{inline(b["caption"], numbering, sources)}* <!-- alt: {b["alt"]} -->'
    return ''


def yaml_value(value):
    return json.dumps(value, ensure_ascii=False)


def to_markdown(article, sources, *, run_date, brand=None):
    by_id = {s['id']: s for s in sources}
    numbering = {}
    body = [f'# {article["h1"]}', '', f'*Last updated: {human_date(run_date)}*', '',
            inline(article['intro'], numbering, by_id), '', '**Key takeaways**', '']
    body += ['- ' + inline(i, numbering, by_id) for i in article['key_takeaways']]
    for s in article['sections']:
        body += ['', '## ' + s['heading'], '']
        for b in s['blocks']:
            body += [block_md(b, numbering, by_id), '']
    if article.get('faq'):
        body += ['', '## Frequently asked questions', '']
        for q in article['faq']:
            body += ['### ' + q['question'], '', inline(q['answer'], numbering, by_id), '']
    if article.get('tag_links'):
        body += ['', '**Tags:** ' + ' · '.join(f'[{t["tag"]}]({t["url"]})' for t in article['tag_links'])]
    if numbering:
        body += ['', '## Sources', '']
        by_url = {}
        for item in sources:
            by_url.setdefault(item['url'], item)
        for url, n in sorted(numbering.items(), key=lambda kv: kv[1]):
            s = by_url.get(url) or {'url': url}
            dated = f', published/updated {s["date"]}' if s.get('date') else ''
            body += [f'{n}. [{s.get("title") or s.get("url")}]({s.get("url")}){dated}, accessed {run_date}']
    front = {'title': article['title'], 'description': article['meta_description'], 'slug': article['slug'],
             'url': article['url'], 'h1': article['h1'], 'category': article['category'], 'tags': article['tags'],
             'primary_keyword': article.get('primary_keyword'), 'author': brand, 'date_published': run_date,
             'date_modified': run_date,
             'images': [{'src': i['src'], 'alt': i['alt']} for i in article.get('images') or []]}
    head = ['---'] + [f'{k}: {yaml_value(v)}' for k, v in front.items() if v is not None] + ['---', '']
    return '\n'.join(head + body).rstrip() + '\n'


def json_ld(article, *, origin, run_date, brand=None):
    url = (origin or '').rstrip('/') + article['url']
    images = [(origin or '').rstrip('/') + i['src'] for i in article.get('images') or [] if i['src'].startswith('/')]
    graph = [{'@type': 'BlogPosting', '@id': url + '#article', 'headline': article['h1'][:110], 'description': article['meta_description'],
              'url': url, 'mainEntityOfPage': url, 'datePublished': run_date, 'dateModified': run_date,
              'keywords': ', '.join(article['tags']), 'articleSection': article['category'], 'wordCount': article.get('word_count'),
              **({'image': images} if images else {}),
              **({'author': {'@type': 'Organization' if brand else 'Person', 'name': brand},
                  'publisher': {'@type': 'Organization', 'name': brand}} if brand else {})}]
    if article.get('faq'):
        graph.append({'@type': 'FAQPage', 'mainEntity': [{'@type': 'Question', 'name': q['question'],
                                                          'acceptedAnswer': {'@type': 'Answer', 'text': q['answer']}}
                                                         for q in article['faq']]})
    parts = [p for p in article['url'].strip('/').split('/') if p]
    crumbs = [{'@type': 'ListItem', 'position': 1, 'name': 'Home', 'item': (origin or '').rstrip('/') + '/'}]
    path = ''
    for i, part in enumerate(parts, 2):
        path += '/' + part
        crumbs.append({'@type': 'ListItem', 'position': i, 'name': article['h1'] if i == len(parts) + 1 else part.title(),
                       'item': (origin or '').rstrip('/') + path})
    graph.append({'@type': 'BreadcrumbList', 'itemListElement': crumbs})
    return {'@context': 'https://schema.org', '@graph': graph}


class ArticleRenderer:
    """Tool: validated article -> Markdown file text + JSON-LD."""

    to_markdown = staticmethod(to_markdown)
    json_ld = staticmethod(json_ld)
