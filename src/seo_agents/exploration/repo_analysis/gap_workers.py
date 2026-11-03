"""Stage F: cheap-model gap workers (audit §5, §8, §13-§14).

Each gap becomes a self-contained task packet (target <= ~6k tokens) that needs no tools. The
model answers with a constrained JSON object (candidate IDs and enums, never coordinates or
edit kinds); code validates it and derives kind/file/line/col from the chosen candidate.

Accuracy comes from the validator, not the model:
  ladder (default small -> small with the validator error -> mid) ; escalation only after two
  validation failures. Transport errors retry with backoff and never advance the ladder; auth
  or credit errors fail fast for the whole run.
Judgment tasks (topic/intent, E-E-A-T) use self-consistency votes on ENUM fields only; free
text is taken from a vote that agrees with the majority. Agreement is a fraction.

Task types: attribute_value, explain_computed, topic_intent, eeat_classify,
enumerate_instances, route_adapter (ladder small -> haiku -> mid; cached per repo snapshot).
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import threading

from seo_agents.log import log

GAP_CODE_VERSION = '2.0.0'  # bump when packets, validators or apply() change: invalidates both caches
SKILLS_DIR = Path(__file__).parent / 'agents' / 'gap' / 'skills'
TASK_SKILL = {'attribute_value': 'attribute-value', 'explain_computed': 'explain-computed', 'topic_intent': 'topic-intent',
              'eeat_classify': 'eeat-classify', 'enumerate_instances': 'enumerate-instances', 'route_adapter': 'route-adapter'}
EDIT_KINDS = ['literal', 'prop', 'json', 'yaml', 'toml', 'markdown', 'i18n', 'computed', 'cms']
INTENTS = ['informational', 'commercial', 'transactional', 'navigational', 'local', 'unknown']
ORIGINS = ['local', 'runtime', 'unknown']
TRI = ['present', 'absent', 'unclear']
EEAT_KEYS = ('authorship', 'dates', 'trust_pages', 'citations')
ENUM_KEYS = {'intent', 'origin', *EEAT_KEYS}
CACHE_DIR = Path(os.getenv('SEO_ADAPTER_CACHE') or Path.home() / '.cache' / 'seo_agents' / 'adapters')

# Numeric limits: the single source for validators AND the skill text ({{NAME}} placeholders).
LIMITS = {
    'MAX_CANDIDATES': 10, 'REASON_MAX': 200, 'VALUE_SOURCE_MAX': 160,
    'TOPIC_MIN_WORDS': 2, 'TOPIC_MAX_WORDS': 6, 'QUERY_MIN_WORDS': 2, 'QUERY_MAX_WORDS': 6, 'MAX_QUERIES': 3,
    'MIN_RULES': 1, 'MAX_RULES': 8, 'HELPER_LINES': 40, 'MAX_HELPERS': 3, 'HEAD_LINES': 20, 'MAX_HEADS': 30,
    'COPY_WORDS': 300, 'MAX_FILES': 400,
}
STOP = set('a an the and or of for to in on at by with from your you our my is are be how what why best vs near'.split())
_AUTH_FAILED = threading.Event()
_AUTH_REASON = {'error': None}


def reset_auth_failure():
    _AUTH_FAILED.clear()
    _AUTH_REASON['error'] = None


# ---------------------------------------------------------------- skills
def load_skill(name, family=None):
    """Skill text with numeric limits filled in and only this family's examples (progressive disclosure).

    Example sections are `### <family> ...`; `### any ...` sections are always kept. Each family
    should ship one positive and one negative example.
    """
    text = (SKILLS_DIR / name / 'SKILL.md').read_text()
    for key, value in LIMITS.items():
        text = text.replace('{{' + key + '}}', str(value))
    body = text.split('---', 2)[-1].strip() if text.startswith('---') else text
    meta = dict(re.findall(r'(?m)^(\w+):\s*(.+)$', text.split('---', 2)[1])) if text.startswith('---') else {}
    match = re.search(r'(## Examples\n)(.*?)(?=\n## |\Z)', body, re.S)
    if match:
        sections = re.split(r'(?m)^(?=### )', match.group(2))
        intro = [s for s in sections if not s.startswith('### ')]
        named = [s for s in sections if s.startswith('### ')]

        def label(section):
            return section[4:].split('\n', 1)[0].strip().split()[0].casefold() if section[4:].strip() else ''
        always = [s for s in named if label(s) == 'any']
        family_specific = [s for s in named if family and label(s) in family_aliases(family)]
        keep = always + family_specific if family_specific else always + [s for s in named if s not in always][:2]
        body = body[:match.start(2)] + ''.join(intro + keep).rstrip() + '\n' + body[match.end(2):]
    return body, meta


def family_aliases(family):
    return {'react': {'jsx', 'next'}, 'vue': {'vue', 'jsx'}, 'svelte': {'svelte', 'jsx'}, 'astro': {'astro', 'jsx'},
            'liquid': {'liquid'}, 'php': {'php'}, 'blade': {'php'}, 'twig': {'php', 'jinja'}, 'jinja': {'jinja', 'php'},
            'erb': {'php'}, 'handlebars': {'liquid'}, 'go-template': {'liquid'}, 'html': {'php', 'jsx'}}.get(family, {family})


def parse_json(text):
    if isinstance(text, dict):
        return text
    if isinstance(text, list):
        text = ''.join(part.get('text', '') if isinstance(part, dict) else str(part) for part in text)
    text = str(text)
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except ValueError:
        pass
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end < start:
        raise ValueError('answer is not a JSON object')
    return json.loads(text[start:end + 1])


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


# ---------------------------------------------------------------- model calls
def default_factory(tier, temperature=0, label=None, escalation=False):
    from seo_agents.exploration.repo_analysis.tools.llm import get_chat_model
    return get_chat_model(tier, max_tokens=1500 if tier == 'small' else 6000, label=label, temperature=temperature,
                          escalation=escalation)


def make_model(factory, tier, temperature, label, escalation):
    try:
        accepts = 'escalation' in inspect.signature(factory).parameters
    except (TypeError, ValueError):
        accepts = False
    return factory(tier, temperature, label, escalation=escalation) if accepts else factory(tier, temperature, label)


def ask(model, system, packet, feedback=None, schema=None, name='answer'):
    """One validated-shape call. Transport errors back off; a provider that rejects response_format
    falls back to prompt-only JSON (brace scan)."""
    from langchain_core.messages import HumanMessage
    from seo_agents.exploration.repo_analysis.tools.llm import call_with_backoff, cached_system_message, with_json_schema
    content = json.dumps(packet, ensure_ascii=False)
    if feedback:
        content += '\n\nYour previous answer was rejected by the validator: ' + feedback + '\nReturn a corrected JSON object only.'
    messages = [cached_system_message(system, model), HumanMessage(content=content)]
    bound = with_json_schema(model, schema, name) if schema else model
    try:
        response = call_with_backoff(lambda: bound.invoke(messages))
    except Exception as error:  # noqa: BLE001 - only a response_format rejection is retried here
        if bound is model or not re.search(r'response_format|json_schema|structured', str(error), re.I):
            raise
        response = call_with_backoff(lambda: model.invoke(messages))
    return parse_json(response.content)


GAP_CACHE = Path(os.getenv('SEO_GAP_CACHE') if os.getenv('SEO_GAP_CACHE') not in {None, '', '0'} else Path.home() / '.cache' / 'seo_agents' / 'gap')


def cache_key(task, skill):
    from seo_agents.exploration.repo_analysis.tools.llm import model_for_tier
    payload = json.dumps([GAP_CODE_VERSION, task['task'], task['packet'], task.get('scope'), skill,
                          model_for_tier('small'), model_for_tier('haiku'), model_for_tier('mid')],
                         sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def solve(task, family=None, *, model_factory=None):
    """Validated answer for one task; resolved answers are cached by packet + repo/snapshot + code version."""
    if _AUTH_FAILED.is_set():
        return {'status': 'failed', 'answer': None, 'tier': None, 'attempts': 0,
                'errors': ['auth: ' + str(_AUTH_REASON['error'] or 'provider rejected credentials')[:200]]}
    skill, _ = load_skill(TASK_SKILL[task['task']], family)
    use_cache = model_factory is None and os.getenv('SEO_GAP_CACHE', '') != '0'
    path = GAP_CACHE / (cache_key(task, skill) + '.json') if use_cache else None
    if path is not None and path.exists():
        try:
            cached = json.loads(path.read_text())
            return {**cached, 'cached': True}
        except ValueError:
            pass
    result = solve_uncached(task, family, model_factory=model_factory)
    if path is not None and result['status'] == 'resolved':
        try:
            GAP_CACHE.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result))
        except OSError:
            pass
    return result


def _provider_failure(error, errors, tier, attempt):
    from seo_agents.exploration.repo_analysis.tools.llm import AuthError, TransportError, classify_error
    kind = 'auth' if isinstance(error, AuthError) else 'transport' if isinstance(error, TransportError) else classify_error(error)
    if kind is None:
        return None
    message = kind + ': ' + type(error).__name__ + ': ' + str(error)[:200]
    errors.append(message)
    if kind == 'auth':
        _AUTH_FAILED.set()
        _AUTH_REASON['error'] = str(error)[:200]
        log('gap workers: provider auth/credit failure; skipping remaining model calls: ' + str(error)[:160])
    return {'status': 'failed', 'answer': None, 'tier': tier, 'attempts': attempt, 'errors': errors, 'failure': kind}


def solve_uncached(task, family=None, *, model_factory=None):
    """Validation ladder (default small -> small+feedback -> mid). Provider failures stop the task."""
    from seo_agents.exploration.repo_analysis.tools.llm import ladder_for_skill
    factory = model_factory or default_factory
    skill, meta = load_skill(TASK_SKILL[task['task']], family)
    tiers = ladder_for_skill(meta)
    votes = int(meta.get('votes', '1'))
    schema = SCHEMAS.get(task['task'])
    errors = []
    if votes > 1:
        answers = []
        for index in range(votes):
            if index == 2 and len(answers) == 2 and all(answers[0].get(k) == answers[1].get(k) for k in ENUM_KEYS if k in answers[0]):
                break  # two agreeing votes: a third cannot change the majority
            try:
                model = make_model(factory, tiers[0], 0.5 if index else 0, task['task'], False)
                answers.append(validate(task, ask(model, skill, task['packet'], schema=schema, name=task['task'])))
            except Exception as error:  # noqa: BLE001 - each vote is independent; failures are counted
                failure = _provider_failure(error, errors, tiers[0], index + 1)
                if failure is not None:
                    return failure
                errors.append(type(error).__name__ + ': ' + str(error)[:160])
        if answers:
            merged, agreement = majority(answers)
            return finish(task, {'status': 'resolved', 'answer': merged, 'tier': tiers[0], 'votes': len(answers),
                                 'agreement': agreement, 'errors': errors})
        tiers = tiers[-1:]
    feedback = None
    for attempt, tier in enumerate(tiers, 1):
        try:
            model = make_model(factory, tier, 0, task['task'], attempt > 1 and tier != tiers[0])
            answer = validate(task, ask(model, skill, task['packet'], feedback, schema=schema, name=task['task']))
            return finish(task, {'status': 'resolved', 'answer': answer, 'tier': tier, 'attempts': attempt, 'errors': errors})
        except Exception as error:  # noqa: BLE001 - invalid answers are retried with the validator error, never applied
            failure = _provider_failure(error, errors, tier, attempt)
            if failure is not None:
                return failure
            feedback = type(error).__name__ + ': ' + str(error)[:200]
            errors.append(feedback)
    return {'status': 'unresolved', 'answer': None, 'tier': tiers[-1], 'attempts': len(tiers), 'errors': errors}


def finish(task, result):
    """Attach derived outputs (instances_suggested) so the orchestrator can feed them back to the manifest."""
    if task['task'] == 'enumerate_instances' and result['answer'].get('group') not in {None, 'none'}:
        from seo_agents.exploration.repo_analysis.url_manifest import substitute
        group = next((g for g in task.get('_groups') or [] if g['id'] == result['answer']['group']), None)
        if group:
            route = task['packet']['route']
            result['instances_suggested'] = [substitute(route, str(v)) for v in group['values'][:500]]
    return result


def majority(answers, enum_keys=None):
    """Majority per ENUM field; agreement = mean fraction of votes agreeing on each enum field.
    Free-text fields come from the first vote that matches the majority enums."""
    keys = [k for k in answers[0] if k in (enum_keys or ENUM_KEYS)]
    merged, agreement = {}, []
    for key in keys:
        value, count = Counter(json.dumps(a.get(key), sort_keys=True) for a in answers).most_common(1)[0]
        merged[key] = json.loads(value)
        agreement.append(count / len(answers))
    representative = max(answers, key=lambda a: sum(a.get(k) == merged[k] for k in keys))
    for key, value in representative.items():
        merged.setdefault(key, value)
    return merged, round(sum(agreement) / len(agreement), 2) if agreement else 1.0


# ---------------------------------------------------------------- task construction
def code_lines(files, file, line, radius=6, prefix='L'):
    lines = files.get(file, '').splitlines()
    rows = []
    for number in range(max(1, line - radius), min(len(lines), line + radius) + 1):
        rows.append({'id': f'{prefix}{number}', 'file': file, 'line': number, 'code': lines[number - 1][:200]})
    return rows


DEFINITION = r'(?:(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*{name}\b|(?:export\s+)?(?:const|let|var)\s+{name}\b|def\s+{name}\b|\b{name}\s*[:=]\s*(?:\[|\{{|\(|async|function))'


def helper_lines(ctx, file, text, seen_ids):
    """Bodies (<= HELPER_LINES lines) of up to MAX_HELPERS identifiers used in `text` and defined in an
    imported module or elsewhere in the same file - the multi-hop reading explain_computed needs."""
    rows, used = [], 0
    imports = getattr(ctx, 'imports', {}).get(file, {}) if hasattr(ctx, 'imports') else {}
    names = [n for n in dict.fromkeys(re.findall(r'[A-Za-z_$][\w$]*', text)) if len(n) > 2 and n not in {'const', 'return', 'export', 'default', 'function', 'title', 'description'}]
    for name in names:
        if used >= LIMITS['MAX_HELPERS']:
            break
        target = imports.get(name)
        candidates = [target] if target in ctx.files else []
        candidates.append(file)
        for source in candidates:
            match = re.search(DEFINITION.format(name=re.escape(name)), ctx.files.get(source, ''))
            if not match:
                continue
            start = ctx.files[source].count('\n', 0, match.start()) + 1
            block = code_lines(ctx.files, source, start + LIMITS['HELPER_LINES'] // 2 - 1, radius=LIMITS['HELPER_LINES'] // 2 - 1,
                               prefix=f'H{used + 1}_')
            block = [r for r in block if r['line'] >= start and (r['file'], r['line']) not in seen_ids]
            if block:
                rows.extend(block)
                seen_ids.update((r['file'], r['line']) for r in block)
                used += 1
            break
    return rows


def snapshot_scope(ctx, fingerprint):
    """Repo id + snapshot hash for cache keys (no cross-repo reuse)."""
    files = getattr(ctx, 'files', {}) or {}
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode())
        digest.update(hashlib.sha1(str(files[path]).encode()).digest())
    return {'repo': getattr(ctx, 'repo_id', None) or (fingerprint or {}).get('repo') or os.getenv('SEO_REPO_ID') or '',
            'snapshot': digest.hexdigest()[:20]}


def build_tasks(gaps, pages, ctx, fingerprint):
    limit = int(os.getenv('SEO_GAP_MAX_TASKS', '200'))
    scope = snapshot_scope(ctx, fingerprint)
    tasks = []
    for gap in gaps:
        if gap['task'] == 'attribute_value':
            tasks.append({'task': 'attribute_value', 'page_id': gap['page_id'], 'field': gap['field'],
                          'packet': {'field': gap['field'], 'observed': gap['observed'],
                                     'page': {'url': gap.get('url'), 'template': gap.get('template')},
                                     **{k: gap[k] for k in ('dom_path', 'section', 'neighbours', 'href') if gap.get(k)},
                                     'candidates': [{k: c.get(k) for k in ('id', 'kind', 'file', 'line', 'snippet', 'pointer', 'i18n_key',
                                                                           'attr', 'call_sites', 'in_loop', 'scope')
                                                     if c.get(k) not in (None, [], '')} for c in gap['candidates'][:LIMITS['MAX_CANDIDATES']]]},
                          '_candidates': gap['candidates'][:LIMITS['MAX_CANDIDATES']]})
    for page in pages:
        for key in ('title', 'description'):
            item = page['head'][key]
            edit = item.get('edit') or {}
            if edit.get('kind') == 'computed' and edit.get('file') in ctx.files and isinstance(edit.get('line'), int) and edit['line'] > 0:
                lines = code_lines(ctx.files, edit['file'], edit['line'])
                seen = {(l['file'], l['line']) for l in lines}
                lines += helper_lines(ctx, edit['file'], ' '.join([edit.get('via') or ''] + [l['code'] for l in lines]), seen)
                tasks.append({'task': 'explain_computed', 'page_id': page['id'], 'field': '/head/' + key,
                              'packet': {'field': '/head/' + key, 'page': {'url': page['url'], 'template': page['template']},
                                         'observed': item.get('value'), 'expression': (edit.get('via') or '')[:200], 'lines': lines}})
    concrete = [p for p in pages if p['instance_status'] != 'template' and p['kind'] != 'not-found']
    for page in concrete[:int(os.getenv('SEO_TOPIC_MAX_PAGES', '40'))]:
        copy = ' '.join(b['text'] for b in page['content']['blocks'] if b.get('section') not in {'header', 'nav', 'footer', 'aside'})
        if not (page['head']['title'].get('value') or copy.strip() or any(h.get('text') for h in page['headings'])):
            continue  # nothing to classify; do not spend a call
        tasks.append({'task': 'topic_intent', 'page_id': page['id'],
                      'packet': {'url': page['url'], 'title': page['head']['title'].get('value'),
                                 'description': page['head']['description'].get('value'),
                                 'headings': [h['text'] for h in page['headings'] if h['level'] <= 2 and h.get('text')][:8],
                                 'copy': ' '.join(copy.split()[:LIMITS['COPY_WORDS']])}})
    for page in concrete[:int(os.getenv('SEO_EEAT_MAX_PAGES', '20'))]:
        candidates = []
        for signal in page['eeat']:
            for item in signal['evidence'][:5] if isinstance(signal['evidence'], list) else []:
                text = item.get('text') or item.get('anchor') or item.get('href') if isinstance(item, dict) else str(item)
                if text:
                    candidates.append({'id': 'k' + str(len(candidates) + 1), 'type': signal['signal'], 'text': str(text)[:200]})
        if candidates:
            tasks.append({'task': 'eeat_classify', 'page_id': page['id'],
                          'packet': {'url': page['url'], 'page_kind': page['kind'], 'candidates': candidates[:25]}})
    groups = data_groups(ctx)
    for page in pages:
        if page['instance_status'] == 'template' and groups:
            tasks.append({'task': 'enumerate_instances', 'page_id': page['id'],
                          'packet': {'route': page['route'], 'template': page['template'],
                                     'imports': [r['resolved'] for r in ctx.index['refs'] if r['file'] == page['template'] and r.get('resolved')][:20],
                                     'groups': [{k: g[k] for k in ('id', 'file', 'pointer_pattern', 'sample_values')} for g in groups[:12]]},
                          '_groups': groups})
    for task in tasks:
        task['scope'] = scope
    return tasks[:limit]


def data_groups(ctx):
    """Local collections whose leaf looks like a route parameter (slug/id/handle/path)."""
    groups = defaultdict(list)
    for record in ctx.index['data']:
        match = re.match(r'^(.*?)/(\d+)/(slug|id|handle|path|permalink|url)$', record['pointer'])
        if match:
            groups[(record['file'], match.group(1) + '/*/' + match.group(3))].append(record['value'])
    return [{'id': 'g' + str(index + 1), 'file': file, 'pointer_pattern': pattern, 'sample_values': values[:5], 'values': values}
            for index, ((file, pattern), values) in enumerate(sorted(groups.items())) if len(values) >= 1]


# ---------------------------------------------------------------- application
def resolve_pointer(page, pointer):
    parts = pointer.strip('/').split('/')
    target = page
    for part in parts:
        target = target[int(part)] if isinstance(target, list) else target.get(part) if isinstance(target, dict) else None
        if target is None:
            return None
    return target


DATA_SUFFIX = {'json': 'json', 'yml': 'yaml', 'yaml': 'yaml', 'toml': 'toml', 'md': 'markdown', 'mdx': 'markdown', 'po': 'i18n'}


def derive_kind(file, code):
    """Edit kind for a chosen source line, from the file and the line itself (never from the model)."""
    suffix = (file or '').rsplit('.', 1)[-1].lower()
    if re.search(r'(?:^|/)(?:locales?|i18n|lang|messages|translations)/', file or '') and suffix in {'json', 'yml', 'yaml', 'po'}:
        return 'i18n'
    if suffix in DATA_SUFFIX:
        return DATA_SUFFIX[suffix]
    return 'literal' if re.search(r'''(['"`])[^'"`{}$]{2,}\1''', code or '') else 'computed'


def apply(task, result, page, ctx):
    answer = result['answer']
    method = 'llm:' + str(result['tier'])
    if task['task'] == 'attribute_value':
        item = resolve_pointer(page, task['field'])
        if item is None or answer['candidate'] == 'none':
            return False
        chosen = next(c for c in task['packet']['candidates'] if c['id'] == answer['candidate'])
        full = next((c for c in task.get('_candidates') or [] if c.get('id') == chosen['id']), chosen)
        kind = full.get('kind') if full.get('kind') in EDIT_KINDS else 'literal'
        edit = {k: full[k] for k in ('file', 'line', 'col', 'end_line', 'end_col', 'pointer', 'i18n_key', 'attr', 'in_loop',
                                     'template_n', 'data_source', 'scope', 'shared', 'shared_by') if full.get(k) is not None}
        item['edit'] = {'kind': kind, **edit, 'resolved_by': method, 'reason': (answer.get('reason') or '')[:LIMITS['REASON_MAX']],
                        'verify': 'heuristic' if kind == 'computed' else 'grounded'}
        item['confidence'] = 0.8 if kind != 'computed' else 0.6
        item.pop('candidates', None)
        item.pop('gap', None)
        return True
    if task['task'] == 'explain_computed':
        item = page['head'][task['field'].rsplit('/', 1)[-1]]
        line = next((l for l in task['packet']['lines'] if l['id'] == answer['line_id']), None)
        if line is None:
            item['gap'] = 'computed-unexplained'  # keep the existing (real) expression location; do not relabel it
            item['gap_reason'] = (answer.get('reason') or '')[:LIMITS['REASON_MAX']]
            return False
        kind = 'cms' if answer.get('origin') == 'runtime' else derive_kind(line['file'], line['code'])
        item['edit'] = {'kind': kind, 'file': line['file'], 'line': line['line'], 'resolved_by': method,
                        'via': (answer.get('value_source') or '')[:LIMITS['VALUE_SOURCE_MAX']],
                        'verify': 'grounded' if kind != 'computed' and item.get('value') and str(item['value']).casefold() in line['code'].casefold() else 'heuristic'}
        item['confidence'] = max(item.get('confidence') or 0, 0.7)
        item.pop('gap', None)
        return True
    if task['task'] == 'topic_intent':
        page['keywords'].update(topic=answer['primary_topic'] or None, intent=answer['intent'],
                                candidate_queries={'kind': 'candidate_queries', 'values': answer['candidate_queries'],
                                                   'grounded_in': 'page title/description/headings/copy',
                                                   'note': 'model-suggested from page text; not search-volume data'},
                                topic_method=method, topic_agreement=result.get('agreement'))
        return True
    if task['task'] == 'eeat_classify':
        page['eeat_assessment'] = {**{k: answer[k] for k in EEAT_KEYS},
                                   'evidence_ids': answer.get('evidence_ids', []), 'method': method, 'agreement': result.get('agreement'),
                                   'reason': (answer.get('reason') or '')[:LIMITS['REASON_MAX']],
                                   'limitations': ['candidate harvest patterns are English-only']}
        return True
    if task['task'] == 'enumerate_instances':
        if answer['group'] == 'none':
            return False
        group = next(g for g in task['_groups'] if g['id'] == answer['group'])
        urls = result.get('instances_suggested')
        if urls is None:
            from seo_agents.exploration.repo_analysis.url_manifest import substitute
            urls = [substitute(page['route'], str(v)) for v in group['values'][:500]]
        page['instances_suggested'] = {'urls': urls, 'source': {'file': group['file'], 'pointer_pattern': group['pointer_pattern']},
                                       'method': method}
        return True
    return False


# ---------------------------------------------------------------- learned adapters (unknown frameworks)
TEMPLATE_HINT = re.compile(r'\.(?:html?|php|tpl|twig|njk|liquid|hbs|handlebars|mustache|ejs|pug|erb|haml|slim|cshtml|razor|'
                           r'jsx?|tsx?|vue|svelte|astro|mdx?|py|rb|go|templ|jinja2?|j2|cfm|jsp|aspx?|gohtml)$', re.I)


def file_heads(files, paths):
    """First HEAD_LINES lines of up to MAX_HEADS template-like files, spread across directories."""
    chosen, per_dir = [], Counter()
    for path in sorted((p for p in paths if TEMPLATE_HINT.search(p)), key=lambda p: (p.count('/'), p)):
        directory = path.rsplit('/', 1)[0] if '/' in path else ''
        if per_dir[directory] >= 3:
            continue
        per_dir[directory] += 1
        chosen.append(path)
        if len(chosen) >= LIMITS['MAX_HEADS']:
            break
    return {path: '\n'.join(line[:160] for line in str(files.get(path, '')).splitlines()[:LIMITS['HEAD_LINES']]) for path in chosen}


def learn_adapter(snapshot, fingerprint, *, model_factory=None):
    """Declarative URL rules for an unrecognised stack (ladder small -> haiku -> mid), cached per repo snapshot."""
    files = sorted(p for p in snapshot['files'] if not re.search(r'(?:^|/)(?:node_modules|tests?|__tests__|\.git)/', p))[:LIMITS['MAX_FILES']]
    names = sorted(c['name'] for c in fingerprint.get('candidates', []))
    repo = snapshot.get('repo') or snapshot.get('root') or os.getenv('SEO_REPO_ID') or ''
    key = hashlib.sha256(json.dumps([GAP_CODE_VERSION, str(repo), snapshot.get('sha256'), names,
                                     fingerprint.get('version', '') and str(fingerprint['version']).split('.')[0],
                                     sorted({p.rsplit('.', 1)[-1] for p in files if '.' in p})]).encode()).hexdigest()[:16]
    cache = CACHE_DIR / (key + '.json')
    if cache.exists():
        try:
            rules = json.loads(cache.read_text())
            log('route adapter: reusing cached rules ' + cache.name)
            return rules, 'cache'
        except ValueError:
            pass
    task = {'task': 'route_adapter', 'page_id': None,
            'packet': {'files': files, 'heads': file_heads(snapshot['files'], files), 'candidates': names,
                       'manifests': sorted({c['name'] for c in fingerprint.get('candidates', [])})}}
    result = solve(task, fingerprint.get('family'), model_factory=model_factory)
    if result['status'] != 'resolved':
        return None, result['status']
    rules = result['answer']
    if not rules.get('rules'):
        return None, 'unknown'
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(rules, indent=2))
    except OSError:
        pass
    return rules, result['tier']


def apply_learned_rules(files, rules):
    rows = []
    for rule in (rules or {}).get('rules', []):
        regex = re.compile(rule['path_regex'])
        for path in sorted(files):
            match = regex.match(path)
            if not match:
                continue
            route = match.group('route') or ''
            parts = [p for p in route.split('/') if p and p not in set(rule.get('index_names') or ['index'])]
            url = (rule.get('url_template') or '/{route}').replace('{route}', '/'.join(parts))
            url = '/' + url.strip('/') if url.strip('/') else '/'
            rows.append({'url': url, 'template': path, 'kind': 'page', 'adapter': 'learned:' + (rules.get('framework') or 'unknown'),
                         'is_dynamic': bool(re.search(r'\[[^\]]+\]|:\w+|\{\w+\}', url))})
    return rows
