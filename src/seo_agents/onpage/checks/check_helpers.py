"""Helpers shared by the on-page checks: the result shape, earlier-result accessors, page facts and edit targets."""

from __future__ import annotations

import re

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.validation.content_policy import PAGE_TYPES, SITE_TYPES

INTENTS = {'informational', 'commercial', 'transactional', 'navigational', 'local'}
STAGES = {'awareness', 'consideration', 'decision', 'retention'}
ROLES = {'pillar', 'cluster', 'standalone'}
ADVICE_TOPICS = {'eeat.authorship', 'eeat.experience', 'eeat.expertise', 'eeat.authority', 'eeat.trust', 'eeat.citations',
                 'aeo.definition', 'aeo.summary', 'aeo.entity_consistency', 'aeo.questions', 'content.depth', 'content.freshness'}
WEAK_ALT = re.compile(r'(?i)^(?:image|img|photo|picture|screenshot|banner|icon|logo|untitled|[\w-]+\.(?:png|jpe?g|webp|gif|svg|avif))$')


def result(status='done', data=None, changes=None, errors=None, meta=None, advice=None, client_inputs=None, site_changes=None):
    return {'status': status, 'data': data or {}, 'changes': changes or [], 'advice': advice or [],
            'client_inputs': client_inputs or [], 'site_changes': site_changes or [], 'errors': errors or [], 'meta': meta or {}}


def intent_of(prior):
    return (prior.get('intent') or {}).get('data') or {}


def plan_of(prior):
    return (prior.get('page_plan') or {}).get('data') or {}


def brand(ctx, prior):
    return intent_of(prior).get('brand_name') or (ctx['brand_candidates'] or [None])[0]


def site_type(ctx, prior):
    found = intent_of(prior).get('site_type')
    return found if found in SITE_TYPES and ctx['site_type'] in ('other', None) else (ctx['site_type'] or found or 'other')


def page_type(ctx, prior):
    found = intent_of(prior).get('page_type')
    if found in PAGE_TYPES:
        return found
    url = ctx['url'].lower()
    if ctx['is_home']:
        return 'home'
    if re.search(r'/(?:blog|posts?|articles?|news)/[^/]+', url):
        return 'article'
    if re.search(r'/about', url):
        return 'about'
    if re.search(r'/contact', url):
        return 'contact'
    return 'collection' if re.search(r'/(?:blog|posts|projects|work|bookshelf|shop|products)/?$', url) else 'other'


def site_url_set(ctx):
    resources = {l['href'] for l in ctx['links'] if isinstance(l.get('href'), str) and l['href'].startswith('/')
                 and (l.get('status') is None or l['status'] < 400)}
    return {u['url'] for u in ctx['site_urls']} | resources


def numbers_allowed(ctx):
    return {ctx['run_date'][:4]}


def first_main_edit(ctx, *, heading=False):
    """Edit of the first page-specific (non-chrome, non-data-template) heading or block."""
    if heading:
        for h in ctx['heading_records']:
            if h['edit'] and h['in_main'] and not h['data_driven']:
                return h['edit']
    for s in ctx['sections']:
        for b in s['blocks']:
            if b.get('edit') and (b['edit'].get('kind') not in ('json', 'yaml', 'markdown', 'i18n')):
                return b['edit']
    return None


def add_target(ctx, field):
    """Head insertion point from exploration (`add_target`) - a target, never a bare `line: 1` placeholder."""
    target = ops.target_of(ctx.get('add_target'), kind='add', placement='before </head>')
    if target is not None and target.get('scope') == 'unknown' and target.get('file'):
        target['scope'] = 'shell' if ops.recipes.is_shell_file(target['file']) else 'page'
    return target
