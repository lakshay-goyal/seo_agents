"""Typed content blocks -> semantic HTML (escaped; only inline <a>, <strong>, <em>, <code> survive)."""

from __future__ import annotations

import html
import json
import re

INLINE = re.compile(r'&lt;(/?)(strong|em|code)&gt;|&lt;a href=&quot;([^&"]+)&quot;\s*&gt;|&lt;/a&gt;')


def inline(text):
    escaped = html.escape(text or '', quote=True)

    def restore(match):
        if match.group(2):
            return '<' + match.group(1) + match.group(2) + '>'
        if match.group(3):
            return '<a href="' + match.group(3) + '">'
        return '</a>'
    return INLINE.sub(restore, escaped)


def block(b, indent='  '):
    tag = b.get('tag')
    if tag in ('ul', 'ol'):
        items = ''.join(f'{indent}  <li>{inline(i)}</li>\n' for i in b.get('items') or [])
        return f'{indent}<{tag}>\n{items}{indent}</{tag}>\n'
    if tag == 'table':
        caption = f'{indent}  <caption>{inline(b["caption"])}</caption>\n' if b.get('caption') else ''
        head = ''.join(f'<th scope="col">{inline(h)}</th>' for h in b.get('header') or [])
        rows = ''.join(f'{indent}    <tr>' + ''.join(f'<td>{inline(c)}</td>' for c in r) + '</tr>\n' for r in b.get('rows') or [])
        return (f'{indent}<table>\n{caption}{indent}  <thead><tr>{head}</tr></thead>\n'
                f'{indent}  <tbody>\n{rows}{indent}  </tbody>\n{indent}</table>\n')
    if tag == 'dl':
        items = ''.join(f'{indent}  <dt>{inline(i.get("term"))}</dt><dd>{inline(i.get("definition"))}</dd>\n'
                        for i in b.get('items') or [])
        return f'{indent}<dl>\n{items}{indent}</dl>\n'
    if tag == 'figure':
        caption = f'\n{indent}  <figcaption>{inline(b["caption"])}</figcaption>' if b.get('caption') else ''
        dims = ''
        if b.get('width') and b.get('height'):
            dims = f' width="{int(b["width"])}" height="{int(b["height"])}"'
        return (f'{indent}<figure>\n{indent}  <img src="{html.escape(b.get("src") or "")}" '
                f'alt="{html.escape(b.get("alt") or "")}"{dims} loading="lazy" decoding="async">{caption}\n{indent}</figure>\n')
    if tag == 'blockquote':
        cite = f'\n{indent}  <cite>{inline(b["cite"])}</cite>' if b.get('cite') else ''
        return f'{indent}<blockquote>\n{indent}  <p>{inline(b.get("text"))}</p>{cite}\n{indent}</blockquote>\n'
    if tag in ('h3', 'h4'):
        return f'{indent}<{tag}>{inline(b.get("text"))}</{tag}>\n'
    return f'{indent}<p>{inline(b.get("text"))}</p>\n'


def section(heading, level, blocks, indent=''):
    slug = re.sub(r'[^a-z0-9]+', '-', (heading or '').lower()).strip('-')[:60]
    body = ''.join(block(b, indent + '  ') for b in blocks)
    return f'{indent}<section aria-labelledby="{slug}">\n{indent}  <h{level} id="{slug}">{inline(heading)}</h{level}>\n{body}{indent}</section>\n'


def faq(items):
    rows = ''.join(f'  <h3>{inline(i["question"])}</h3>\n  <p>{inline(i["answer"])}</p>\n' for i in items)
    return f'<section aria-labelledby="faq">\n  <h2 id="faq">Frequently asked questions</h2>\n{rows}</section>\n'


def json_ld(data):
    return '<script type="application/ld+json">\n' + json.dumps(data, indent=2, ensure_ascii=False) + '\n</script>\n'


def head_tags(tags):
    out = []
    for name, content in tags:
        attr = 'property' if name.startswith('og:') else 'name'
        out.append(f'<meta {attr}="{html.escape(name)}" content="{html.escape(content or "")}">')
    return '\n'.join(out)


def page_from_changes(changes, ctx):
    """Review aid built ONLY from the change list (so it can never disagree with it). Not directly applicable: apply
    changes[] individually at their targets."""
    by_field = {}
    for change in changes:
        by_field.setdefault(change['field'], []).append(change)

    def first(field):
        return (by_field.get(field) or [None])[0]

    status = lambda c: f' data-status="{html.escape(c["status"])}" data-change="{html.escape(c["id"])}"'  # noqa: E731
    parts = ['<!-- REVIEW AID ONLY - not directly applicable. Generated from changes[] by seo_agents.onpage; each element '
             'carries data-change=<id>; apply the changes individually at their targets. -->\n']
    head_bits = [c for c in changes if c['field'].startswith(('head.', 'technical.canonical')) and c.get('html')]
    if head_bits:
        parts.append('<!-- head -->\n')
        for c in head_bits:
            parts.append(f'<!-- {c["id"]} {c["status"]}{" depends_on " + ",".join(c["depends_on"]) if c["depends_on"] else ""} -->\n'
                         + c['html'].rstrip() + '\n')
    parts.append('<main>\n<article>\n<header>\n')
    h1 = first('headings.h1')
    if h1:
        parts.append(f'  <h1{status(h1)}>{inline(h1["value"])}</h1>\n')
    answer = first('content.direct_answer')
    if answer:
        parts.append(f'  <p class="lead"{status(answer)}>{inline(answer["value"])}</p>\n')
    parts.append('</header>\n')
    for field in sorted(f for f in by_field if f.startswith('content.section[')):
        c = first(field)
        value = c['value'] or {}
        parts.append(f'<!-- {c["id"]} {c["op"]} {c["status"]} -->\n' + section(value.get('heading'), value.get('level', 2), value.get('blocks') or []))
    faq_change = first('content.faq')
    if faq_change:
        parts.append(f'<!-- {faq_change["id"]} {faq_change["status"]} -->\n' + faq(faq_change['value']))
    parts.append('</article>\n</main>\n')
    return ''.join(parts)


class PagePreviewRenderer:
    """Tool: render the expected page (head tags, JSON-LD, sections, FAQ) from a change list."""

    inline = staticmethod(inline)
    block = staticmethod(block)
    section = staticmethod(section)
    faq = staticmethod(faq)
    json_ld = staticmethod(json_ld)
    head_tags = staticmethod(head_tags)
    page_from_changes = staticmethod(page_from_changes)
