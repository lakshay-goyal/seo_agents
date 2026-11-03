"""Write one new blog article from a brief + competitor research (model, 'article-writer'), then validate it.

The validator enforces the content rules (article_rules.py) on every text unit: SEO title with a click-through hook,
H1, meta description, evergreen slug, answer-first intro, key takeaways, 4-12 H2 sections, every brief question
answered under its own heading (or FAQ), at least one table, one list, one diagram and one image (with alt text),
3+ internal links to real site URLs (always the pillar), tags, cited + fresh numbers, no copied competitor phrases,
no keyword stuffing, no "this page" self references.
"""

from __future__ import annotations

import re

from seo_agents.onpage.content.article_rules import (ARTICLE_LIMITS, IMAGE_FILE, check_alt, check_ctr_title, check_facts,
                                                     check_list, check_mermaid, check_overlap, check_table, check_title,
                                                     shingles)
from seo_agents.onpage.validation import content_validators as v
from seo_agents.onpage.validation.content_policy import LIMITS

_STR = {'type': 'string'}
_STRS = {'type': 'array', 'items': _STR}
_BLOCK = {'type': 'object', 'required': ['tag'], 'properties': {
    'tag': {'type': 'string', 'enum': ['p', 'ul', 'ol', 'table', 'figure', 'image_brief', 'diagram', 'h3', 'blockquote']},
    'text': _STR, 'items': _STRS, 'caption': _STR, 'header': _STRS, 'rows': {'type': 'array', 'items': _STRS},
    'src': _STR, 'alt': _STR, 'filename': _STR, 'prompt': _STR, 'code': _STR}}
ARTICLE_SCHEMA = {'type': 'object', 'required': ['title', 'h1', 'meta_description', 'intro', 'key_takeaways', 'sections', 'faq',
                                                 'tags', 'category', 'link_from'], 'properties': {
    'title': _STR, 'h1': _STR, 'meta_description': _STR, 'intro': _STR, 'key_takeaways': _STRS,
    'sections': {'type': 'array', 'items': {'type': 'object', 'required': ['heading', 'blocks'],
                                            'properties': {'heading': _STR, 'blocks': {'type': 'array', 'items': _BLOCK}}}},
    'faq': {'type': 'array', 'items': {'type': 'object', 'required': ['question', 'answer'],
                                       'properties': {'question': _STR, 'answer': _STR}}},
    'tags': _STRS, 'category': _STR,
    'link_from': {'type': 'array', 'items': {'type': 'object', 'required': ['url', 'anchor'],
                                             'properties': {'url': _STR, 'anchor': _STR}}}}}
BLOCKS = {'p', 'ul', 'ol', 'table', 'figure', 'image_brief', 'diagram', 'h3', 'blockquote'}


def word_target(patterns):
    """Minimum body words: 80% of the competitor median, clamped to 900-1800 (1200 without data). Live runs showed one
    model call reliably writes ~1800 validated words; longer targets were missed on every attempt."""
    median = ((patterns or {}).get('word_count') or {}).get('median')
    low, high = ARTICLE_LIMITS['WORDS']
    if not median:
        return 1200
    return max(low, min(round(median * 0.8), 1800, high))


