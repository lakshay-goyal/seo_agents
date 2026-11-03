"""Check 21 (deterministic): scope gate, reference validation, dedup, the <=15-change view and the checklist.

Every change is annotated with `serves` (the target keywords / questions / prompts it serves) and the page gets a
before/after `keyword_coverage` table plus the fact-check quality verdict.
"""

from __future__ import annotations

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.validation import content_validators as v
from seo_agents.onpage.changes import page_preview_renderer as render
from seo_agents.onpage.reports.exploration_reports import value
from seo_agents.onpage.validation.content_policy import LIMITS
from seo_agents.onpage.checks.check_helpers import keywords_of, names_of, result
from seo_agents.onpage.checks.media_links_check import alt_status
from seo_agents.onpage.checks.heading_structure_check import outline, skipped_levels, structural
from seo_agents.onpage.checks.link_hygiene_check import internal_nofollow
from seo_agents.onpage.checks.technical_check import platform_change
from seo_agents.onpage.checks.base_check import OnpageCheck

# ====================================================================== 12. QA: scope gate + consolidation + checklist (deterministic)
def outcome(changes, *fields):
    hits = [c for c in changes if c['field'].startswith(fields)]
    for status, label in (('proposed', True), ('blocked', 'blocked'), ('needs_input', 'needs_input')):
        if any(c['status'] == status for c in hits):
            return label
    return False


PLACEMENT_FIELDS = {'title': 'head.title', 'h1': 'headings.h1', 'meta_description': 'head.description',
                    'first_100_words': 'content.direct_answer'}


