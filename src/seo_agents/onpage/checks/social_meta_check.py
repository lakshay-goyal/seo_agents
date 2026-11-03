"""Check 10 (deterministic): Open Graph and X card tags with a correctly sized share image."""

from __future__ import annotations

import re

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.changes import page_preview_renderer as render
from seo_agents.onpage.reports.exploration_reports import value
from seo_agents.onpage.checks.check_helpers import add_target, brand, page_type, plan_of, result
from seo_agents.onpage.checks.base_check import OnpageCheck

# ====================================================================== 10. social meta (deterministic)
def share_image(ctx):
    """og:image must be absolute and ~1200x630 (1.91:1); otherwise it is a client input, not a guess."""
    og = ctx['head'].get('og') or {}
    current = value(og.get('image'))
    if current:
        return current, None
    for image in ctx['images']:
        natural = image.get('natural') or []
        if image.get('src') and len(natural) == 2 and natural[0] >= 1200 and 1.85 <= natural[0] / max(natural[1], 1) <= 1.97:
            src = image['src']
            return (ctx['origin'] + src if ctx['origin'] and src.startswith('/') else src), None
    return '[VERIFY: 1200x630 share image]', '1200x630 share image (og:image); page screenshots are the wrong size/aspect'


def check_social_meta(ctx, prior, writer=None):
    plan = plan_of(prior)
    title = plan.get('title') or ctx['title']
    description = plan.get('description') or ctx['description']
    name = brand(ctx, prior)
    og = ctx['head'].get('og') or {}
    tw = ctx['head'].get('twitter') or {}
    og_title = title
    if name and title:
        stripped = re.split(r'\s+[|—–-]\s+' + re.escape(name) + r'\s*$', title)[0]
        og_title = stripped if len(stripped) >= 10 else title
    image, needed = share_image(ctx)
    expected = [('og:title', og_title), ('og:description', description), ('og:type', 'article' if page_type(ctx, prior) == 'article' else 'website'),
                ('og:url', ctx['absolute_url'] or ctx['url']), ('og:image', image), ('og:site_name', name),
                ('twitter:card', 'summary_large_image')]
    expected = [(k, e) for k, e in expected if e]
    inputs = []
    changes = []
    if not og and not tw:
        # nothing exists yet: one atomic insert (no per-tag + composite duplicates)
        change = ops.make_change(ctx, 'social_meta', 'head.social', None, dict(expected), target=add_target(ctx, 'head.social'),
                                 op='insert_node', confidence=0.85, html=render.head_tags(expected),
                                 rationale='Share preview (Open Graph / X card); social crawlers do not run JavaScript')
        changes.append(change)
    else:
        for key, exp in expected:
            group, prop = key.split(':', 1)
            cur_field = (og if group == 'og' else tw).get(prop)
            cur = value(cur_field)
            if cur == exp:
                continue
            changes.append(ops.make_change(ctx, 'social_meta', 'head.' + key.replace(':', '.'), cur, exp,
                                           target=ops.target_of(cur_field, attr='content') or add_target(ctx, 'head.' + key),
                                           op='set_attr' if cur else 'insert_node', confidence=0.8, html=render.head_tags([(key, exp)]),
                                           rationale='Share preview (Open Graph / X card)'))
    if needed and changes:
        inputs.append(ops.client_input(ctx, needed, change=changes[0]['id'], field='head.og.image'))
        for change in changes:
            if change['field'] in ('head.social', 'head.og.image') and change['status'] == 'proposed':
                change['status'] = 'needs_input'
    return result(data={'expected': dict(expected)}, changes=changes, client_inputs=inputs)


class SocialMetaCheck(OnpageCheck):
    """Check: Open Graph and X card tags from the planned title/description and a sized share image (deterministic)."""

    name = 'social_meta'
    skill = 'social-meta'
    uses_model = False
    depends_on = ()

    def run(self, ctx, prior, writer):
        return check_social_meta(ctx, prior, writer)
