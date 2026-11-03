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
from seo_agents.onpage.structured_data import schema_facts as facts
from seo_agents.onpage.structured_data import schema_nodes as nodes
from seo_agents.onpage.structured_data import schema_rules as rules

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


def article_visible_text(article):
    """Normalized text the rendered article shows (FAQ/HowTo markup must mirror it)."""
    rows = [article.get('h1'), article.get('title'), article.get('intro')] + list(article.get('key_takeaways') or [])
    for s in article.get('sections') or []:
        rows.append(s.get('heading'))
        for b in s.get('blocks') or []:
            rows += [b.get('text'), b.get('caption')] + list(b.get('items') or [])
    for q in article.get('faq') or []:
        rows += [q.get('question'), q.get('answer')]
    return ' \n '.join(rules.norm(rules.plain_text(r)) for r in rows if isinstance(r, str) and r)


def json_ld(article, *, origin, run_date, brand=None, site_type=None, lang=None):
    """Strict @id graph for a new article: WebPage, BlogPosting, WebSite, Person/Organization publisher, ImageObjects,
    BreadcrumbList, FAQPage (mirrors the visible FAQ) and HowTo (when a step-by-step ordered list is shown).
    Invalid optional nodes are pruned (see `schema_rules`)."""
    origin = (origin or '').rstrip('/')
    url = origin + article['url']
    entity_type = 'Person' if site_type == 'personal' else 'Organization'
    id_of = nodes.ids(origin, url, entity_type)
    image_nodes = [nodes.image_object(f'{url}#image-{n}', {'url': origin + i['src']}, caption=i.get('alt'))
                   for n, i in enumerate((i for i in article.get('images') or [] if i['src'].startswith('/')), 1)]
    graph = [
        nodes.web_page('WebPage', id_of['webpage'], url=url, name=article.get('title') or article['h1'],
                       description=rules.plain_text(article['meta_description']), website_id=id_of['website'], lang=lang,
                       breadcrumb_id=id_of['breadcrumb'], image_id=image_nodes[0]['@id'] if image_nodes else None,
                       main_entity=nodes.ref(id_of['article']), published=run_date, modified=run_date),
        nodes.article('BlogPosting', id_of['article'], url=url, webpage_id=id_of['webpage'], title=article['h1'],
                      description=rules.plain_text(article['meta_description']), author=nodes.ref(id_of['entity']) if brand else None,
                      publisher_id=id_of['entity'] if brand else None, lang=lang,
                      image_ids=[n['@id'] for n in image_nodes], published=run_date, modified=run_date,
                      word_count=article.get('word_count'), section=article.get('category'),
                      keywords=', '.join(article.get('tags') or []) or None),
    ]
    if brand:
        graph.append(nodes.website(id_of['website'], origin=origin, name=brand, publisher_id=id_of['entity'], lang=lang))
        graph.append(nodes.entity(entity_type, id_of['entity'], name=brand, url=origin + '/'))
    else:  # no publisher name: keep the page in a WebSite without inventing one
        graph[0].pop('isPartOf', None)
    graph += image_nodes
    if article.get('faq'):
        graph.append(nodes.faq_page(id_of['faq'], url=url, questions=article['faq'], website_id=id_of['website'], lang=lang))
        if not brand:
            graph[-1].pop('isPartOf', None)
    how = facts.steps({}, {'sections': article.get('sections') or []},
                      how_to=bool(re.search(r'(?i)\bhow to\b|step[- ]by[- ]step', (article.get('title') or '') + ' ' + article['h1'])))
    if how:
        graph.append(nodes.how_to(id_of['howto'], name=how['name'] or article['h1'], url=url, steps=how['steps'], lang=lang,
                                  image_id=image_nodes[0]['@id'] if image_nodes else None))
    parts = [p for p in article['url'].strip('/').split('/') if p]
    crumbs, path = [('Home', origin + '/')], ''
    for i, part in enumerate(parts, 1):
        path += '/' + part
        crumbs.append((article['h1'] if i == len(parts) else part.replace('-', ' ').title(), origin + path))
    graph.append(nodes.breadcrumbs(id_of['breadcrumb'], crumbs))
    data, _issues, _dropped = rules.prune({'@context': rules.CONTEXT, '@graph': graph},
                                          visible=article_visible_text(article),
                                          keep={id_of['webpage'], id_of['article'], id_of['breadcrumb']})
    return data


class ArticleRenderer:
    """Tool: validated article -> Markdown file text + JSON-LD."""

    to_markdown = staticmethod(to_markdown)
    json_ld = staticmethod(json_ld)
