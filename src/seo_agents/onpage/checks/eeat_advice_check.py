"""Check 5 (model, small, optional): E-E-A-T, AEO and GEO advice tied to this page, plus facts only the owner can supply.

Experience / expertise / authoritativeness / trust (author bio, credentials, first-hand evidence, sources, contact and
policy pages) and answer-engine advice (llms.txt, entity consistency, sameAs, FAQ/HowTo markup only where that content
exists). Advice only; no page copy.
"""

from __future__ import annotations

import os
import re

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.validation import content_validators as v
from seo_agents.onpage.page_context.page_context_builder import facts
from seo_agents.onpage.checks.check_helpers import (ADVICE_TOPICS, brand, intent_of, keywords_of, numbers_allowed, page_type,
                                                    plan_of, result, site_type)
from seo_agents.onpage.checks.base_check import OnpageCheck

# ====================================================================== 5. E-E-A-T / AEO advice (small, optional)
ADVICE_SCHEMA = {'type': 'object', 'required': ['advice', 'missing_facts']}


def check_advice(ctx, prior, writer):
    if os.getenv('SEO_ONPAGE_ADVICE', '1') == '0':
        return result(status='skipped', data={'reason': 'SEO_ONPAGE_ADVICE=0'})
    kind, ptype = site_type(ctx, prior), page_type(ctx, prior)
    plan, kw, intent = plan_of(prior), keywords_of(prior), intent_of(prior)
    content = content_signals(ctx, plan)
    packet = {'eeat_observed': ctx['eeat_observed'], 'site_type': kind, 'page_type': ptype, 'brand': brand(ctx, prior),
              'facts': facts(ctx)[:60], 'profiles': ctx['profiles'], 'topics': sorted(ADVICE_TOPICS),
              'plan': {k: plan.get(k) for k in ('h1', 'direct_answer')},
              'keywords': {'primary': kw['primary'], 'questions': kw['questions'], 'source': kw['source'],
                           'intent': intent.get('intent'), 'journey_stage': intent.get('journey_stage')},
              'entities': {'brand_candidates': ctx['brand_candidates'], 'entities': intent.get('entities') or []},
              'content': content,
              'stack': {'framework': ctx['framework'], 'head_scope': ctx['head_scope']}}
    personal = kind == 'personal' and ptype != 'article'

    def validate(a):
        v.require(a, 'advice', 'missing_facts')
        if not isinstance(a['advice'], list) or len(a['advice']) > 8:
            raise ValueError('advice must be a list of at most 8 items')
        for i, item in enumerate(a['advice']):
            v.one_of((item or {}).get('topic'), ADVICE_TOPICS, f'advice[{i}].topic')
            v.text_length(item.get('text'), 10, 300, f'advice[{i}].text')
            v.grounded(item['text'], ctx['corpus'], f'advice[{i}].text', numbers_allowed(ctx))
            if personal and re.search(r'(?i)author box|last[- ]updated|byline', item['text']):
                raise ValueError(f'advice[{i}]: a personal {ptype} page needs no author box / "last updated" line')
            if item['topic'] == 'aeo.faq_schema' and not content['has_faq']:
                raise ValueError(f'advice[{i}]: FAQPage markup only where a visible FAQ / question headings exist (none here)')
            if item['topic'] == 'aeo.howto' and not content['has_steps']:
                raise ValueError(f'advice[{i}]: HowTo guidance only where the page has step-by-step content (none here)')
        a['missing_facts'] = v.string_list(a['missing_facts'], 0, 6, 'missing_facts')
        return a

    answer, meta = writer.write('eeat-aeo-advice', packet, validate, ADVICE_SCHEMA)
    advice = [ops.advice(ctx, 'advice', item['topic'], item['text'], impact='low') for item in answer['advice']]
    inputs = [ops.client_input(ctx, needed) for needed in answer['missing_facts']]
    return result(data=answer, advice=advice, client_inputs=inputs, meta=meta)


def content_signals(ctx, plan):
    """What the page (or its plan) actually contains, so FAQ/HowTo advice is only given where the content exists."""
    blocks = [b for s in ctx['sections'] for b in s['blocks']] + [b for s in plan.get('sections') or [] for b in s.get('blocks') or []]
    headings = [h['text'] for h in ctx['headings']] + [o.get('text') or '' for o in plan.get('outline') or []]
    return {'has_faq': bool(plan.get('faq')) or any(t.strip().endswith('?') for t in headings),
            'has_steps': any(b.get('tag') == 'ol' for b in blocks),
            'has_dates': any(s.get('signal') == 'dates' and s.get('status') == 'observed' for s in ctx['eeat_observed']),
            'external_sources': len(ctx['external_links'])}


class EeatAdviceCheck(OnpageCheck):
    """Check: Advice (not copy) on E-E-A-T and answer-engine gaps, plus facts only the owner can supply (model, small)."""

    name = 'advice'
    skill = 'eeat-aeo-advice'
    uses_model = True
    depends_on = ('intent',)

    def run(self, ctx, prior, writer):
        return check_advice(ctx, prior, writer)
