"""Check 5 (model, small, optional): E-E-A-T and answer-engine advice plus facts only the owner can supply."""

from __future__ import annotations

import os
import re

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.validation import content_validators as v
from seo_agents.onpage.page_context.page_context_builder import facts
from seo_agents.onpage.checks.check_helpers import ADVICE_TOPICS, brand, numbers_allowed, page_type, plan_of, result, site_type
from seo_agents.onpage.checks.base_check import OnpageCheck

# ====================================================================== 5. E-E-A-T / AEO advice (small, optional)
ADVICE_SCHEMA = {'type': 'object', 'required': ['advice', 'missing_facts']}


def check_advice(ctx, prior, writer):
    if os.getenv('SEO_ONPAGE_ADVICE', '1') == '0':
        return result(status='skipped', data={'reason': 'SEO_ONPAGE_ADVICE=0'})
    kind, ptype = site_type(ctx, prior), page_type(ctx, prior)
    packet = {'eeat_observed': ctx['eeat_observed'], 'site_type': kind, 'page_type': ptype, 'brand': brand(ctx, prior),
              'facts': facts(ctx)[:60], 'profiles': ctx['profiles'], 'topics': sorted(ADVICE_TOPICS),
              'plan': {k: plan_of(prior).get(k) for k in ('h1', 'direct_answer')},
              'stack': {'framework': ctx['framework'], 'head_scope': ctx['head_scope']}}
    personal = kind == 'personal' and ptype != 'article'

    def validate(a):
        v.require(a, 'advice', 'missing_facts')
        if not isinstance(a['advice'], list) or len(a['advice']) > 6:
            raise ValueError('advice must be a list of at most 6 items')
        for i, item in enumerate(a['advice']):
            v.one_of((item or {}).get('topic'), ADVICE_TOPICS, f'advice[{i}].topic')
            v.text_length(item.get('text'), 10, 300, f'advice[{i}].text')
            v.grounded(item['text'], ctx['corpus'], f'advice[{i}].text', numbers_allowed(ctx))
            if personal and re.search(r'(?i)author box|last[- ]updated|byline', item['text']):
                raise ValueError(f'advice[{i}]: a personal {ptype} page needs no author box / "last updated" line')
        a['missing_facts'] = v.string_list(a['missing_facts'], 0, 6, 'missing_facts')
        return a

    answer, meta = writer.write('eeat-aeo-advice', packet, validate, ADVICE_SCHEMA)
    advice = [ops.advice(ctx, 'advice', item['topic'], item['text'], impact='low') for item in answer['advice']]
    inputs = [ops.client_input(ctx, needed) for needed in answer['missing_facts']]
    return result(data=answer, advice=advice, client_inputs=inputs, meta=meta)


class EeatAdviceCheck(OnpageCheck):
    """Check: Advice (not copy) on E-E-A-T and answer-engine gaps, plus facts only the owner can supply (model, small)."""

    name = 'advice'
    skill = 'eeat-aeo-advice'
    uses_model = True
    depends_on = ('intent',)

    def run(self, ctx, prior, writer):
        return check_advice(ctx, prior, writer)
