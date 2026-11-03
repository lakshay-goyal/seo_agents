"""Check 11 (deterministic): after content is generated, verify every proposed claim against the page facts, check
helpfulness (intent + questions answered, competitor gaps closed) and freshness, and record a per-page quality verdict.

Unsupported claims (numbers or names not in the facts, or sentences whose content words are mostly absent from the
facts) are flagged and the change is `blocked` until the owner confirms them. `[VERIFY: ...]` placeholders stay
`needs_input`.
"""

from __future__ import annotations

import re

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.validation import content_validators as v
from seo_agents.onpage.validation.content_policy import LIMITS
from seo_agents.onpage.checks.check_helpers import (brand, first_main_edit, intent_of, keywords_of, names_of, numbers_allowed, page_type,
                                                    plan_of, result)
from seo_agents.onpage.checks.base_check import OnpageCheck

# ====================================================================== 11. fact-check + freshness (deterministic)
FRESH_CONTEXT = re.compile(r'(?i)(©|copyright|updated|as of|latest|since|in (19|20)\d\d|current|new|this year)')
YEAR = re.compile(r'\b(19|20)\d{2}\b')
SKIP_STRING_KEYS = {'@id', '@type', 'url', 'src', 'href', '@context', 'image', 'logo', 'item', 'sameAs', 'dateModified', 'email',
                    'isPartOf', 'mainEntityOfPage', 'inLanguage'}
# Fields whose text is a claim about the client (alts describe an image, anchors name a link target).
CLAIM_FIELDS = ('head.title', 'head.description', 'headings.', 'content.')
MIN_CLAIM_TOKENS = 5


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


def sentences_of(texts):
    return [x.strip() for t in texts for x in re.split(r'(?<=[.!?:])\s+', v.VERIFY.sub(' ', v.strip_markup(t))) if x.strip()]


def keyword_tokens(ctx, prior):
    """Words a sentence may use without counting as a new claim: the target keywords, brand and entities."""
    kw, intent = keywords_of(prior), intent_of(prior)
    phrases = [kw['primary'] or ''] + kw['secondary'] + kw['questions'] + kw['conversational'] + kw['short_tail']
    phrases += [brand(ctx, prior) or ''] + list(intent.get('entities') or [])
    return set().union(*(v.content_tokens(p) for p in phrases)) if phrases else set()


def stale_years(texts, run_year):
    return [{'text': t[:160], 'year': int(mt.group(0))} for t in texts for mt in YEAR.finditer(t)
            if int(mt.group(0)) < run_year - 2 and FRESH_CONTEXT.search(t)]


def verify_change(change, ctx, corpus_words, corpus_tokens, allowed_tokens, allowed):
    texts = list(_strings(change.get('value')))
    joined = ' '.join(texts)
    sentences = sentences_of(texts)
    numbers_bad = v.ungrounded_numbers(joined, ctx['corpus'], allowed)
    capitalised = [w for s in sentences for w in re.findall(r'\b[A-Z][A-Za-z0-9]{2,}\b', s.split(' ', 1)[-1] if ' ' in s else '')]
    terms = sorted({t for t in capitalised if t.lower() not in corpus_words and t.lower() not in v.STOPWORDS})
    unsupported = []
    if change['field'].startswith(CLAIM_FIELDS):
        for sentence in sentences:
            if len(v.content_tokens(sentence)) < MIN_CLAIM_TOKENS:
                continue
            ratio, missing = v.support_ratio(sentence, corpus_tokens, allowed_tokens)
            if ratio < LIMITS['CLAIM_SUPPORT_MIN'][1]:
                unsupported.append({'sentence': sentence[:200], 'support': ratio, 'not_in_facts': missing[:10]})
    return {'needs_verification': numbers_bad, 'unverified_terms': terms[:15], 'unsupported_claims': unsupported,
            'placeholders': v.VERIFY.findall(joined)}


