"""Check 11 (deterministic): verify numbers, names and placeholders in model-written changes; flag stale years."""

from __future__ import annotations

import re

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.validation import content_validators as v
from seo_agents.onpage.checks.check_helpers import first_main_edit, numbers_allowed, page_type, result
from seo_agents.onpage.checks.base_check import OnpageCheck

# ====================================================================== 11. fact-check + freshness (deterministic)
FRESH_CONTEXT = re.compile(r'(?i)(©|copyright|updated|as of|latest|since|in (19|20)\d\d)')
YEAR = re.compile(r'\b(19|20)\d{2}\b')
SKIP_STRING_KEYS = {'@id', '@type', 'url', 'src', 'href', '@context', 'image', 'logo', 'item', 'sameAs', 'dateModified', 'email',
                    'isPartOf', 'mainEntityOfPage', 'inLanguage'}


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, item in obj.items():
            if k not in SKIP_STRING_KEYS:
                yield from _strings(item)
    elif isinstance(obj, list):
        for item in obj:
            yield from _strings(item)


def check_fact_check(ctx, prior, writer=None):
    corpus = ctx['corpus']
    corpus_words = set(re.findall(r'[a-z0-9]+', corpus.lower()))
    allowed = numbers_allowed(ctx)
    report, inputs = [], []
    from seo_agents.onpage.checks.checklist import CHECKS  # registry imports this module
    written_by_model = {check.name for check in CHECKS if check.uses_model}
    for check, res in prior.items():
        if check not in written_by_model:
            continue
        for change in (res or {}).get('changes') or []:
            texts = list(_strings(change.get('value')))
            joined = ' '.join(texts)
            sentences = [x for t in texts for x in re.split(r'(?<=[.!?:])\s+', v.VERIFY.sub(' ', v.strip_markup(t)))]
            numbers_bad = v.ungrounded_numbers(joined, corpus, allowed)
            capitalised = [w for s in sentences for w in re.findall(r'\b[A-Z][A-Za-z0-9]{2,}\b', s.split(' ', 1)[-1] if ' ' in s else '')]
            terms = sorted({t for t in capitalised if t.lower() not in corpus_words and t.lower() not in v.STOPWORDS})
            placeholders = v.VERIFY.findall(joined)
            change['evidence']['fact_check'] = {'needs_verification': numbers_bad, 'unverified_terms': terms[:15], 'placeholders': placeholders}
            if numbers_bad or placeholders or terms:
                change['status'] = 'needs_input'
            inputs += [ops.client_input(ctx, p, change=change['id'], field=change['field']) for p in placeholders]
            inputs += [ops.client_input(ctx, f'Confirm "{t}" (not found in the site content)', change=change['id'], field=change['field'])
                       for t in terms[:5]]
            if numbers_bad or terms:
                report.append({'change': change['id'], 'numbers': numbers_bad, 'terms': terms[:15]})
    run_year = int(ctx['run_date'][:4])
    stale = [{'text': t[:160], 'year': int(mt.group(0))} for t in ctx['copy'] for mt in YEAR.finditer(t)
             if int(mt.group(0)) < run_year - 2 and FRESH_CONTEXT.search(t)]
    changes, advice = [], []
    if stale:
        advice.append(ops.advice(ctx, 'fact_check', 'content.freshness', f'Outdated years in freshness-sensitive copy (as of {run_year})',
                                 impact='low', evidence=stale))
    has_date = any(s.get('signal') == 'dates' and s.get('status') == 'observed' for s in ctx['eeat_observed'])
    if page_type(ctx, prior) == 'article' and not has_date:  # personal/home pages do not get a "Last updated" line
        anchor = first_main_edit(ctx, heading=True)
        changes.append(ops.make_change(ctx, 'fact_check', 'freshness.published', None, '[VERIFY: publication date]',
                                       target=ops.target_of(anchor, placement='under the post title') if anchor else None, op='insert_node',
                                       confidence=0.6, html='<time datetime="[VERIFY]">[VERIFY: publication date]</time>',
                                       rationale='Visible publication/updated date for a post (mirrored in BlogPosting dates)', status='needs_input'))
        inputs.append(ops.client_input(ctx, 'Publication date for this post', change=changes[-1]['id'], field='freshness.published'))
    return result(data={'claims_report': report, 'stale': stale,
                        'policy': 'numbers (digits and spelled-out) must appear in the visible text/data JSON; capitalised terms not '
                                  'found there go to client_inputs'},
                  changes=changes, advice=advice, client_inputs=inputs)


class FactCheckFreshnessCheck(OnpageCheck):
    """Check: Verify numbers, names and placeholders in model-written changes; flag stale years (deterministic)."""

    name = 'fact_check'
    skill = 'fact-check-freshness'
    uses_model = False
    depends_on = ()

    def run(self, ctx, prior, writer):
        return check_fact_check(ctx, prior, writer)
