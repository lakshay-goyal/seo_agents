"""Apply a resolved gap-worker answer to the page model: edit kind, file/line/col derived in code from the candidate."""

from __future__ import annotations

import re

from seo_agents.repo_analysis.agents.gap_workers.gap_answer_validation import EDIT_KINDS, EEAT_KEYS
from seo_agents.repo_analysis.agents.gap_workers.gap_task_prompts import LIMITS

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
            from seo_agents.repo_analysis.site_model.url_manifest import substitute
            urls = [substitute(page['route'], str(v)) for v in group['values'][:500]]
        page['instances_suggested'] = {'urls': urls, 'source': {'file': group['file'], 'pointer_pattern': group['pointer_pattern']},
                                       'method': method}
        return True
    return False
