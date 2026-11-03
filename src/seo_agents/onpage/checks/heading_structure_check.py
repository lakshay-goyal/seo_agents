"""Check 13 (deterministic): the current heading outline - one visible H1 in main content, no skipped levels, no empty
or duplicate headings."""

from __future__ import annotations

import re

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.changes import page_preview_renderer as render
from seo_agents.onpage.validation import content_validators as v
from seo_agents.onpage.validation.content_policy import LIMITS
from seo_agents.onpage.checks.check_helpers import result
from seo_agents.onpage.checks.base_check import OnpageCheck

# ====================================================================== 14. heading structure (deterministic)
NO_H2_WORDS = 300


def heading_text(record):
    text = record.get('text')
    return re.sub(r'\s+', ' ', text).strip() if isinstance(text, str) else ''


def outline(ctx):
    """Heading records in document order with an integer level 1-6."""
    return [h for h in ctx.get('heading_records') or [] if isinstance(h, dict) and isinstance(h.get('level'), int)
            and 1 <= h['level'] <= 6]


def skipped_levels(records):
    """[(from, to)] where the outline jumps more than one level down (H2 -> H4)."""
    jumps, previous = [], None
    for record in records:
        level = record['level']
        if previous is not None and level > previous + 1:
            jumps.append((previous, level))
        previous = level
    return jumps


def structural(records):
    """Headings that belong to the route's own outline: in main content (in_main None counts as main) and not data-driven."""
    return [h for h in records if h.get('in_main') is not False and not h.get('data_driven')]


def pick_primary(h1s):
    """The H1 to keep: the first visible, non-empty, non-data-driven one inside main content, else the first non-empty,
    else the first."""
    for test in (lambda h: heading_text(h) and h.get('in_main') and h.get('visible') is not False and not h.get('data_driven'),
                 lambda h: heading_text(h), lambda h: True):
        found = next((h for h in h1s if test(h)), None)
        if found is not None:
            return found
    return None


def primary_h1(ctx):
    """The page's primary H1 record (shared by heading_structure and page_plan so both act on the same element), or None."""
    return pick_primary([h for h in outline(ctx) if h['level'] == 1])