def helpfulness(ctx, prior, proposed_text):
    """Does the proposed content answer the primary intent and each question, and close the competitors' gaps?"""
    kw, plan = keywords_of(prior), plan_of(prior)
    current = (prior.get('keyword_plan') or {}).get('data') or {}
    answer = plan.get('direct_answer') or ''
    primary = kw['primary']
    wanted = v.content_tokens(primary or '')
    primary_answered = bool(answer) and bool(wanted) and len(wanted & v.content_tokens(answer)) / len(wanted) >= 0.6
    headings = [s.get('heading') or '' for s in plan.get('sections') or []] + [q.get('question') or '' for q in plan.get('faq') or []] \
        + [o.get('text') or '' for o in plan.get('outline') or []]
    unanswerable = {u.get('question') for u in plan.get('unanswerable') or [] if isinstance(u, dict)}
    questions = {}
    for q in kw['questions']:
        if (current.get('questions_current') or {}).get(q, {}).get('answered'):
            questions[q] = 'answered (current page)'
        elif q in (plan.get('questions_covered') or {}) or any(v.covers_question(q, h, ignore=names_of(ctx, prior)) for h in headings):
            questions[q] = 'answered (proposed)'
        elif q in unanswerable:
            questions[q] = 'needs owner facts'
        else:
            questions[q] = 'unanswered'
    have = v.content_tokens(proposed_text)
    gaps = {}
    for gap in kw['content_gaps']:
        tokens = v.content_tokens(gap)
        gaps[gap] = bool(tokens) and len(tokens & have) / len(tokens) >= 0.5
    return {'primary_intent_answered': primary_answered, 'questions': questions,
            'content_gaps_addressed': [g for g, ok in gaps.items() if ok], 'content_gaps_open': [g for g, ok in gaps.items() if not ok]}


def verdict(blocked, help_, stale, plan_done):
    reasons = []
    if blocked:
        reasons.append(f'{len(blocked)} change(s) blocked: claims not supported by the page facts')
    if not plan_done:
        reasons.append('no page plan (content was not generated)')
    elif not help_['primary_intent_answered']:
        reasons.append('the direct answer does not address the primary keyword/intent')
    unanswered = [q for q, s in help_['questions'].items() if s == 'unanswered']
    if unanswered:
        reasons.append('questions not answered: ' + '; '.join(unanswered[:5]))
    if [q for q, s in help_['questions'].items() if s == 'needs owner facts']:
        reasons.append('some questions need facts from the owner')
    if help_['content_gaps_open']:
        reasons.append('competitor topics not covered: ' + '; '.join(help_['content_gaps_open'][:5]))
    if stale:
        reasons.append('outdated years in freshness-sensitive copy')
    if blocked or not plan_done or not help_['primary_intent_answered']:
        label = 'fail'
    elif reasons:
        label = 'needs_work'
    else:
        label = 'pass'
    return {'verdict': label, 'reasons': reasons}


