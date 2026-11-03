"""Apply approved articles to a client app whose blog is static Markdown (opt-in, run by a human after review).

    python -m seo_agents.onpage.content.article_publisher <seo_reports/onpage/articles> <app checkout>
           [--blog-dir public/blog] [--public-dir public] [--index blog.json]

For every article whose status is `done` and fact check is `pass`:
    <blog-dir>/<slug>.md        the rendered article (frontmatter, body, tags, sources)
    <blog-dir>/<slug>.ld.json   JSON-LD (BlogPosting, FAQPage, BreadcrumbList) for the page head
    <blog-dir>/<index>          {"<category>": [{title, link, date, description, tags, linkFrom}]} - the blog index,
                                tag pages (/blog/tags/<tag>) and "related articles" links on existing pages read it
    <public-dir>/<image>.svg    an illustration for every image the article references that the app does not have yet
                                (title, caption and alt drawn as a designed card; replace with a real image when ready)

The on-page workflow itself never edits the client (proposals only); this tool is the explicit apply step.
"""

from __future__ import annotations

import argparse
from datetime import date
import html
import json
from pathlib import Path

PALETTES = (('#eef2ff', '#6366f1', '#1e1b4b'), ('#ecfdf5', '#10b981', '#064e3b'), ('#fff7ed', '#f97316', '#431407'),
            ('#f0f9ff', '#0ea5e9', '#0c4a6e'), ('#fdf2f8', '#ec4899', '#500724'))


def wrap(text, width):
    lines = ['']
    for word in str(text or '').split():
        if len(lines[-1]) + len(word) + 1 > width and lines[-1]:
            lines.append(word)
        else:
            lines[-1] = (lines[-1] + ' ' + word).strip()
    return lines


def illustration(image, article, index=0):
    """A designed SVG card for an image the article needs: topic, caption and the alt text (also the aria-label)."""
    bg, accent, ink = PALETTES[index % len(PALETTES)]
    title = wrap(image.get('caption') or article.get('h1'), 30)[:3]
    alt = wrap(image.get('alt'), 58)[:2]
    tags = (article.get('tags') or [])[:4]
    rows = ''.join(f'<text x="96" y="{250 + i * 64}" font-size="52" font-weight="600" fill="{ink}">{html.escape(t)}</text>'
                   for i, t in enumerate(title))
    sub = ''.join(f'<text x="96" y="{250 + len(title) * 64 + 40 + i * 38}" font-size="28" fill="{ink}" opacity=".7">'
                  f'{html.escape(t)}</text>' for i, t in enumerate(alt))
    chips, x = '', 96
    for tag in tags:
        w = 24 + len(tag) * 15
        chips += (f'<rect x="{x}" y="560" width="{w}" height="44" rx="22" fill="{accent}" opacity=".15"/>'
                  f'<text x="{x + w / 2}" y="590" font-size="22" text-anchor="middle" fill="{ink}">#{html.escape(tag)}</text>')
        x += w + 14
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 675" role="img" aria-label="{html.escape(image["alt"])}">'
            f'<title>{html.escape(image["alt"])}</title><rect width="1200" height="675" fill="{bg}"/>'
            f'<circle cx="1040" cy="120" r="220" fill="{accent}" opacity=".12"/><circle cx="1110" cy="560" r="140" fill="{accent}" opacity=".18"/>'
            f'<rect x="96" y="120" width="96" height="10" rx="5" fill="{accent}"/>'
            f'<g font-family="Inter,system-ui,-apple-system,sans-serif">{rows}{sub}{chips}</g></svg>\n')


def human_date(iso):
    d = date.fromisoformat(iso[:10])
    return d.strftime('%B ') + str(d.day) + d.strftime(', %Y')


def publish(articles_dir, app_dir, *, blog_dir='public/blog', public_dir='public', index='blog.json'):
    articles, app = Path(articles_dir), Path(app_dir)
    blog, public = app / blog_dir, app / public_dir
    blog.mkdir(parents=True, exist_ok=True)
    index_path = blog / index
    catalog = json.loads(index_path.read_text()) if index_path.exists() else {}
    report = {'applied': [], 'skipped': [], 'images': []}
    for f in sorted(articles.glob('*.json')):
        if f.name == 'index.json':
            continue
        record = json.loads(f.read_text())
        verdict = (record.get('fact_check') or {}).get('verdict')
        if record.get('status') != 'done' or verdict != 'pass':
            report['skipped'].append({'slug': f.stem, 'status': record.get('status'), 'fact_check': verdict})
            continue
        article = record['article']
        md = (articles / (f.stem + '.md')).read_text()
        for n, image in enumerate(article.get('images') or []):
            src = image['src']
            if (public / src.lstrip('/')).exists() and not image.get('new'):
                continue                                   # the app already has this file
            svg = src.rsplit('.', 1)[0] + '.svg'
            target = public / svg.lstrip('/')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(illustration(image, article, n))
            md = md.replace(src, svg)
            report['images'].append(svg)
        (blog / (f.stem + '.md')).write_text(md)
        (blog / (f.stem + '.ld.json')).write_text(json.dumps(record['json_ld'], indent=2, ensure_ascii=False) + '\n')
        entry = {'title': article['title'], 'link': article['url'], 'date': human_date(article['last_updated']),
                 'description': article['meta_description'], 'tags': article['tags'],
                 'linkFrom': [r for r in article.get('link_from') or [] if r['url'] != article['url'].rsplit('/', 1)[0]]}
        for items in catalog.values():
            items[:] = [i for i in items if i.get('link') != article['url']]
        catalog.setdefault(article['category'], []).append(entry)
        report['applied'].append(article['url'])
    index_path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + '\n')
    return report


class ArticlePublisher:
    """Tool: apply approved (done + fact-check pass) articles to a static-Markdown blog in the client app."""

    publish = staticmethod(publish)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m seo_agents.onpage.content.article_publisher')
    parser.add_argument('articles_dir')
    parser.add_argument('app_dir')
    parser.add_argument('--blog-dir', default='public/blog')
    parser.add_argument('--public-dir', default='public')
    parser.add_argument('--index', default='blog.json')
    args = parser.parse_args(argv)
    print(json.dumps(publish(args.articles_dir, args.app_dir, blog_dir=args.blog_dir, public_dir=args.public_dir,
                             index=args.index), indent=2))


if __name__ == '__main__':
    main()
