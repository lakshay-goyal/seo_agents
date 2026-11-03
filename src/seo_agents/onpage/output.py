"""Write seo_reports/onpage/: index.json, site.json, client_inputs.json, pages/<id>.json (+ .expected.html), README."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path

from seo_agents.onpage.writer import skill_catalog

SCHEMA = 'seo-onpage/2.0'
README = """# On-page SEO proposals (`seo_reports/onpage/`)

Generated from the exploration JSON only; no client source was read or changed. Review, then apply what you approve.

- `index.json` - run metadata, checklist definition, one row per page (counts, before/after checklist).
- `site.json` - site-wide changes: platform changes (prerender / per-route head, permanent host redirect, soft 404),
  robots.txt, sitemap.xml, llms.txt, cross-page duplicates, topic clusters.
- `client_inputs.json` - facts only the owner can supply (`[VERIFY: ...]`, unconfirmed names, share image, dates).
- `pages/<id>.json`:
  - `changes`      : at most 15 deduplicated, high-confidence atomic changes (the default review view)
  - `changes_more` : the remaining lower-impact / lower-confidence changes
  - `advice`       : guidance and reports that are not a single edit (prerender rationale, image weight, E-E-A-T)
  Each change is ONE operation: `op` (set_text | set_attr | set_json | insert_node | remove | platform_config) on
  `target` (file, pointer or line/col, attr, `scope`: literal | data | template | layout | shell | page | platform,
  `shared_by`, `verified`), with `current`, `value`, `depends_on`, `conflicts_with`, `supersedes` and
  `status`: proposed | blocked (waits for `depends_on`, e.g. `chg_prerender`) | needs_input (unverified target or facts).
- `pages/<id>.expected.html` - review aid generated from the change list; NOT directly applicable.
"""


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')


def model_info():
    try:
        from seo_agents.exploration.repo_analysis.tools.llm import model_for_tier, usage_summary
        return {'tier_override': os.getenv('SEO_ONPAGE_TIER'), 'small': model_for_tier('small'), 'mid': model_for_tier('mid'),
                'usage': usage_summary()}
    except Exception as error:  # noqa: BLE001
        return {'error': str(error)}


def trimmed(res):
    data = dict(res.get('data') or {})
    for key in ('expected_page_html', 'changes', 'changes_more', 'advice', 'client_inputs', 'site_changes'):
        data.pop(key, None)
    return {'status': res.get('status'), 'meta': res.get('meta'), 'errors': res.get('errors'), 'data': data}


def counts(changes, key):
    out = {}
    for c in changes:
        out[c[key]] = out.get(c[key], 0) + 1
    return out


def write_outputs(out_dir, state):
    out = Path(out_dir)
    files = []
    contexts, results = state['contexts'], state.get('results') or {}
    from seo_agents.onpage.checks import CHECKS
    order = [c[0] for c in CHECKS]
    rows, all_inputs = [], []
    for page_id, ctx in contexts.items():
        page_results = results.get(page_id) or {}
        qa = (page_results.get('qa') or {}).get('data') or {}
        changes, more = qa.get('changes') or [], qa.get('changes_more') or []
        inputs = qa.get('client_inputs') or []
        all_inputs += [{**item, 'page': page_id, 'url': ctx['url']} for item in inputs]
        record = {
            'schema': SCHEMA, 'id': page_id, 'url': ctx['url'], 'absolute_url': ctx['absolute_url'], 'template': ctx['template'],
            'lang': ctx['lang'], 'run_date': ctx['run_date'], 'framework': ctx['fingerprint'], 'head_scope': ctx['head_scope'],
            'framework_hint': ctx['framework_hint'], 'hosting': ctx['hosting'],
            'intent': (page_results.get('intent') or {}).get('data'),
            'keyword_plan': (page_results.get('keyword_plan') or {}).get('data'),
            'checklist': qa.get('checklist'),
            'changes': changes, 'changes_more': more, 'advice': qa.get('advice') or [], 'client_inputs': inputs,
            'site_changes': [c['id'] for c in qa.get('site_changes') or []],
            'checks': {name: trimmed(page_results[name]) for name in order if name in page_results},
        }
        dump(out / 'pages' / (page_id + '.json'), record)
        files.append('pages/' + page_id + '.json')
        if qa.get('expected_page_html'):
            (out / 'pages' / (page_id + '.expected.html')).write_text(qa['expected_page_html'])
            files.append('pages/' + page_id + '.expected.html')
        checklist = qa.get('checklist') or {}
        rows.append({'id': page_id, 'url': ctx['url'], 'file': 'pages/' + page_id + '.json',
                     'expected_html': 'pages/' + page_id + '.expected.html' if qa.get('expected_page_html') else None,
                     'primary_query': ((page_results.get('intent') or {}).get('data') or {}).get('primary_query'),
                     'head_scope': ctx['head_scope'], 'changes': len(changes), 'changes_more': len(more),
                     'advice': len(qa.get('advice') or []), 'client_inputs': len(inputs),
                     'by_status': counts(changes + more, 'status'), 'by_impact': counts(changes + more, 'impact'),
                     'checks': {name: (page_results.get(name) or {}).get('status') for name in order},
                     'checklist_after_pass': sum(1 for x in checklist.values() if x.get('after') is True),
                     'checklist_before_pass': sum(1 for x in checklist.values() if x.get('before') is True),
                     'checklist_total': len(checklist)})
    site = state.get('site') or {}
    dump(out / 'site.json', {'schema': SCHEMA, **site})
    files.append('site.json')
    all_inputs += [{**item, 'page': None, 'url': None} for item in site.get('client_inputs') or []]
    dump(out / 'client_inputs.json', {'schema': SCHEMA, 'inputs': all_inputs})
    files.append('client_inputs.json')
    skills = {s['name']: s for s in skill_catalog()}
    index = {
        'schema': SCHEMA,
        'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'source': state.get('reports_meta'),
        'model': model_info(),
        'workflow': 'LangGraph linear: load -> ' + ' -> '.join(order) + ' -> site -> write',
        'checklist': [{'order': i + 1, 'check': name, 'skill': skill, 'uses_llm': llm, 'tier': (skills.get(skill) or {}).get('tier'),
                       'description': (skills.get(skill) or {}).get('description')} for i, (name, skill, _, llm) in enumerate(CHECKS)],
        'how_to_review': 'Start with site.json platform changes (they unblock `blocked` page changes), then pages/<id>.json '
                         'changes[]. Apply only `proposed` changes whose target.verified is true; needs_input items need the owner.',
        'pages': rows,
        'site_file': 'site.json',
        'client_inputs_file': 'client_inputs.json',
    }
    dump(out / 'index.json', index)
    files.append('index.json')
    (out / 'README.md').write_text(README)
    files.append('README.md')
    return {'out_dir': str(out), 'files': files}
