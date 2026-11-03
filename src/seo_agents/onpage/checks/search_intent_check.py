"""Check 1 (model, small): the page's primary query, search intent, journey stage, questions, site/page type."""

from __future__ import annotations

from seo_agents.onpage.validation import content_validators as v
from seo_agents.onpage.page_context.page_context_builder import page_brief
from seo_agents.onpage.validation.content_policy import PAGE_TYPES, SITE_TYPES
from seo_agents.onpage.checks.check_helpers import INTENTS, ROLES, STAGES, result, site_url_set
from seo_agents.onpage.checks.base_check import OnpageCheck

# ====================================================================== 1. search intent (small)
INTENT_SCHEMA = {'type': 'object', 'required': ['primary_topic', 'intent', 'journey_stage', 'primary_query', 'secondary_queries',
                                                 'entities', 'user_questions', 'cluster', 'brand_name', 'site_type', 'page_type']}


def check_intent(ctx, prior, writer):
    urls = site_url_set(ctx)
    packet = {'page': page_brief(ctx), 'framework': ctx['fingerprint'], 'site_type_hint': ctx['site_type'],
              'site': {'brand_candidates': ctx['brand_candidates'], 'urls': [{'url': u['url'], 'title': u['title']} for u in ctx['site_urls']]},
              'exploration_keywords': ctx['exploration_keywords']}

    def validate(a):
        v.require(a, 'primary_topic', 'intent', 'journey_stage', 'primary_query', 'secondary_queries', 'entities',
                  'user_questions', 'cluster', 'brand_name')
        v.one_of(a['intent'], INTENTS, 'intent')
        v.one_of(a['journey_stage'], STAGES, 'journey_stage')
        v.text_length(a['primary_query'], 3, 70, 'primary_query')
        a['primary_query'] = a['primary_query'].lower().strip()
        a['secondary_queries'] = v.string_list(a['secondary_queries'], 0, 6, 'secondary_queries')
        a['user_questions'] = v.string_list(a['user_questions'], 0, 6, 'user_questions')
        a['entities'] = v.string_list(a['entities'], 1, 20, 'entities')
        v.text_length(a['brand_name'], 2, 80, 'brand_name')
        a['site_type'] = a.get('site_type') if a.get('site_type') in SITE_TYPES else ctx['site_type']
        a['page_type'] = a.get('page_type') if a.get('page_type') in PAGE_TYPES else None
        cluster = a['cluster'] if isinstance(a['cluster'], dict) else {}
        v.one_of(cluster.get('role'), ROLES, 'cluster.role')
        cluster['pillar_url'] = cluster.get('pillar_url') if cluster.get('pillar_url') in urls else None
        cluster['related_urls'] = [u for u in cluster.get('related_urls') or [] if u in urls and u != ctx['url']]
        a['cluster'] = cluster
        # Queries are model hypotheses, not measured search demand (audit §8 topic-intent).
        a['query_source'] = 'model-hypothesis'
        return a

    answer, meta = writer.write('search-intent', packet, validate, INTENT_SCHEMA)
    return result(data=answer, meta=meta)


class SearchIntentCheck(OnpageCheck):
    """Check: Decide the primary query, search intent, journey stage, user questions, site/page type (model, small)."""

    name = 'intent'
    skill = 'search-intent'
    uses_model = True
    depends_on = ()

    def run(self, ctx, prior, writer):
        return check_intent(ctx, prior, writer)