def _texts(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for key, item in obj.items():
            if key not in ('src', 'href', '@id', '@type', '@context', 'url', 'sameAs'):
                yield from _texts(item)
    elif isinstance(obj, list):
        for item in obj:
            yield from _texts(item)


def headings_in(change):
    value = change.get('value')
    if change['field'].startswith('headings.') and isinstance(value, str):
        return [value]
    if isinstance(value, dict) and isinstance(value.get('heading'), str):
        return [value['heading']]
    if change['field'] == 'content.faq' and isinstance(value, list):
        return [q.get('question') or '' for q in value if isinstance(q, dict)]
    return []


def serves(change, kw, names=()):
    """Target keywords, questions and conversational prompts a change serves (deterministic text match)."""
    text = ' '.join(_texts(change.get('value')))
    if not text:
        return []
    out = [k for k in [kw['primary']] + kw['secondary'] + kw['short_tail'] if k and v.contains_query(text, k)]
    heads = headings_in(change)
    out += [q for q in kw['questions'] if any(v.covers_question(q, h, ignore=names) for h in heads)]
    have = v.content_tokens(text)
    if len(v.words(text)) >= LIMITS['QUOTABLE_PASSAGE_WORDS'][0]:
        out += [p for p in kw['conversational'] if v.content_tokens(p) and len(v.content_tokens(p) & have) / len(v.content_tokens(p)) >= 0.5]
    return list(dict.fromkeys(out))


def change_state(changes):
    for status, label in (('proposed', True), ('blocked', 'blocked'), ('needs_input', 'needs_input')):
        if any(c['status'] == status for c in changes):
            return label
    return False


def keyword_coverage(prior, final, kw):
    """Before/after per keyword role: where the primary sits, and whether each question/prompt is answered."""
    current = (prior.get('keyword_plan') or {}).get('data') or {}
    placements = current.get('placements_current') or {}
    serving = lambda key: [c for c in final if key in (c.get('serves') or [])]  # noqa: E731
    primary = {}
    for where, field in PLACEMENT_FIELDS.items():
        before = bool(placements.get(where))
        after = True if before else change_state([c for c in serving(kw['primary']) if c['field'] == field])
        primary[where] = {'before': before, 'after': after}
    secondary = {k: {'before': any(bool(x) for x in ((current.get('secondary_placements_current') or {}).get(k) or {}).values()),
                     'after_changes': [c['id'] for c in serving(k)]} for k in kw['secondary']}
    questions = {}
    for q in kw['questions']:
        before = bool(((current.get('questions_current') or {}).get(q) or {}).get('answered'))
        questions[q] = {'before': before, 'after': True if before else change_state(serving(q)), 'changes': [c['id'] for c in serving(q)]}
    prompts = {}
    for p in kw['conversational']:
        row = (current.get('conversational_current') or {}).get(p) or {}
        before = bool(row.get('passage') and row.get('entity_explicit') and row.get('self_contained'))
        prompts[p] = {'before': before, 'after': True if before else change_state(serving(p)), 'changes': [c['id'] for c in serving(p)]}
    return {'source': kw['source'], 'primary': kw['primary'], 'primary_placements': primary, 'secondary': secondary,
            'questions': questions, 'conversational': prompts, 'stuffing_flags_current': current.get('stuffing_flags') or []}


def h1_outcome(final, h1_count):
    """No H1 -> a headings.h1 change adds one; several -> every extra H1 needs its own headings.h1_extra change.
    headings.h1_extra is a tag change emitted as needs_input, so it yields 'needs_input', never True (fixed)."""
    if h1_count == 0:
        return outcome([c for c in final if c['field'] == 'headings.h1'], 'headings.h1')
    extras = [c for c in final if c['field'] == 'headings.h1_extra']
    if len({(c.get('target') or {}).get('key') or c['id'] for c in extras}) < h1_count - 1:
        return False
    return outcome(extras, 'headings.h1_extra')


def rel_outcome(final, offending):
    """Fixed only when distinct links.rel change targets cover every offending (internal nofollow) link."""
    rel = [c for c in final if c['field'] == 'links.rel']
    if len({(c.get('target') or {}).get('key') or c['id'] for c in rel}) < offending:
        return False
    return outcome(rel, 'links.rel')


def checklist(ctx, prior, final):
    h1_records = [h for h in outline(ctx) if h['level'] == 1]
    h1s = h1_records if ctx.get('heading_records') else [h for h in ctx['headings'] if h['level'] == 1]
    rendered_links = [l for l in ctx.get('links') or [] if isinstance(l, dict) and l.get('renders') is not False and l.get('href')]
    kw = (prior.get('keyword_plan') or {}).get('data') or {}
    placements = kw.get('placements_current') or {}
    canonical = value(ctx['head'].get('canonical'))
    expected_canonical = ctx['absolute_url'] or ctx['url']
    images_missing = [i for i in ctx['images'] if i.get('src') and alt_status(i) in ('missing', 'generic')]
    items = {
        'title_tag': bool(ctx['title']) and LIMITS['TITLE_CHARS'][0] <= len(ctx['title'] or '') <= LIMITS['TITLE_CHARS'][1],
        'meta_description': bool(ctx['description']) and LIMITS['DESCRIPTION_CHARS'][0] <= len(ctx['description'] or '') <= LIMITS['DESCRIPTION_CHARS'][1],
        'single_h1': len(h1s) == 1,
        'direct_answer': False,
        'keyword_placement': all(placements.get(k) for k in ('title', 'h1', 'first_100_words', 'meta_description')),
        'image_alt_text': not images_missing,
        'canonical': bool(canonical) and canonical.rstrip('/') == expected_canonical.rstrip('/'),
        'social_meta': bool((ctx['head'].get('og') or {}).get('title')) and bool((ctx['head'].get('og') or {}).get('image')),
        'structured_data': bool(ctx['json_ld']),
        'served_html': ctx['head_scope'] != 'shared-shell',
        'questions_answered': all(c.get('answered') for c in (kw.get('questions_current') or {}).values()),
        'heading_structure': not skipped_levels(structural(outline(ctx))),
        'internal_link_rel': not any(internal_nofollow(l, ctx) for l in rendered_links),
    }
    after_fields = {
        'title_tag': ('head.title',), 'meta_description': ('head.description',), 'single_h1': ('headings.h1',),
        'direct_answer': ('content.direct_answer',), 'keyword_placement': ('head.title', 'headings.h1', 'content.direct_answer'),
        'image_alt_text': ('images.alt',), 'canonical': ('technical.canonical',), 'social_meta': ('head.social', 'head.og'),
        'structured_data': ('head.json_ld',), 'served_html': ('platform.prerender',),
        'questions_answered': ('content.section', 'content.faq', 'headings.h2', 'headings.h3'),
        'heading_structure': (),  # skipped levels are advice-only (heading_structure): after = 'advice' 
        'internal_link_rel': ('links.rel',),
    }
    site_changes = [c for c in final if c['op'] == 'platform_config']
    out = {}
    for key, before in items.items():
        after = True if before else outcome(final, *after_fields[key])
        if key == 'single_h1' and not before:
            after = h1_outcome(final, len(h1s))
        if key == 'heading_structure' and not before:
            after = 'advice'
        if key == 'internal_link_rel' and not before:
            after = rel_outcome(final, sum(1 for l in rendered_links if internal_nofollow(l, ctx)))
        if key == 'served_html' and not before:
            after = 'needs_platform_change' if site_changes else False
        out[key] = {'before': before, 'after': after}
    return out


def check_qa(ctx, prior, writer=None):
    collected, advice, inputs, site_changes = [], [], [], []
    from seo_agents.onpage.checks.checklist import CHECKS  # registry imports this module
    for name in [check.name for check in CHECKS]:
        res = prior.get(name) or {}
        collected += res.get('changes') or []
        advice += res.get('advice') or []
        inputs += res.get('client_inputs') or []
        site_changes += res.get('site_changes') or []
    kw = keywords_of(prior)
    for change in collected:
        change['serves'] = serves(change, kw, names_of(ctx, prior))
    gated, needs_prerender, needs_per_route = ops.gate(collected, ctx)
    if needs_per_route and not any(c['id'] == 'chg_per_route_head' for c in site_changes):
        site_changes.append(platform_change('platform.per_route_head', 'head shared by several routes',
                                            'Give each route its own title/description/canonical/JSON-LD',
                                            where=ctx['recipe'].get('mechanism') or 'per-route head', hosting=ctx['hosting'],
                                            rationale='The head lives in a shared layout; per-route values need the stack\'s head API',
                                            change_id='chg_per_route_head'))
    changes, more = ops.consolidate(gated, ctx)
    for change in changes + more:  # a claim the fact check could not support stays blocked whatever the gate decided
        if change.get('blocked_by') == 'fact_check':
            change['status'] = 'blocked'
    seen_inputs, unique_inputs = set(), []
    for item in inputs:
        key = (item['needed'], item.get('change'))
        if key not in seen_inputs:
            seen_inputs.add(key)
            unique_inputs.append(item)
    failed = {k: r.get('errors') for k, r in prior.items() if (r or {}).get('status') == 'failed'}
    final = changes + more + site_changes
    quality = ((prior.get('fact_check') or {}).get('data') or {}).get('quality')
    return result(data={'changes': changes, 'changes_more': more, 'advice': advice, 'client_inputs': unique_inputs,
                        'site_changes': site_changes, 'checklist': checklist(ctx, prior, final),
                        'keyword_coverage': keyword_coverage(prior, changes + more, kw), 'quality': quality,
                        'expected_page_html': render.page_from_changes(changes + more, ctx), 'failed_checks': failed,
                        'needs_prerender': needs_prerender})


class QaConsolidationCheck(OnpageCheck):
    """Check: Scope gate, reference validation, dedup/conflicts, the <=15-change view and the before/after checklist (deterministic)."""

    name = 'qa'
    skill = 'qa-consolidation'
    uses_model = False
    depends_on = ()

    def run(self, ctx, prior, writer):
        return check_qa(ctx, prior, writer)
