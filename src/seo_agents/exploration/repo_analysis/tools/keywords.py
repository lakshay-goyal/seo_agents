"""Weighted keyword candidates from the repository's served text (repository prose such as README is excluded).

Compact by design: counts per field plus at most `limits.examples` file:line examples per keyword, no
occurrence lists or text snippets (they dominated repo_context.json size).
"""

from collections import Counter, defaultdict
import re

from seo_agents.exploration.repo_analysis.tools.skill_runner import DOC_FILES, skill

WORD = re.compile(r"[^\W\d_][\w'’-]*[^\W_]|[^\W\d_]", re.UNICODE)
STOPWORDS = set('''
a about above after again against all also am an and any are as at be because been before being below between both
but by can could did do does doing down during each else etc even every few for from further get gets got had has have
having he her here hers herself him himself his how i if in into is it its itself just let lets like make made many may
me more most much must my myself new no nor not now of off on once one only or other our ours ourselves out over own
per same see she should so some such than that the their theirs them themselves then there these they this those
through to too under until up us use used using very via want was we were what when where which while who whom why
will with within without would yes yet you your yours yourself yourselves s t don re ve ll d m page click here read
more learn view home true false null undefined div span class classname href src alt img px rem em http https www com
'''.split())


def tokens(text):
    words = [w.casefold().strip("'’-") for w in WORD.findall(text or '')]
    return [w for w in words if len(w) > 1]


def ngrams(words, n):
    for i in range(len(words) - n + 1):
        gram = words[i:i + n]
        if gram[0] in STOPWORDS or gram[-1] in STOPWORDS or any(len(w) < 2 for w in gram):
            continue
        if n == 1 and (gram[0] in STOPWORDS or len(gram[0]) < 3):
            continue
        yield ' '.join(gram)


def _sources(onpage, routes):
    """(field, text, record) for every text-bearing record."""
    meta = onpage['metadata']
    for record in meta['title_tags']:
        yield 'title', record['value'], record
    for value in meta['declared_values']:
        field = {'title': 'title', 'description': 'description', 'keywords': 'declared'}.get(value['field'])
        if field:
            yield field, value['value'], value
    for record in meta['meta_tags']:
        name = (record.get('name') or '').lower()
        if name in {'description', 'og:description', 'twitter:description'}:
            yield 'description', record['value'], record
        elif name in {'og:title', 'twitter:title'}:
            yield 'title', record['value'], record
        elif name == 'keywords':
            yield 'declared', record['value'], record
    for record in onpage['headings']:
        yield ('h1' if record['level'] == 1 else 'h2' if record['level'] == 2 else 'h3' if record['level'] == 3
               else 'heading'), record['text'], record
    for record in onpage['front_matter']:
        fields = record.get('fields', {})
        for key, field in (('title', 'title'), ('description', 'description'), ('summary', 'description'),
                           ('tags', 'declared'), ('keywords', 'declared'), ('categories', 'declared')):
            value = fields.get(key)
            if value:
                yield field, ', '.join(value) if isinstance(value, list) else str(value), record
    for record in onpage['text_blocks']:
        yield 'body', record['text'], record
    for record in onpage['data_content']:
        yield 'body', record['text'], record
    for record in onpage['media']:
        if record.get('alt'):
            yield 'alt', record['alt'], record
    for route in routes:
        if route['kind'] in {'page', 'content-page'} and not route['is_dynamic']:
            yield 'url', route['route'].replace('-', ' ').replace('/', ' '), route['source']


def doc_file(path):
    # docs/ folders are filtered upstream by SourceIndex.is_doc (which knows whether a docs site serves them).
    return bool(DOC_FILES.search(path))


def extract_keywords(onpage, routes):
    profile = skill('keywords')['profile']
    weights, limits = profile['weights'], profile['limits']
    scores = {n: Counter() for n in (1, 2, 3)}
    frequency = {n: Counter() for n in (1, 2, 3)}
    fields = {n: defaultdict(Counter) for n in (1, 2, 3)}
    occurrences = {n: defaultdict(list) for n in (1, 2, 3)}
    per_file = defaultdict(Counter)
    declared = []
    for field, text, record in _sources(onpage, routes):
        if not isinstance(text, str) or not text.strip():
            continue
        location = record.get('location', {})
        if location.get('file') and doc_file(location['file']):
            continue  # repository prose (README, CHANGELOG, docs/) is never served content
        if field == 'declared':
            declared.append({'value': text, 'file': location.get('file'), 'line': location.get('line_start')})
        words = tokens(text)
        for n in (1, 2, 3):
            for gram in ngrams(words, n):
                scores[n][gram] += weights.get(field, 1)
                frequency[n][gram] += 1
                fields[n][gram][field] += 1
                if len(occurrences[n][gram]) < limits.get('examples', 2) and location.get('file'):
                    example = location['file'] + ':' + str(location.get('line_start') or 1)
                    if example not in occurrences[n][gram]:
                        occurrences[n][gram].append(example)
                if location.get('file') and n <= 2:
                    per_file[location['file']][gram] += weights.get(field, 1)

    def ranked(n, limit):
        minimum = 1 if n == 1 else 2
        rows = [(g, s) for g, s in scores[n].most_common() if frequency[n][g] >= minimum or s >= 4]
        return [{'keyword': g, 'score': round(s, 2), 'frequency': frequency[n][g], 'fields': dict(fields[n][g]),
                 'examples': occurrences[n][g]} for g, s in rows[:limit]]

    unigrams = ranked(1, limits['unigrams'])
    bigrams = ranked(2, limits['bigrams'])
    trigrams = ranked(3, limits['trigrams'])
    phrases = sorted(bigrams + trigrams, key=lambda r: -r['score'])
    return {
        'method': 'Weighted lexical n-grams from titles, descriptions, headings, URLs, front matter, alt text and body copy; '
                  'no search-volume, difficulty or ranking data.',
        'weights': weights,
        'primary_candidates': [r['keyword'] for r in phrases[:15]] or [r['keyword'] for r in unigrams[:15]],
        'unigrams': unigrams, 'bigrams': bigrams, 'trigrams': trigrams,
        'declared_keywords': declared,
        'by_file': {f: [{'keyword': g, 'score': round(s, 2)} for g, s in c.most_common(limits['per_file'])]
                    for f, c in sorted(per_file.items())},
    }