def check_fact_check(ctx, prior, writer=None):
    corpus = ctx['corpus']
    corpus_words = set(re.findall(r'[a-z0-9]+', corpus.lower()))
    corpus_tokens = v.content_tokens(corpus)
    allowed_tokens = keyword_tokens(ctx, prior)
    allowed = numbers_allowed(ctx)
    report, inputs, blocked, proposed_texts = [], [], [], []
    from seo_agents.onpage.checks.checklist import CHECKS  # registry imports this module
    written_by_model = {check.name for check in CHECKS if check.uses_model}
    for check, res in prior.items():
        if check not in written_by_model:
            continue
        for change in (res or {}).get('changes') or []:
            found = verify_change(change, ctx, corpus_words, corpus_tokens, allowed_tokens, allowed)
            change['evidence']['fact_check'] = found
            proposed_texts += list(_strings(change.get('value')))
            if found['needs_verification'] or found['unverified_terms'] or found['unsupported_claims']:
                # Unsupported claim: flagged and the change is blocked until the owner confirms it.
                change['status'] = 'blocked'
                change['blocked_by'] = 'fact_check'
                blocked.append(change['id'])
                report.append({'change': change['id'], 'field': change['field'], 'numbers': found['needs_verification'],
                               'terms': found['unverified_terms'], 'unsupported': found['unsupported_claims']})
                inputs.append(ops.client_input(ctx, 'Confirm or correct before applying: ' + '; '.join(
                    found['needs_verification'] + found['unverified_terms']
                    + [u['sentence'][:80] for u in found['unsupported_claims']])[:300], change=change['id'], field=change['field']))
            elif found['placeholders']:
                change['status'] = 'needs_input'
            inputs += [ops.client_input(ctx, p, change=change['id'], field=change['field']) for p in found['placeholders']]
    run_year = int(ctx['run_date'][:4])
    stale = stale_years(ctx['copy'], run_year)
    stale_proposed = stale_years(proposed_texts, run_year)
    future = sorted({int(m.group(0)) for t in proposed_texts for m in YEAR.finditer(v.VERIFY.sub(' ', t)) if int(m.group(0)) > run_year})
    changes, advice = [], []
    if stale:
        advice.append(ops.advice(ctx, 'fact_check', 'content.freshness', f'Outdated years in freshness-sensitive copy (as of {run_year})',
                                 impact='low', evidence=stale))
    if stale_proposed or future:
        advice.append(ops.advice(ctx, 'fact_check', 'content.freshness.proposed',
                                 'Proposed copy mentions outdated or future years; confirm them before applying', impact='medium',
                                 evidence={'stale': stale_proposed, 'future_years': future}))
    has_date = any(s.get('signal') == 'dates' and s.get('status') == 'observed' for s in ctx['eeat_observed'])
    if page_type(ctx, prior) == 'article' and not has_date:  # personal/home pages do not get a "Last updated" line
        anchor = first_main_edit(ctx, heading=True)
        changes.append(ops.make_change(ctx, 'fact_check', 'freshness.published', None, '[VERIFY: publication date]',
                                       target=ops.target_of(anchor, placement='under the post title') if anchor else None, op='insert_node',
                                       confidence=0.6, html='<time datetime="[VERIFY]">[VERIFY: publication date]</time>',
                                       rationale='Visible publication/updated date for a post (mirrored in BlogPosting dates)', status='needs_input'))
        inputs.append(ops.client_input(ctx, 'Publication date for this post', change=changes[-1]['id'], field='freshness.published'))
    help_ = helpfulness(ctx, prior, ' '.join(proposed_texts))
    if help_['content_gaps_open']:
        advice.append(ops.advice(ctx, 'fact_check', 'content.depth', 'Topics competitors cover that the page and the proposal do not',
                                 impact='medium', evidence=help_['content_gaps_open']))
    plan_done = (prior.get('page_plan') or {}).get('status') == 'done'
    quality = {**verdict(blocked, help_, stale + stale_proposed + [{'year': y} for y in future], plan_done),
               'blocked_changes': blocked, 'helpfulness': help_, 'keyword_source': keywords_of(prior)['source']}
    return result(data={'claims_report': report, 'stale': stale, 'stale_proposed': stale_proposed, 'future_years': future,
                        'quality': quality,
                        'policy': 'numbers (digits and spelled-out) and names must appear in the visible text/data JSON, and each '
                                  'proposed sentence needs most of its content words in the page facts; otherwise the change is '
                                  'blocked and the owner confirms it (client_inputs)'},
                  changes=changes, advice=advice, client_inputs=inputs)


class FactCheckFreshnessCheck(OnpageCheck):
    """Check: Verify every proposed claim (blocks unsupported ones), helpfulness, freshness, per-page quality verdict (deterministic)."""

    name = 'fact_check'
    skill = 'fact-check-freshness'
    uses_model = False
    depends_on = ()

    def run(self, ctx, prior, writer):
        return check_fact_check(ctx, prior, writer)
