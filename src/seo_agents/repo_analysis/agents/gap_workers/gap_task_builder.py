"""Gap-worker task construction: one self-contained packet (code excerpts, candidates, helpers) per attribution gap."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import os
import re

from seo_agents.repo_analysis.agents.gap_workers.gap_task_prompts import LIMITS

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
