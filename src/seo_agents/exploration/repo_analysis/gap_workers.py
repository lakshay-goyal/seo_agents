"""Stage F: cheap-model gap workers (doc §13–§14).

Each gap becomes a self-contained task packet (target <= ~6k tokens) that needs no
tools. The model answers with a constrained JSON object (candidate IDs and enums,
never free-form coordinates); code validates it and fills file/line/col.

Accuracy comes from the validator, not the model:
  small tier -> validate -> one fresh retry with the exact error -> mid tier -> unresolved.
Judgment tasks (topic/intent, E-E-A-T) use self-consistency: 3 small votes, majority.

Task types: attribute_value, explain_computed, topic_intent, eeat_classify,
enumerate_instances, route_adapter (mid tier; cached per framework on disk).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re

from seo_agents.log import log

SKILLS_DIR = Path(__file__).parent / 'agents' / 'gap' / 'skills'
TASK_SKILL = {'attribute_value': 'attribute-value', 'explain_computed': 'explain-computed', 'topic_intent': 'topic-intent',
              'eeat_classify': 'eeat-classify', 'enumerate_instances': 'enumerate-instances', 'route_adapter': 'route-adapter'}
EDIT_KINDS = ['literal', 'prop', 'json', 'yaml', 'markdown', 'i18n', 'computed', 'cms']
INTENTS = ['informational', 'commercial', 'transactional', 'navigational', 'local']
TRI = ['present', 'absent', 'unclear']
CACHE_DIR = Path(os.getenv('SEO_ADAPTER_CACHE') or Path.home() / '.cache' / 'seo_agents' / 'adapters')


def load_skill(name, family=None):
    """Skill text with only the examples relevant to this framework family (progressive disclosure)."""
    text = (SKILLS_DIR / name / 'SKILL.md').read_text()
    body = text.split('---', 2)[-1].strip() if text.startswith('---') else text
    meta = dict(re.findall(r'(?m)^(\w+):\s*(.+)$', text.split('---', 2)[1])) if text.startswith('---') else {}
    match = re.search(r'(## Examples\n)(.*?)(?=\n## |\Z)', body, re.S)
    if match:
        sections = re.split(r'(?m)^(?=### )', match.group(2))
        intro = [s for s in sections if not s.startswith('### ')]
        named = [s for s in sections if s.startswith('### ')]
        keep = [s for s in named if family and s[4:].split('\n', 1)[0].strip() in family_aliases(family)] or named[:2]
        body = body[:match.start(2)] + ''.join(intro + keep).rstrip() + '\n' + body[match.end(2):]
    return body, meta


def family_aliases(family):
    return {'react': {'jsx', 'next'}, 'vue': {'vue', 'jsx'}, 'svelte': {'svelte', 'jsx'}, 'astro': {'astro', 'jsx'},
            'liquid': {'liquid'}, 'php': {'php'}, 'blade': {'php'}, 'twig': {'php', 'jinja'}, 'jinja': {'jinja', 'php'},
            'erb': {'php'}, 'handlebars': {'liquid'}, 'go-template': {'liquid'}, 'html': {'php', 'jsx'}}.get(family, {family})


def parse_json(text):
    if isinstance(text, list):
        text = ''.join(part.get('text', '') if isinstance(part, dict) else str(part) for part in text)
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end < start:
        raise ValueError('answer is not a JSON object')
    return json.loads(text[start:end + 1])


# ---------------------------------------------------------------- validators
def validate(task, answer):
    kind = task['task']
    if not isinstance(answer, dict):
        raise ValueError('expected a JSON object')
    if kind == 'attribute_value':
        ids = {c['id'] for c in task['packet']['candidates']} | {'none'}
        if answer.get('candidate') not in ids:
            raise ValueError('candidate must be one of ' + ', '.join(sorted(ids)))
        if answer.get('edit_kind') not in EDIT_KINDS:
            raise ValueError('edit_kind must be one of ' + ', '.join(EDIT_KINDS))
    elif kind == 'explain_computed':
        ids = {l['id'] for l in task['packet']['lines']} | {'none'}
        if answer.get('line_id') not in ids:
            raise ValueError('line_id must be one of the given line ids or none')
        if answer.get('edit_kind') not in EDIT_KINDS:
            raise ValueError('edit_kind must be one of ' + ', '.join(EDIT_KINDS))
    elif kind == 'topic_intent':
        if answer.get('intent') not in INTENTS:
            raise ValueError('intent must be one of ' + ', '.join(INTENTS))
        queries = answer.get('target_queries')
        if not isinstance(queries, list) or len(queries) != 3 or not all(isinstance(q, str) and q.strip() for q in queries):
            raise ValueError('target_queries must be exactly 3 non-empty strings')
        if not isinstance(answer.get('primary_topic'), str) or not 0 < len(answer['primary_topic'].split()) <= 8:
            raise ValueError('primary_topic must be 1-8 words')
    elif kind == 'eeat_classify':
        for key in ('authorship', 'dates', 'expertise_evidence', 'trust_pages', 'citations'):
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
        if not isinstance(rules, list) or not 1 <= len(rules) <= 8:
            raise ValueError('rules must be a list of 1-8 rules')
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
def default_factory(tier, temperature=0, label=None):
    from seo_agents.exploration.repo_analysis.tools.llm import get_chat_model
    return get_chat_model(tier, max_tokens=1500 if tier == 'small' else 6000, label=label, temperature=temperature)


def ask(model, system, packet, feedback=None):
    from langchain_core.messages import HumanMessage, SystemMessage
    content = json.dumps(packet, ensure_ascii=False)
    if feedback:
        content += '\n\nYour previous answer was rejected: ' + feedback + '\nReturn a corrected JSON object only.'
    response = model.invoke([SystemMessage(content=system), HumanMessage(content=content)])
    return parse_json(response.content)


GAP_CACHE = Path(os.getenv('SEO_GAP_CACHE') if os.getenv('SEO_GAP_CACHE') not in {None, '', '0'} else Path.home() / '.cache' / 'seo_agents' / 'gap')


def cache_key(task, skill):
    from seo_agents.exploration.repo_analysis.tools.llm import model_for_tier
    payload = json.dumps([task['task'], task['packet'], skill, model_for_tier('small'), model_for_tier('mid')],
                         sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def solve(task, family=None, *, model_factory=None):
    """Validated answer for one task; resolved answers are cached by packet hash (incremental re-runs)."""
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


def solve_uncached(task, family=None, *, model_factory=None):
    """Validated answer for one task: small -> fresh retry -> mid -> unresolved."""
    factory = model_factory or default_factory
    skill, meta = load_skill(TASK_SKILL[task['task']], family)
    tiers = ['mid'] if meta.get('tier') == 'mid' else ['small', 'small', 'mid']
    votes = int(meta.get('votes', '1'))
    errors = []
    if votes > 1:
        answers = []
        for index in range(votes):
            try:
                answers.append(validate(task, ask(factory('small', 0.7 if index else 0, task['task']), skill, task['packet'])))
            except Exception as error:  # noqa: BLE001 - each vote is independent; failures are counted
                errors.append(type(error).__name__ + ': ' + str(error)[:160])
        if answers:
            merged, agreement = majority(answers)
            return {'status': 'resolved', 'answer': merged, 'tier': 'small', 'votes': len(answers), 'agreement': agreement, 'errors': errors}
        tiers = ['mid']
    feedback = None
    for attempt, tier in enumerate(tiers, 1):
        try:
            answer = validate(task, ask(factory(tier, 0, task['task']), skill, task['packet'], feedback))
            return {'status': 'resolved', 'answer': answer, 'tier': tier, 'attempts': attempt, 'errors': errors}
        except Exception as error:  # noqa: BLE001 - invalid answers are retried, never applied
            feedback = type(error).__name__ + ': ' + str(error)[:200]
            errors.append(feedback)
    return {'status': 'unresolved', 'answer': None, 'tier': tiers[-1], 'attempts': len(tiers), 'errors': errors}


def majority(answers):
    merged, agreement = {}, []
    for key in answers[0]:
        values = [json.dumps(a.get(key), sort_keys=True) for a in answers]
        value, count = Counter(values).most_common(1)[0]
        merged[key] = json.loads(value)
        if not isinstance(answers[0].get(key), (list, str)) or key in {'intent', 'authorship', 'dates', 'expertise_evidence', 'trust_pages', 'citations'}:
            agreement.append(count / len(answers))
    return merged, round(sum(agreement) / len(agreement), 2) if agreement else 1.0


# ---------------------------------------------------------------- task construction
def code_lines(files, file, line, radius=6, prefix='L'):
    lines = files.get(file, '').splitlines()
    rows = []
    for number in range(max(1, line - radius), min(len(lines), line + radius) + 1):
        rows.append({'id': f'{prefix}{number}', 'file': file, 'line': number, 'code': lines[number - 1][:200]})
    return rows


def build_tasks(gaps, pages, ctx, fingerprint):
    limit = int(os.getenv('SEO_GAP_MAX_TASKS', '200'))
    tasks = []
    for gap in gaps:
        if gap['task'] == 'attribute_value':
            tasks.append({'task': 'attribute_value', 'page_id': gap['page_id'], 'field': gap['field'],
                          'packet': {'field': gap['field'], 'observed': gap['observed'],
                                     'page': {'url': gap.get('url'), 'template': gap.get('template')},
                                     'candidates': [{k: c.get(k) for k in ('id', 'kind', 'file', 'line', 'snippet', 'pointer', 'i18n_key', 'attr')
                                                     if c.get(k) is not None} for c in gap['candidates'][:10]]}})
    for page in pages:
        for key in ('title', 'description'):
            item = page['head'][key]
            edit = item.get('edit') or {}
            if edit.get('kind') == 'computed' and edit.get('file') in ctx.files and isinstance(edit.get('line'), int):
                lines = code_lines(ctx.files, edit['file'], edit['line'])
                tasks.append({'task': 'explain_computed', 'page_id': page['id'], 'field': '/head/' + key,
                              'packet': {'field': '/head/' + key, 'page': {'url': page['url'], 'template': page['template']},
                                         'expression': edit.get('via', '')[:200], 'lines': lines}})
    concrete = [p for p in pages if p['instance_status'] != 'template' and p['kind'] != 'not-found']
    for page in concrete[:int(os.getenv('SEO_TOPIC_MAX_PAGES', '40'))]:
        copy = ' '.join(b['text'] for b in page['content']['blocks'] if b.get('section') not in {'header', 'nav', 'footer', 'aside'})
        if not (page['head']['title'].get('value') or copy.strip() or any(h.get('text') for h in page['headings'])):
            continue  # nothing to classify; do not spend a call
        tasks.append({'task': 'topic_intent', 'page_id': page['id'],
                      'packet': {'url': page['url'], 'title': page['head']['title'].get('value'),
                                 'description': page['head']['description'].get('value'),
                                 'headings': [h['text'] for h in page['headings'] if h['level'] <= 2 and h.get('text')][:8],
                                 'copy': ' '.join(copy.split()[:300])}})
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


def apply(task, result, page, ctx):
    answer = result['answer']
    method = 'llm:' + result['tier']
    if task['task'] == 'attribute_value':
        item = resolve_pointer(page, task['field'])
        if item is None or answer['candidate'] == 'none':
            return False
        chosen = next(c for c in task['packet']['candidates'] if c['id'] == answer['candidate'])
        edit = {k: chosen[k] for k in ('file', 'line', 'pointer', 'i18n_key', 'attr') if chosen.get(k) is not None}
        item['edit'] = {'kind': answer['edit_kind'], **edit, 'resolved_by': method, 'reason': (answer.get('reason') or '')[:200]}
        item['confidence'] = 0.8
        item.pop('candidates', None)
        item.pop('gap', None)
        return True
    if task['task'] == 'explain_computed':
        item = page['head'][task['field'].rsplit('/', 1)[-1]]
        line = next((l for l in task['packet']['lines'] if l['id'] == answer['line_id']), None)
        if line:
            item['edit'] = {'kind': answer['edit_kind'], 'file': line['file'], 'line': line['line'], 'resolved_by': method,
                            'via': (answer.get('value_source') or '')[:160]}
        else:
            item['edit'] = {**(item.get('edit') or {}), 'kind': answer['edit_kind'], 'resolved_by': method,
                            'via': (answer.get('value_source') or '')[:160]}
        item['confidence'] = max(item.get('confidence') or 0, 0.7)
        return True
    if task['task'] == 'topic_intent':
        page['keywords'].update(topic=answer['primary_topic'], intent=answer['intent'],
                                target_queries=answer['target_queries'], topic_method=method,
                                topic_agreement=result.get('agreement'))
        return True
    if task['task'] == 'eeat_classify':
        page['eeat_assessment'] = {**{k: answer[k] for k in ('authorship', 'dates', 'expertise_evidence', 'trust_pages', 'citations')},
                                   'evidence_ids': answer.get('evidence_ids', []), 'method': method, 'agreement': result.get('agreement'),
                                   'reason': (answer.get('reason') or '')[:200]}
        return True
    if task['task'] == 'enumerate_instances':
        if answer['group'] == 'none':
            return False
        group = next(g for g in task['_groups'] if g['id'] == answer['group'])
        from seo_agents.exploration.repo_analysis.url_manifest import substitute
        page['instances_suggested'] = {'urls': [substitute(page['route'], str(v)) for v in group['values'][:500]],
                                       'source': {'file': group['file'], 'pointer_pattern': group['pointer_pattern']},
                                       'method': method}
        return True
    return False


def run_gap_workers(gaps, pages, ctx, fingerprint, *, model_factory=None):
    tasks = build_tasks(gaps, pages, ctx, fingerprint)
    by_id = {page['id']: page for page in pages}
    workers = int(os.getenv('SEO_GAP_CONCURRENCY', '6'))
    family = fingerprint.get('family')
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(lambda task: solve(task, family, model_factory=model_factory), tasks))
    counts = Counter()
    for task, result in zip(tasks, results):
        counts[task['task'] + ':' + result['status']] += 1
        if result['status'] == 'resolved' and apply(task, result, by_id[task['page_id']], ctx):
            counts['applied'] += 1
    report = {'total': len(tasks), 'resolved': sum(r['status'] == 'resolved' for r in results), 'applied': counts['applied'],
              'by_outcome': dict(counts), 'escalated_to_mid': sum(r['tier'] == 'mid' for r in results),
              'status': 'completed', 'unresolved': [{'task': t['task'], 'page_id': t['page_id'], 'field': t.get('field'),
                                                    'errors': r['errors'][-2:]} for t, r in zip(tasks, results) if r['status'] != 'resolved'][:50]}
    log('gap workers: ' + json.dumps({k: v for k, v in report.items() if k != 'unresolved'}))
    return report


# ---------------------------------------------------------------- learned adapters (unknown frameworks)
def learn_adapter(snapshot, fingerprint, *, model_factory=None):
    """Declarative URL rules for an unrecognised stack, generated once (mid tier) and cached on disk."""
    files = sorted(p for p in snapshot['files'] if not re.search(r'(?:^|/)(?:node_modules|tests?|__tests__|\.git)/', p))[:400]
    names = sorted(c['name'] for c in fingerprint.get('candidates', []))
    key = hashlib.sha256(json.dumps([names, fingerprint.get('version', '') and str(fingerprint['version']).split('.')[0],
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
            'packet': {'files': files, 'candidates': names,
                       'manifests': sorted({c['name'] for c in fingerprint.get('candidates', [])})}}
    result = solve(task, fingerprint.get('family'), model_factory=model_factory)
    if result['status'] != 'resolved':
        return None, 'unresolved'
    rules = result['answer']
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
