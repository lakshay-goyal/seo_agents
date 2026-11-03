"""Check 1 (model, small): the page's primary query, search intent, journey stage, questions, site/page type.

With a researched keyword plan (seo_reports/keywords/keyword_map.json) the keyword fields come from the plan
(`query_source: keyword-research`) and the model only decides brand / site type / page type / entities / answer gap; the
validator overwrites any keyword the model returns. Without a plan the model's queries are labelled `model-hypothesis`.
"""

from __future__ import annotations

from seo_agents.onpage.validation import content_validators as v
from seo_agents.onpage.page_context.page_context_builder import page_brief
from seo_agents.onpage.validation.content_policy import PAGE_TYPES, SITE_TYPES
from seo_agents.onpage.checks.check_helpers import HYPOTHESIS, INTENTS, RESEARCHED, ROLES, STAGES, researched_plan, result, site_url_set
from seo_agents.onpage.checks.base_check import OnpageCheck

# ====================================================================== 1. search intent (small)
INTENT_SCHEMA = {'type': 'object', 'required': ['primary_topic', 'intent', 'journey_stage', 'primary_query', 'secondary_queries',
                                                 'entities', 'user_questions', 'cluster', 'brand_name', 'site_type', 'page_type']}
RESEARCHED_SCHEMA = {'type': 'object', 'required': ['primary_topic', 'entities', 'brand_name', 'site_type', 'page_type', 'answer_gap']}
KEYWORD_FIELDS = ('primary_query', 'secondary_queries', 'user_questions', 'intent', 'journey_stage', 'cluster')


def researched_fields(plan, urls, page_url):
    """Keyword fields taken verbatim from the researched plan (None = the plan has no value; the model decides it)."""
    cluster = plan.get('cluster') or {}
    researched_cluster = None
    if cluster.get('role') in ROLES:
        researched_cluster = {'role': cluster['role'],
                              'pillar_url': cluster.get('pillar_url') if cluster.get('pillar_url') in urls else None,
                              'related_urls': [u for u in cluster.get('related_urls') or [] if u in urls and u != page_url]}
    return {'primary_query': plan['primary'], 'secondary_queries': list(plan.get('secondary') or []),
            'user_questions': list(plan.get('questions') or []),
            'intent': plan.get('intent') if plan.get('intent') in INTENTS else None,
            'journey_stage': plan.get('journey_stage') if plan.get('journey_stage') in STAGES else None,
            'cluster': researched_cluster}


def check_intent(ctx, prior, writer):
    urls = site_url_set(ctx)
    plan = researched_plan(ctx)
    fixed = researched_fields(plan, urls, ctx['url']) if plan else {}
    packet = {'page': page_brief(ctx), 'framework': ctx['fingerprint'], 'site_type_hint': ctx['site_type'],
              'site': {'brand_candidates': ctx['brand_candidates'], 'urls': [{'url': u['url'], 'title': u['title']} for u in ctx['site_urls']]},
              'exploration_keywords': ctx['exploration_keywords']}
    if plan:
        packet['researched'] = {k: val for k, val in fixed.items() if val is not None}
        packet['researched']['conversational'] = plan.get('conversational') or []
    # Fields the model must still answer: everything without a researched value.
    model_keys = [k for k in KEYWORD_FIELDS if fixed.get(k) is None] if plan else list(KEYWORD_FIELDS)

    def validate(a):
        v.require(a, 'primary_topic', 'entities', 'brand_name', *model_keys)
        hypothesis = {k: a.get(k) for k in KEYWORD_FIELDS if k in a} if plan else None
        if 'intent' in model_keys:
            v.one_of(a['intent'], INTENTS, 'intent')
        if 'journey_stage' in model_keys:
            v.one_of(a['journey_stage'], STAGES, 'journey_stage')
        if 'primary_query' in model_keys:
            v.text_length(a['primary_query'], 3, 70, 'primary_query')
            a['primary_query'] = a['primary_query'].lower().strip()
        if 'secondary_queries' in model_keys:
            a['secondary_queries'] = v.string_list(a['secondary_queries'], 0, 6, 'secondary_queries')
        if 'user_questions' in model_keys:
            a['user_questions'] = v.string_list(a['user_questions'], 0, 6, 'user_questions')
        a['entities'] = v.string_list(a['entities'], 1, 20, 'entities')
        v.text_length(a['brand_name'], 2, 80, 'brand_name')
        a['site_type'] = a.get('site_type') if a.get('site_type') in SITE_TYPES else ctx['site_type']
        a['page_type'] = a.get('page_type') if a.get('page_type') in PAGE_TYPES else None
        if 'cluster' in model_keys:
            cluster = a['cluster'] if isinstance(a['cluster'], dict) else {}
            v.one_of(cluster.get('role'), ROLES, 'cluster.role')
            cluster['pillar_url'] = cluster.get('pillar_url') if cluster.get('pillar_url') in urls else None
            cluster['related_urls'] = [u for u in cluster.get('related_urls') or [] if u in urls and u != ctx['url']]
            a['cluster'] = cluster
        if not plan:
            # Queries are model hypotheses, not measured search demand (audit §8 topic-intent).
            a['query_source'] = HYPOTHESIS
            return a
        # Researched keywords always win: the model's keyword fields are discarded (kept only for audit).
        for key, val in fixed.items():
            if val is not None:
                a[key] = val
        a['conversational'] = list(plan.get('conversational') or [])
        a['short_tail'] = list(plan.get('short_tail') or [])
        a['content_gaps'] = list(plan.get('content_gaps') or [])
        a['competitor_urls'] = list(plan.get('competitor_urls') or [])
        a['query_source'] = RESEARCHED
        a['market'] = (ctx.get('keyword_research') or {}).get('market')
        a['provenance'] = {**{k: (ctx.get('keyword_research') or {}).get(k) for k in ('source', 'schema', 'generated_at')},
                           'plan_status': plan.get('status'), 'iterations': plan.get('iterations'),
                           'from_plan': sorted(k for k, val in fixed.items() if val is not None)}
        dropped = {k: val for k, val in (hypothesis or {}).items() if k not in model_keys and val not in (None, [], {})}
        if dropped:
            a['model_hypothesis_ignored'] = dropped
        return a

    schema = {**RESEARCHED_SCHEMA, 'required': RESEARCHED_SCHEMA['required'] + model_keys} if plan else INTENT_SCHEMA
    answer, meta = writer.write('search-intent', packet, validate, schema)
    return result(data=answer, meta=meta)


class SearchIntentCheck(OnpageCheck):
    """Check: Decide the primary query, search intent, journey stage, user questions, site/page type (model, small).
    Researched keywords (ctx['keyword_plan']) are fixed inputs; the model never overrides them."""

    name = 'intent'
    skill = 'search-intent'
    uses_model = True
    depends_on = ()

    def run(self, ctx, prior, writer):
        return check_intent(ctx, prior, writer)
