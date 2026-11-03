"""Check 12 (deterministic): scope gate, reference validation, dedup, the <=15-change view and the checklist."""

from __future__ import annotations

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.changes import page_preview_renderer as render
from seo_agents.onpage.reports.exploration_reports import value
from seo_agents.onpage.validation.content_policy import LIMITS
from seo_agents.onpage.checks.check_helpers import result
from seo_agents.onpage.checks.media_links_check import alt_status
from seo_agents.onpage.checks.technical_check import platform_change
from seo_agents.onpage.checks.base_check import OnpageCheck

# ====================================================================== 12. QA: scope gate + consolidation + checklist (deterministic)
def outcome(changes, *fields):
    hits = [c for c in changes if c['field'].startswith(fields)]
    for status, label in (('proposed', True), ('blocked', 'blocked'), ('needs_input', 'needs_input')):
        if any(c['status'] == status for c in hits):
            return label
    return False


def checklist(ctx, prior, final):
    h1s = [h for h in ctx['headings'] if h['level'] == 1]
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
    }
    after_fields = {
        'title_tag': ('head.title',), 'meta_description': ('head.description',), 'single_h1': ('headings.h1',),
        'direct_answer': ('content.direct_answer',), 'keyword_placement': ('head.title', 'headings.h1', 'content.direct_answer'),
        'image_alt_text': ('images.alt',), 'canonical': ('technical.canonical',), 'social_meta': ('head.social', 'head.og'),
        'structured_data': ('head.json_ld',), 'served_html': ('platform.prerender',),
    }
    site_changes = [c for c in final if c['op'] == 'platform_config']
    out = {}
    for key, before in items.items():
        after = True if before else outcome(final, *after_fields[key])
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
    gated, needs_prerender, needs_per_route = ops.gate(collected, ctx)
    if needs_per_route and not any(c['id'] == 'chg_per_route_head' for c in site_changes):
        site_changes.append(platform_change('platform.per_route_head', 'head shared by several routes',
                                            'Give each route its own title/description/canonical/JSON-LD',
                                            where=ctx['recipe'].get('mechanism') or 'per-route head', hosting=ctx['hosting'],
                                            rationale='The head lives in a shared layout; per-route values need the stack\'s head API',
                                            change_id='chg_per_route_head'))
    changes, more = ops.consolidate(gated, ctx)
    seen_inputs, unique_inputs = set(), []
    for item in inputs:
        key = (item['needed'], item.get('change'))
        if key not in seen_inputs:
            seen_inputs.add(key)
            unique_inputs.append(item)
    failed = {k: r.get('errors') for k, r in prior.items() if (r or {}).get('status') == 'failed'}
    final = changes + more + site_changes
    return result(data={'changes': changes, 'changes_more': more, 'advice': advice, 'client_inputs': unique_inputs,
                        'site_changes': site_changes, 'checklist': checklist(ctx, prior, final),
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
