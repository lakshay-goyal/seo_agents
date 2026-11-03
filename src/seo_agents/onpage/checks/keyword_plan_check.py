"""Check 2 (deterministic): where the target keywords appear today, which questions/prompts the page already answers,
keyword stuffing, and the content alignment gaps the page plan must close.

Keywords come from search-intent: researched (`keyword-research`, from seo_reports/keywords/keyword_map.json) or model
hypotheses (`model-hypothesis`) when no keyword map exists.
"""

from __future__ import annotations

from seo_agents.onpage.validation import content_validators as v
from seo_agents.onpage.validation.content_policy import LIMITS
from seo_agents.onpage.checks.check_helpers import brand, intent_of, keywords_of, names_of, question_coverage, result
from seo_agents.onpage.checks.base_check import OnpageCheck

PLACEMENTS_REQUIRED = ['title', 'h1', 'first_100_words', 'meta_description']
SELF_START = ('it ', 'this ', 'these ', 'they ', 'he ', 'she ', 'we ', 'here ', 'that ')


# ====================================================================== 2. keyword plan (deterministic)
def contains_query(text, query):
    return v.contains_query(text, query)


def placements(ctx, query, copy, first_100, h1, h2):
    return {
        'title': contains_query(ctx['title'], query), 'h1': any(contains_query(t, query) for t in h1),
        'first_100_words': contains_query(first_100, query), 'h2': any(contains_query(t, query) for t in h2),
        'meta_description': contains_query(ctx['description'], query),
        'image_alt': any(contains_query(i.get('alt'), query) for i in ctx['images']),
        'body_count': v.phrase_count(copy, query),
    }


def quotable_passage(ctx, prompt, entities):
    """GEO: a self-contained paragraph that answers the prompt and names the entity explicitly (quotable alone)."""
    low, high = LIMITS['QUOTABLE_PASSAGE_WORDS']
    wanted = v.content_tokens(prompt)
    best = None
    for s in ctx['sections']:
        for b in s['blocks']:
            text = b.get('text') or ''
            count = len(v.words(text))
            if not low <= count <= high or not wanted:
                continue
            overlap = len(wanted & v.content_tokens(text)) / len(wanted)
            if overlap < 0.5:
                continue
            explicit = any(e and v.phrase_count(text, e) for e in entities)
            self_contained = not text.lower().startswith(SELF_START) and not v.SELF_REFERENCE.search(text)
            row = {'passage': text[:200], 'overlap': round(overlap, 2), 'entity_explicit': explicit, 'self_contained': self_contained}
            if best is None or (explicit and self_contained, overlap) > (best['entity_explicit'] and best['self_contained'], best['overlap']):
                best = row
    return best or {'passage': None, 'overlap': 0, 'entity_explicit': False, 'self_contained': False}


def stuffing(copy, queries):
    total = len(v.words(copy))
    cap = max(3, total // 100 + 1)
    flags = [f'{t} {p}%' for t, p in v.density(copy).items() if p > LIMITS['MAX_TERM_DENSITY_PCT'][1] and total >= 150]
    flags += [f'"{q}" x{v.phrase_count(copy, q)} (max {cap})' for q in queries if q and v.phrase_count(copy, q) > cap]
    return flags


def check_keyword_plan(ctx, prior, writer=None):
    intent = intent_of(prior)
    kw = keywords_of(prior)
    primary = kw['primary']
    copy = ' '.join(ctx['copy'])
    first_100 = ' '.join(v.words(copy)[:100])
    h1 = [h['text'] for h in ctx['headings'] if h['level'] == 1]
    h2 = [h['text'] for h in ctx['headings'] if h['level'] == 2]
    primary_places = placements(ctx, primary, copy, first_100, h1, h2)
    secondary_places = {q: placements(ctx, q, copy, first_100, h1, h2) for q in kw['secondary']}
    names = names_of(ctx, prior)
    questions = {q: question_coverage(ctx, q, names) for q in kw['questions']}
    entities = [brand(ctx, prior)] + list(intent.get('entities') or [])
    prompts = {p: quotable_passage(ctx, p, entities) for p in kw['conversational']}
    dens = v.density(copy)
    queries = [primary] + kw['secondary']
    gaps = [f'primary "{primary}" missing from {where}' for where in PLACEMENTS_REQUIRED if primary and not primary_places[where]]
    gaps += [f'secondary "{q}" does not appear anywhere on the page' for q, pl in secondary_places.items()
             if not any(pl[k] for k in ('title', 'h1', 'h2', 'first_100_words', 'meta_description', 'image_alt')) and not pl['body_count']]
    gaps += [f'question "{q}" is not answered under an H2/H3' for q, c in questions.items() if not c['answered']]
    gaps += [f'no self-contained, entity-explicit passage answers "{p}"' for p, row in prompts.items()
             if not (row['passage'] and row['entity_explicit'] and row['self_contained'])]
    gaps += [f'competitors cover: {g}' for g in kw['content_gaps']]
    data = {'primary': primary, 'secondary': kw['secondary'], 'questions': kw['questions'], 'conversational': kw['conversational'],
            'entities': intent.get('entities') or [], 'source': kw['source'],
            'placements_current': primary_places, 'placements_required': PLACEMENTS_REQUIRED,
            'secondary_placements_current': secondary_places,
            'questions_current': questions, 'conversational_current': prompts,
            'query_counts_current': {q: v.phrase_count(copy, q) for q in queries if q},
            'top_density_current': dict(sorted(dens.items(), key=lambda kv: -kv[1])[:10]),
            'stuffing_flags': stuffing(copy, queries),
            'alignment_gaps': gaps,
            'limits': {k: LIMITS[k] for k in ('PRIMARY_PHRASE_PER_100_WORDS', 'MAX_TERM_DENSITY_PCT', 'QUESTION_ANSWER_WORDS')}}
    return result(data=data)


class KeywordPlanCheck(OnpageCheck):
    """Check: Measure primary/secondary placements, question and prompt coverage, stuffing and alignment gaps (deterministic)."""

    name = 'keyword_plan'
    skill = 'keyword-integration'
    uses_model = False
    depends_on = ('intent',)

    def run(self, ctx, prior, writer):
        return check_keyword_plan(ctx, prior, writer)
