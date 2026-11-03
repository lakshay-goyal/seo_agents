"""Gap-worker answers: JSON response schemas (structured outputs) and the validators that accept or reject them.\n\nThe model answers with candidate IDs and enums only; code validates the answer against the task packet."""

from __future__ import annotations

import re

from seo_agents.repo_analysis.agents.gap_workers.gap_task_prompts import LIMITS

EDIT_KINDS = ['literal', 'prop', 'json', 'yaml', 'toml', 'markdown', 'i18n', 'computed', 'cms']
INTENTS = ['informational', 'commercial', 'transactional', 'navigational', 'local', 'unknown']
ORIGINS = ['local', 'runtime', 'unknown']
TRI = ['present', 'absent', 'unclear']
EEAT_KEYS = ('authorship', 'dates', 'trust_pages', 'citations')
ENUM_KEYS = {'intent', 'origin', *EEAT_KEYS}


STOP = set('a an the and or of for to in on at by with from your you our my is are be how what why best vs near'.split())


# ---------------------------------------------------------------- response schemas (structured outputs)
def _obj(properties, required):
    return {'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False}


REASON = {'type': 'string', 'maxLength': LIMITS['REASON_MAX']}
SCHEMAS = {
    'attribute_value': _obj({'candidate': {'type': 'string'}, 'reason': REASON}, ['candidate', 'reason']),
    'explain_computed': _obj({'line_id': {'type': 'string'}, 'origin': {'type': 'string', 'enum': ORIGINS},
                              'value_source': {'type': 'string', 'maxLength': LIMITS['VALUE_SOURCE_MAX']}, 'reason': REASON},
                             ['line_id', 'origin', 'value_source', 'reason']),
    'topic_intent': _obj({'primary_topic': {'type': 'string'}, 'intent': {'type': 'string', 'enum': INTENTS},
                          'candidate_queries': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': LIMITS['MAX_QUERIES']},
                          'reason': REASON}, ['primary_topic', 'intent', 'candidate_queries', 'reason']),
    'eeat_classify': _obj({**{k: {'type': 'string', 'enum': TRI} for k in EEAT_KEYS},
                           'evidence_ids': {'type': 'array', 'items': {'type': 'string'}}, 'reason': REASON},
                          [*EEAT_KEYS, 'evidence_ids', 'reason']),
    'enumerate_instances': _obj({'group': {'type': 'string'}, 'reason': REASON}, ['group', 'reason']),
    'route_adapter': _obj({'framework': {'type': 'string'}, 'rules': {'type': 'array', 'maxItems': LIMITS['MAX_RULES'], 'items': _obj({
        'path_regex': {'type': 'string'}, 'url_template': {'type': 'string'}, 'index_names': {'type': 'array', 'items': {'type': 'string'}},
        'dynamic_syntax': {'type': 'string'}}, ['path_regex', 'url_template', 'index_names', 'dynamic_syntax'])},
        'reason': {'type': 'string'}}, ['framework', 'rules', 'reason']),
}


# ---------------------------------------------------------------- validators
def words(text):
    return re.findall(r"[a-z0-9][a-z0-9'+.-]*", str(text or '').casefold())


def grounding_vocabulary(packet):
    parts = [packet.get('url'), packet.get('title'), packet.get('description'), packet.get('copy')] + list(packet.get('headings') or [])
    vocab = set()
    for part in parts:
        for word in words(re.sub(r'[/_\-]+', ' ', str(part or ''))):
            vocab.add(word.strip(".'"))
            vocab.add(word.strip(".'").rstrip('s'))
    return vocab


def validate(task, answer):
    kind = task['task']
    if not isinstance(answer, dict):
        raise ValueError('expected a JSON object')
    answer.pop('edit_kind', None)  # code derives the edit kind from the chosen candidate; never the model
    if kind == 'attribute_value':
        ids = {c['id'] for c in task['packet']['candidates']} | {'none'}
        if answer.get('candidate') not in ids:
            raise ValueError('candidate must be one of ' + ', '.join(sorted(ids)))
    elif kind == 'explain_computed':
        ids = {l['id'] for l in task['packet']['lines']} | {'none'}
        if answer.get('line_id') not in ids:
            raise ValueError('line_id must be one of the given line ids or none')
        answer['origin'] = answer.get('origin') or 'unknown'
        if answer['origin'] not in ORIGINS:
            raise ValueError('origin must be one of ' + ', '.join(ORIGINS))
    elif kind == 'topic_intent':
        if answer.get('intent') not in INTENTS:
            raise ValueError('intent must be one of ' + ', '.join(INTENTS))
        queries = answer.get('candidate_queries', answer.pop('target_queries', None))
        if queries is None:
            queries = []
        if not isinstance(queries, list) or len(queries) > LIMITS['MAX_QUERIES'] or not all(isinstance(q, str) and q.strip() for q in queries):
            raise ValueError(f"candidate_queries must be a list of at most {LIMITS['MAX_QUERIES']} non-empty strings")
        vocab = grounding_vocabulary(task.get('packet') or {})
        cleaned = []
        for query in queries:
            tokens = words(query)
            if not LIMITS['QUERY_MIN_WORDS'] <= len(tokens) <= LIMITS['QUERY_MAX_WORDS']:
                raise ValueError(f"each query must be {LIMITS['QUERY_MIN_WORDS']}-{LIMITS['QUERY_MAX_WORDS']} words: {query[:60]!r}")
            missing = [t for t in tokens if t not in STOP and t.strip(".'") not in vocab and t.strip(".'").rstrip('s') not in vocab]
            if vocab and missing:
                raise ValueError('query words not found in the page text (do not invent): ' + ', '.join(missing[:5]))
            cleaned.append(' '.join(tokens))
        answer['candidate_queries'] = cleaned
        topic = answer.get('primary_topic')
        if answer['intent'] == 'unknown' and not topic:
            answer['primary_topic'] = ''
        elif not isinstance(topic, str) or not LIMITS['TOPIC_MIN_WORDS'] <= len(topic.split()) <= LIMITS['TOPIC_MAX_WORDS']:
            raise ValueError(f"primary_topic must be {LIMITS['TOPIC_MIN_WORDS']}-{LIMITS['TOPIC_MAX_WORDS']} words")
    elif kind == 'eeat_classify':
        answer.pop('expertise_evidence', None)
        for key in EEAT_KEYS:
            if answer.get(key) not in TRI:
                raise ValueError(key + ' must be one of present|absent|unclear')
        ids = {c['id'] for c in task['packet']['candidates']}
        if not set(answer.get('evidence_ids') or []) <= ids:
            raise ValueError('evidence_ids must be candidate ids')
    elif kind == 'enumerate_instances':
        ids = {g['id'] for g in task['packet']['groups']} | {'none'}
        if answer.get('group') not in ids:
            raise ValueError('group must be one of the given ids or none')
    elif kind == 'route_adapter':
        rules = answer.get('rules')
        if (answer.get('framework') or '').casefold() == 'unknown' and rules in (None, []):
            answer['rules'] = []
        else:
            if not isinstance(rules, list) or not LIMITS['MIN_RULES'] <= len(rules) <= LIMITS['MAX_RULES']:
                raise ValueError(f"rules must be a list of {LIMITS['MIN_RULES']}-{LIMITS['MAX_RULES']} rules "
                                 "(or framework 'unknown' with no rules)")
            files = task['packet']['files']
            for rule in rules:
                try:
                    regex = re.compile(rule['path_regex'])
                except (re.error, KeyError, TypeError) as error:
                    raise ValueError('invalid path_regex: ' + str(error)[:80]) from error
                if 'route' not in regex.groupindex:
                    raise ValueError("path_regex needs a named group 'route'")
                matched = [f for f in files if regex.match(f)]
                if not matched:
                    raise ValueError('rule matches no listed file: ' + rule['path_regex'][:80])
                if any(re.search(r'(?:^|/)(?:tests?|node_modules|__tests__)/', f) for f in matched):
                    raise ValueError('rule matches test or dependency files')
    for key in ('reason', 'value_source'):
        if answer.get(key) is not None and not isinstance(answer[key], str):
            raise ValueError(key + ' must be a string')
        answer[key] = answer.get(key) or ''
    return answer