class ArticleValidator:
    """Callable validator for one article answer (raises ValueError with repair feedback; returns the cleaned answer)."""

    def __init__(self, brief, *, research, links, assets, corpus, run_date, other_titles, brand):
        self.brief = brief
        self.sources = research.get('sources') or []
        self.competitor_titles = research.get('titles') or []
        self.copy_guard = set().union(*[shingles(t, ARTICLE_LIMITS['COPY_SHINGLE_WORDS'][0]) for t in research.get('texts') or []]) \
            if research.get('texts') else set()
        self.links = links                  # {path: title}
        self.assets = assets                # {src: {...}}
        self.corpus = corpus
        self.run_date = run_date
        self.run_year = int(run_date[:4])
        self.other_titles = other_titles
        self.brand = brand
        self.min_words = word_target(research.get('patterns'))

    # ---------------------------------------------------------------- per text unit
    def unit(self, text, name, units):
        if not isinstance(text, str) or not text.strip():
            raise ValueError(name + ' must be non-empty text')
        if v.SELF_REFERENCE.search(text):
            raise ValueError(f'{name} uses a self reference ("this page", "click here", ...); write self-contained text')
        v.check_links(text, set(self.links), name)
        for href, _ in v.inline_links(text):
            if not href.startswith('/'):
                raise ValueError(f'{name} links to {href}: inline links must be internal site paths; cite outside facts with [S#]')
            if href.split('#')[0] == self.brief['url']:
                raise ValueError(f'{name} links to the article itself')
        try:
            units['claims'] += check_facts(text, name, corpus=self.corpus, sources=self.sources, run_date=self.run_date)
        except ValueError as error:  # collected: one repair turn must fix every uncited number, not one at a time
            units['fact_errors'].append(str(error))
        check_overlap(text, self.copy_guard, name)
        units['texts'].append((name, text))
        return text.strip()

    def block(self, b, name, units, counts, alts):
        if not isinstance(b, dict) or b.get('tag') not in BLOCKS:
            raise ValueError(f'{name}.tag must be one of {sorted(BLOCKS)}')
        tag = b['tag']
        counts[tag] = counts.get(tag, 0) + 1
        primary = self.brief['primary_keyword']
        if tag in ('p', 'blockquote'):
            self.unit(b.get('text'), name, units)
            if len(v.words(b['text'])) > LIMITS['PARAGRAPH_MAX_WORDS'][1]:
                raise ValueError(f'{name}: paragraph is {len(v.words(b["text"]))} words; split it '
                                 f'(max {LIMITS["PARAGRAPH_MAX_WORDS"][1]})')
        elif tag == 'h3':
            b['text'] = v.text_length(b.get('text'), *LIMITS['HEADING_CHARS'], name)
            units['headings'].append(b['text'])
        elif tag in ('ul', 'ol'):
            b['items'] = check_list(b.get('items'), name)
            for j, item in enumerate(b['items']):
                self.unit(item, f'{name}.items[{j}]', units)
        elif tag == 'table':
            check_table(b, name)
            b['caption'] = v.text_length(b.get('caption'), 5, 160, name + '.caption')
            cited_caption = b['caption']
            for j, cell in enumerate(b['header']):
                self.unit(cell, f'{name}.header[{j}]', units)
            for r, row in enumerate(b['rows']):
                for c, cell in enumerate(row):
                    # a cell's number may be backed by a citation in the same row or in the caption
                    context = cell + ' ' + ' '.join(x for x in row if '[S' in x) + ' ' + cited_caption
                    check_facts(context, f'{name}.rows[{r}][{c}]', corpus=self.corpus, sources=self.sources, run_date=self.run_date)
                    if cell.strip():
                        v.check_links(cell, set(self.links), f'{name}.rows[{r}][{c}]')
                        check_overlap(cell, self.copy_guard, f'{name}.rows[{r}][{c}]')
                        units['texts'].append((f'{name}.rows[{r}][{c}]', cell))
        elif tag == 'figure':
            if b.get('src') not in self.assets:
                raise ValueError(f'{name}: figure src "{b.get("src")}" is not one of assets[].src; use image_brief for a new image')
            b['alt'] = check_alt(b.get('alt'), name + '.alt', primary=primary, seen=alts)
            b['caption'] = v.text_length(b.get('caption'), 5, 160, name + '.caption')
            units['images'].append({'src': b['src'], 'alt': b['alt'], 'caption': b['caption'], 'new': False})
        elif tag == 'image_brief':
            filename = str(b.get('filename') or '')
            if not IMAGE_FILE.match(filename) or len(filename) > 70:
                raise ValueError(f'{name}.filename "{filename}" must be a descriptive hyphenated name like '
                                 '"test-automation-workflow-diagram.webp" (lowercase, <= 70 chars)')
            b['alt'] = check_alt(b.get('alt'), name + '.alt', primary=primary, seen=alts)
            b['caption'] = v.text_length(b.get('caption'), 5, 160, name + '.caption')
            b['prompt'] = v.text_length(b.get('prompt'), 20, 400, name + '.prompt')
            units['images'].append({'src': '/images/' + filename, 'alt': b['alt'], 'caption': b['caption'], 'new': True,
                                    'prompt': b['prompt']})
        elif tag == 'diagram':
            b['code'] = check_mermaid(b.get('code'), name)
            b['alt'] = check_alt(b.get('alt'), name + '.alt', primary=primary, seen=alts)
            b['caption'] = v.text_length(b.get('caption'), 5, 160, name + '.caption')
            check_facts(b['code'] + ' ' + b['caption'], name, corpus=self.corpus, sources=self.sources, run_date=self.run_date)
        return b

    # ---------------------------------------------------------------- whole article
    def __call__(self, a):
        v.require(a, *ARTICLE_SCHEMA['required'])
        brief, primary = self.brief, self.brief['primary_keyword']
        units = {'texts': [], 'claims': [], 'headings': [], 'images': [], 'fact_errors': []}
        others = self.other_titles
        a['title'] = check_title(a['title'], primary=primary, run_year=self.run_year, others=others,
                                 competitor_titles=self.competitor_titles)
        a['title_hooks'] = check_ctr_title(a['title'], self.run_year)
        a['h1'] = check_title(a['h1'], primary=primary, run_year=self.run_year, others=others,
                              competitor_titles=self.competitor_titles, name='h1', limits='H1_CHARS')
        a['meta_description'] = v.text_length(a['meta_description'], *ARTICLE_LIMITS['DESCRIPTION_CHARS'], 'meta_description')
        if '"' in a['meta_description']:
            raise ValueError('meta_description: remove double quotes')
        if not v.contains_query(a['meta_description'], primary):
            raise ValueError(f'meta_description must contain the primary keyword "{primary}"')
        self.unit(a['meta_description'], 'meta_description', units)
        v.word_count(a['intro'], *ARTICLE_LIMITS['INTRO_WORDS'], 'intro')
        if not v.contains_query(a['intro'], primary):
            raise ValueError(f'intro must answer "{primary}" directly and contain it (answer-first opening)')
        self.unit(a['intro'], 'intro', units)
        low, high = ARTICLE_LIMITS['TAKEAWAYS']
        if not isinstance(a['key_takeaways'], list) or not low <= len(a['key_takeaways']) <= high:
            raise ValueError(f'key_takeaways needs {low}-{high} items')
        a['key_takeaways'] = check_list(a['key_takeaways'], 'key_takeaways')
        for j, item in enumerate(a['key_takeaways']):
            self.unit(item, f'key_takeaways[{j}]', units)
        sections = a['sections']
        low, high = ARTICLE_LIMITS['H2_COUNT']
        if not isinstance(sections, list) or not low <= len(sections) <= high:
            raise ValueError(f'sections needs {low}-{high} H2 sections')
        counts, alts, slots = {}, set(), []
        for i, s in enumerate(sections):
            name = f'sections[{i}]'
            if not isinstance(s, dict) or not isinstance(s.get('blocks'), list) or not s['blocks']:
                raise ValueError(f'{name} needs a heading and non-empty blocks')
            s['heading'] = v.text_length(s.get('heading'), *LIMITS['HEADING_CHARS'], name + '.heading')
            units['headings'].append(s['heading'])
            for j, b in enumerate(s['blocks']):
                self.block(b, f'{name}.blocks[{j}]', units, counts, alts)
            first = s['blocks'][0]
            slots.append((s['heading'], first.get('text') if first.get('tag') == 'p' else None, name))
            for j, b in enumerate(s['blocks'][:-1]):
                nxt = s['blocks'][j + 1]
                if b['tag'] == 'h3':
                    slots.append((b['text'], nxt.get('text') if nxt.get('tag') == 'p' else None, f'{name}.blocks[{j}]'))
        normalized = [v.normalize(h) for h in units['headings']]
        if len(set(normalized)) != len(normalized):
            raise ValueError('headings repeat; every H2/H3 must be distinct')
        if sum(1 for s in sections if v.phrase_count(s['heading'], primary)) > 2:
            raise ValueError('the primary keyword is in more than 2 H2s; use variants and sub-topics')
        missing = [k for k, label in (('table', 'a comparison/summary table'), ('diagram', 'a Mermaid diagram'))
                   if not counts.get(k)]
        if not (counts.get('ul') or counts.get('ol')):
            missing.append('a bullet or numbered list inside a section')
        if not (counts.get('figure') or counts.get('image_brief')):
            missing.append('an image (figure from assets or image_brief for a new one)')
        if missing:
            raise ValueError('add ' + ', '.join(missing) + ' to make the article visual and scannable')
        faq = a['faq']
        low, high = ARTICLE_LIMITS['FAQ_ITEMS']
        if not isinstance(faq, list) or len(faq) > high:
            raise ValueError(f'faq must be a list of at most {high} items')
        for i, q in enumerate(faq):
            if not isinstance(q, dict):
                raise ValueError(f'faq[{i}] must be an object')
            q['question'] = v.text_length(q.get('question'), 10, 120, f'faq[{i}].question')
            if not q['question'].endswith('?'):
                raise ValueError(f'faq[{i}].question must end with "?"')
            if any(v.similar(q['question'], h) for h in units['headings']):
                raise ValueError(f'faq[{i}] repeats a heading; FAQ is for follow-up questions the sections do not cover')
            v.word_count(q.get('answer'), *ARTICLE_LIMITS['FAQ_ANSWER_WORDS'], f'faq[{i}].answer')
            self.unit(q['answer'], f'faq[{i}].answer', units)
            slots.append((q['question'], q['answer'], f'faq[{i}]'))
        low_q, high_q = LIMITS['QUESTION_ANSWER_WORDS']
        covered = {}
        for question in brief.get('questions') or []:
            slot = next((s for s in slots if v.covers_question(question, s[0], ignore=[self.brand or ''])), None)
            if slot is None:
                raise ValueError(f'question "{question}" is not answered: add an H2/H3 that asks it (or a FAQ item) and open with a '
                                 f'direct {low_q}-{high_q + 10} word answer')
            if not slot[1]:
                raise ValueError(f'{slot[2]} covers "{question}" but its first block is not a paragraph answering it')
            if not slot[2].startswith('faq'):
                v.word_count(slot[1], low_q, high_q + 10, f'{slot[2]} answer to "{question}"')
            covered[question] = slot[2]
        a['questions_covered'] = covered
        body = ' '.join(t for _, t in units['texts'])
        total = len(v.words(body)) + sum(len(v.words(h)) for h in units['headings'])
        high_words = ARTICLE_LIMITS['WORDS'][1]
        if not self.min_words <= total <= high_words:
            raise ValueError(f'article body is {total} words; competitors that rank average more, so write '
                             f'{self.min_words}-{high_words} words of real substance (examples, steps, specifics), not filler')
        v.no_stuffing(body, primary, name='article body', protected=[self.brand or ''])
        v.keyword_caps(body, brief.get('secondary_keywords') or [], name='article body', protected=[self.brand or ''])
        hrefs = {href.split('#')[0] for _, text in units['texts'] for href, _ in v.inline_links(text)}
        need = min(ARTICLE_LIMITS['INTERNAL_LINKS'][0], len([u for u in self.links if u != brief['url']]))
        if len(hrefs) < need:
            raise ValueError(f'article links {len(hrefs)} site pages; add descriptive inline links to at least {need} of links[].url')
        if brief.get('pillar_url') and brief['pillar_url'] not in hrefs:
            raise ValueError(f'link the pillar page {brief["pillar_url"]} from the article (topic cluster)')
        tags = a['tags']
        low, high = ARTICLE_LIMITS['TAGS']
        if not isinstance(tags, list) or not low <= len(tags) <= high:
            raise ValueError(f'tags needs {low}-{high} items')
        a['tags'] = []
        for i, tag in enumerate(tags):
            tag = ' '.join(str(tag or '').lower().split())
            if not 1 <= len(tag.split()) <= 3 or not tag.replace(' ', '').replace('-', '').isalnum():
                raise ValueError(f'tags[{i}] "{tag}" must be 1-3 plain lowercase words')
            if tag in a['tags']:
                raise ValueError(f'tags[{i}] "{tag}" is duplicated')
            a['tags'].append(tag)
        a['category'] = v.text_length(a['category'], 2, 40, 'category')
        link_from = a['link_from']
        low, high = ARTICLE_LIMITS['LINK_FROM']
        if not isinstance(link_from, list) or not low <= len(link_from) <= high:
            raise ValueError(f'link_from needs {low}-{high} existing pages that should link to the new article')
        for i, item in enumerate(link_from):
            if not isinstance(item, dict) or item.get('url') not in self.links or item.get('url') == brief['url']:
                raise ValueError(f'link_from[{i}].url must be one of links[].url')
            v.check_anchor(item.get('anchor') or '', f'link_from[{i}].anchor')
            if not v.contains_query(item['anchor'], primary) and not (v.content_tokens(item['anchor']) & v.content_tokens(primary)):
                raise ValueError(f'link_from[{i}].anchor must describe the new article (use words of "{primary}")')
        if units['fact_errors']:
            raise ValueError(f"{len(units['fact_errors'])} unsupported numbers; fix ALL of them in this answer (cite the [S#] "
                             'that states each number or remove it): ' + ' | '.join(units['fact_errors'][:10]))
        a['slug'], a['url'] = brief['slug'], brief['url']
        a['claims'] = units['claims']
        a['images'] = units['images']
        a['word_count'] = total
        a['internal_links'] = sorted(hrefs)
        a['sources_cited'] = sorted({c['source'] for c in units['claims']} |
                                    {sid for _, t in units['texts'] for sid in re.findall(r'\[(S\d+)\]', t)},
                                    key=lambda s: int(s[1:]))
        return a


