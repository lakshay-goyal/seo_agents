"""Single source of truth for on-page limits, priorities and field groups.

Validators, skill prompts (via `{{NAME}}` placeholders in SKILL.md) and the consolidation pass all read these
constants, so a prompt can never promise a limit the validator does not enforce (audit §8, §13).
"""

from __future__ import annotations

LIMITS = {
    'TITLE_CHARS': (30, 60),
    'DESCRIPTION_CHARS': (120, 160),
    'H1_CHARS': (20, 70),
    'HEADING_CHARS': (3, 90),
    'H2_COUNT': (1, 8),
    'DIRECT_ANSWER_WORDS': (40, 60),
    'PARAGRAPH_MAX_WORDS': (1, 70),
    'FAQ_ITEMS': (0, 5),
    'FAQ_ITEMS_NAVIGATIONAL': (0, 3),
    'FAQ_ANSWER_WORDS': (25, 70),
    'ALT_CHARS': (5, 125),
    'ANCHOR_WORDS': (1, 6),
    'NAV_ANCHOR_WORDS': (1, 3),
    'NEW_LINKS': (0, 3),
    'ENTITY_PER_SECTION': (0, 1),
    'ENTITY_WORDS_PER_MENTION': (150, 150),
    'PRIMARY_PHRASE_PER_100_WORDS': (0, 1),
    'MAX_TERM_DENSITY_PCT': (0, 3.5),
    'DEFAULT_VIEW_CHANGES': (0, 15),
}


def render_limits(text):
    """Replace `{{NAME}}` with "low-high" and `{{NAME.max}}` / `{{NAME.min}}` with one bound."""
    for name, (low, high) in LIMITS.items():
        text = text.replace('{{' + name + '.max}}', str(high)).replace('{{' + name + '.min}}', str(low))
        text = text.replace('{{' + name + '}}', f'{low}-{high}')
    return text


# ------------------------------------------------------------------ priorities (real field names, audit §10 bug)
HIGH_FIELDS = ('head.title', 'head.description', 'headings.h1', 'content.direct_answer', 'technical.canonical',
               'technical.robots', 'technical.prerender', 'platform.prerender', 'platform.per_route_head',
               'platform.redirect', 'platform.soft_404', 'links.broken')
MEDIUM_FIELDS = ('headings.', 'content.', 'head.json_ld', 'images.', 'links.anchor', 'links.new', 'technical.',
                 'platform.', 'site.')
LOW_FIELDS = ('head.og', 'head.twitter', 'head.social', 'links.accessible_name', 'links.verify', 'freshness.')


def priority_for(field):
    if field in HIGH_FIELDS or any(field.startswith(f + '[') for f in HIGH_FIELDS):
        return 'high'
    if field.startswith(LOW_FIELDS):
        return 'low'
    if field.startswith(MEDIUM_FIELDS):
        return 'medium'
    return 'low'


IMPACT_RANK = {'high': 0, 'medium': 1, 'low': 2}

# Fields that belong to the document <head> (or are page-identity signals that must be per route).
HEAD_FIELDS = ('head.', 'technical.canonical', 'technical.robots', 'technical.lang')
# Fields that must never be placed in a shared shell/layout (they are page specific).
PAGE_SPECIFIC = HEAD_FIELDS + ('headings.h1', 'content.', 'links.new', 'freshness.')

OPS = {'set_text', 'set_attr', 'set_json', 'insert_node', 'remove', 'platform_config'}
STATUSES = {'proposed', 'blocked', 'needs_input'}
SCOPES = {'literal', 'instance', 'data', 'template', 'layout', 'shell', 'page', 'platform', 'unknown'}

# Personal / portfolio pages do not get a mandatory author box or "Last updated" line (audit §8).
SITE_TYPES = {'personal', 'business', 'ecommerce', 'publisher', 'docs', 'other'}
PAGE_TYPES = {'home', 'profile', 'about', 'contact', 'collection', 'article', 'product', 'docs', 'other'}


class ContentPolicy:
    """Tool: numeric content limits rendered into prompts, and change priorities."""

    render_limits = staticmethod(render_limits)
    priority_for = staticmethod(priority_for)
