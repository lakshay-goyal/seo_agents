"""Check 2 (deterministic): where the primary query appears today, and keyword stuffing."""

from __future__ import annotations

from seo_agents.onpage.validation import content_validators as v
from seo_agents.onpage.validation.content_policy import LIMITS
from seo_agents.onpage.checks.check_helpers import intent_of, result
from seo_agents.onpage.checks.base_check import OnpageCheck

# ====================================================================== 2. keyword plan (deterministic)
def contains_query(text, query):
    if not text or not query:
        return False
    if v.phrase_count(text, query):
        return True
    tokens = [w for w in v.words(query) if w not in v.STOPWORDS]
    have = set(v.words(text))
    return bool(tokens) and all(t in have for t in tokens)


def check_keyword_plan(ctx, prior, writer=None):
    intent = intent_of(prior)
    primary = intent.get('primary_query')
    copy = ' '.join(ctx['copy'])
    first_100 = ' '.join(v.words(copy)[:100])
    h1 = [h['text'] for h in ctx['headings'] if h['level'] == 1]
    h2 = [h['text'] for h in ctx['headings'] if h['level'] == 2]
    placements = {
        'title': contains_query(ctx['title'], primary), 'h1': any(contains_query(t, primary) for t in h1),
        'first_100_words': contains_query(first_100, primary), 'h2': any(contains_query(t, primary) for t in h2),
        'meta_description': contains_query(ctx['description'], primary),
        'image_alt': any(contains_query(i.get('alt'), primary) for i in ctx['images']),
    }
    dens = v.density(copy)
    queries = [primary] + list(intent.get('secondary_queries') or [])
    data = {'primary': primary, 'secondary': intent.get('secondary_queries') or [], 'entities': intent.get('entities') or [],
            'source': intent.get('query_source'), 'placements_current': placements,
            'placements_required': ['title', 'h1', 'first_100_words', 'meta_description'],
            'query_counts_current': {q: v.phrase_count(copy, q) for q in queries if q},
            'top_density_current': dict(sorted(dens.items(), key=lambda kv: -kv[1])[:10]),
            'stuffing_flags': [f'{t} {p}%' for t, p in dens.items() if p > LIMITS['MAX_TERM_DENSITY_PCT'][1] and len(v.words(copy)) >= 150],
            'limits': {k: LIMITS[k] for k in ('PRIMARY_PHRASE_PER_100_WORDS', 'MAX_TERM_DENSITY_PCT')}}
    return result(data=data)


class KeywordPlanCheck(OnpageCheck):
    """Check: Measure where the primary query appears today and flag stuffing (deterministic)."""

    name = 'keyword_plan'
    skill = 'keyword-integration'
    uses_model = False
    depends_on = ('intent',)

    def run(self, ctx, prior, writer):
        return check_keyword_plan(ctx, prior, writer)