class ArticleWriter:
    """Tool (model): brief + research -> validated article (skill lib/prompts/onpage/article-writer)."""

    skill = 'article-writer'

    def write(self, writer, brief, *, research, links, assets, corpus, client_facts, business, run_date, other_titles, brand,
              lang='en'):
        validate = ArticleValidator(brief, research=research, links=links, assets=assets, corpus=corpus, run_date=run_date,
                                    other_titles=other_titles, brand=brand)
        patterns = research.get('patterns') or {}
        packet = {
            'brief': {k: brief.get(k) for k in ('primary_keyword', 'secondary_keywords', 'questions', 'search_intent', 'format',
                                                'angle', 'audience', 'working_title', 'slug', 'url', 'pillar_url')},
            'topics': [t['text'] for t in brief.get('topics') or []],
            'run_date': run_date, 'lang': lang, 'brand': brand, 'business': business,
            'competitors': research.get('pages') or [],
            'patterns': {k: patterns.get(k) for k in ('pages', 'word_count', 'h2', 'formats', 'images_median', 'common_subtopics',
                                                      'question_headings', 'freshness', 'schema_types', 'benchmarks')},
            'title_inspiration': {'titles': research.get('titles') or [], 'patterns': patterns.get('title_patterns'),
                                  'people_also_ask': ((research.get('serp') or {}).get('people_also_ask') or [])[:8]},
            'sources': [{k: s.get(k) for k in ('id', 'url', 'title', 'date', 'passage')} for s in research.get('sources') or []],
            'client_facts': client_facts,
            'assets': [{'src': src, 'alt': meta.get('alt'), 'type': meta.get('type', 'image')} for src, meta in assets.items()][:30],
            'links': [{'url': u, 'title': t} for u, t in links.items() if u != brief['url']],
            'min_words': validate.min_words,
            'words_per_section': round(validate.min_words / 7) + 40,
        }
        if getattr(writer, 'attempts', 4) < 4:  # a full article needs one more repair turn than a page plan
            from seo_agents.onpage.writer.content_writer import ContentWriter
            writer = ContentWriter(writer.factory, writer.tier, 4)
        # Prompt-only JSON: a strict response schema this large made the mid model loop until the token cap (live run,
        # 2026-11-03). ARTICLE_SCHEMA stays as the documented shape; the validator enforces it.
        return writer.write(self.skill, packet, validate, None)