def check_heading_structure(ctx, prior=None, writer=None):
    changes, advice = [], []
    records = outline(ctx)
    note = lambda topic, text, impact='medium', evidence=None: advice.append(  # noqa: E731
        ops.advice(ctx, 'heading_structure', topic, text, impact=impact, evidence=evidence))
    data_only = bool(records) and all(h.get('data_driven') for h in records)
    h1s = [h for h in records if h['level'] == 1]
    keep = pick_primary(h1s)

    # ---- multiple H1
    if len(h1s) > 1:
        unplaced = []
        for record in h1s:
            if record is keep:
                continue
            text = heading_text(record)
            target = ops.target_of(record.get('edit'), confidence=record.get('confidence'))
            if target is None or record.get('data_driven'):
                unplaced.append(text or '(empty)')
                continue
            # A tag change (h1 -> h2, text unchanged) has no exact op: emitted for the owner to apply (needs_input)
            changes.append(ops.make_change(ctx, 'heading_structure', 'headings.h1_extra', text or None, text or None,
                                           target=target, op='set_text', confidence=0.75, status='needs_input',
                                           html=f'<h2>{render.inline(text)}</h2>',
                                           rationale='Change the element from h1 to h2 (tag change, text unchanged); one H1 per '
                                                     'page, keep the main-content H1',
                                           extra={'tag_from': 'h1', 'tag_to': 'h2', 'keep_h1': heading_text(keep) or None}))
        note('headings.multiple_h1', f'{len(h1s)} H1 elements on the page; keep one (the main-content title) and demote the rest '
             'to H2.', 'medium', {'h1s': [heading_text(h) or '(empty)' for h in h1s][:8], 'keep': heading_text(keep) or None,
                                  'no_edit_location': unplaced[:8]})

    # ---- H1 placement / visibility
    if h1s:
        if not any(h.get('in_main') for h in h1s):
            note('headings.h1_outside_main', 'The only H1 is in the header/nav/footer (shared chrome); the H1 should be the '
                 'route\'s own title inside main content.', 'medium', {'h1s': [heading_text(h) for h in h1s][:5]})
        hidden = [h for h in h1s if h.get('visible') is False]
        if hidden:
            note('headings.h1_hidden', 'An H1 is hidden (display:none / off-screen); hidden headings carry little weight and '
                 'can look like cloaking. Make the page title visible.', 'high', {'h1s': [heading_text(h) for h in hidden][:5]})

    # ---- empty headings
    empty = [h for h in records if not heading_text(h)]
    if empty:
        note('headings.empty', f'{len(empty)} heading element(s) have no text (icon-only or placeholder); give them text or '
             'use a non-heading element.', 'high' if any(h['level'] == 1 for h in empty) else 'medium',
             {'levels': ['H' + str(h['level']) for h in empty][:10]})

    # ---- skipped levels / order / duplicates: only the route's own outline (main content, not data-driven)
    own = structural(records)
    jumps = skipped_levels(own)
    if jumps:
        note('headings.skipped_levels', 'Heading levels skip (' + ', '.join(f'H{a}->H{b}' for a, b in jumps[:6]) + '); keep the '
             'outline nested one level at a time so the structure is machine-readable.', 'low' if data_only else 'medium',
             {'jumps': [f'H{a}->H{b}' for a, b in jumps][:10]})
    own_h1s = [h for h in own if h['level'] == 1]
    if own_h1s:
        first_h1 = own.index(own_h1s[0])
        before = [h for h in own[:first_h1] if h['level'] > 1]
        if before:
            note('headings.before_h1', f'{len(before)} heading(s) appear before the first H1 (often in the header/nav); the '
                 'page title should open the outline.', 'low', {'headings': [f"H{h['level']}: {heading_text(h)}" for h in before][:6]})

    # ---- duplicate / long headings
    seen = {}
    for h in own:
        key = v.normalize(heading_text(h))
        if key:
            seen.setdefault(key, []).append(heading_text(h))
    repeated = {items[0]: len(items) for items in seen.values() if len(items) > 1}
    if repeated:
        note('headings.duplicate', 'Headings repeat on the page; each heading should describe its own section.', 'low',
             {'repeated': dict(list(repeated.items())[:8])})
    max_chars = LIMITS['HEADING_CHARS'][1]
    long_ones = [heading_text(h) for h in records if len(heading_text(h)) > max_chars]
    if long_ones:
        note('headings.too_long', f'{len(long_ones)} heading(s) exceed {max_chars} characters; headings are labels, move detail '
             'into the paragraph below.', 'low', {'headings': [t[:120] for t in long_ones][:5]})

    # ---- no H2 on a long page
    main_words = ctx.get('main_words') if isinstance(ctx.get('main_words'), int) else 0
    if main_words > NO_H2_WORDS and not any(h['level'] == 2 for h in records):
        note('headings.no_h2', f'{main_words} words of main content with no H2; break it into sections with descriptive H2s.',
             'medium', {'main_words': main_words})

    data = {'h1_count': len(h1s), 'headings': len(records), 'skipped_levels': [f'H{a}->H{b}' for a, b in jumps],
            'empty': len(empty), 'data_driven_only': data_only}
    return result(data=data, changes=changes, advice=advice)


class HeadingStructureCheck(OnpageCheck):
    """Check: One visible H1 in main content, no skipped levels, empty/duplicate/long headings, H2s on long pages (deterministic)."""

    name = 'heading_structure'
    skill = 'heading-structure'
    uses_model = False
    depends_on = ()

    def run(self, ctx, prior, writer):
        return check_heading_structure(ctx, prior or {}, writer)
