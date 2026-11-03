"""Atomic change schema, edit-target scoping, the deterministic scope gate and the consolidation/dedup pass.

Change (audit §10 "Recommended change schema"):
  {id, dedup_key, check, field, op, target:{key, file, pointer, line, col, end_col, attr, kind, scope, shared_by, template_n,
   confidence, verified, placement}, current, value, html?, framework_hint, impact, effort, confidence, depends_on,
   conflicts_with, supersedes, evidence:{facts}, rationale, status}
op ∈ set_text | set_attr | set_json | insert_node | remove | platform_config
status ∈ proposed | blocked | needs_input
"""

from __future__ import annotations

import hashlib
import re

from seo_agents.onpage.changes import head_recipes as recipes
from seo_agents.onpage.validation.content_policy import HEAD_FIELDS, IMPACT_RANK, LIMITS, PAGE_SPECIFIC, priority_for

PLACEHOLDER_LINE = 1
PER_INSTANCE_FIELDS = ('images.alt', 'links.anchor', 'links.href', 'links.rel', 'headings.h', 'content.section')
EFFORT = {'set_text': 'S', 'set_attr': 'S', 'set_json': 'S', 'insert_node': 'M', 'remove': 'S', 'platform_config': 'M'}


# ------------------------------------------------------------------ targets
def infer_scope(edit):
    """literal | data | template | layout | shell | platform | unknown (explicit exploration scope wins)."""
    if not edit:
        return 'unknown'
    scope = edit.get('scope')
    if isinstance(scope, str) and scope:
        return scope.split('(')[0]
    kind = edit.get('kind')
    file = edit.get('file')
    if kind == 'platform':
        return 'platform'
    if edit.get('template') or edit.get('composed_from'):
        return 'data' if kind in ('json', 'yaml', 'toml', 'markdown', 'i18n') else 'template'
    if kind in ('json', 'yaml', 'toml', 'markdown', 'i18n', 'cms'):
        return 'data'
    if file and recipes.is_shell_file(file):
        return 'shell'
    if kind in ('literal', 'prop'):
        return 'literal'
    return 'unknown'


def verified_edit(edit, confidence=None):
    """Concrete only when exploration grounded it (verify == 'grounded'), or for older output: confident, not a
    placeholder (line 1 / computed / add) and with a file."""
    if not edit or not edit.get('file'):
        return False
    verify = edit.get('verify')
    if verify is not None:
        return verify == 'grounded'
    if edit.get('kind') in ('add', 'computed', 'cms'):
        return False
    if edit.get('line') in (None, PLACEHOLDER_LINE) and not edit.get('pointer'):
        return False
    return (confidence if confidence is not None else edit.get('confidence') or 0) >= 0.8


def target_of(field_or_edit=None, *, confidence=None, placement=None, attr=None, scope=None, kind=None):
    """Exploration FIELD ({value, edit, confidence}) or bare EDIT -> change target."""
    if isinstance(field_or_edit, dict) and 'edit' in field_or_edit and 'file' not in field_or_edit:
        confidence = field_or_edit.get('confidence') if confidence is None else confidence
        edit = field_or_edit.get('edit')
    else:
        edit = field_or_edit
    if not isinstance(edit, dict) or not edit:
        return None
    template = edit.get('template') if isinstance(edit.get('template'), dict) else None
    target = {
        'file': edit.get('file'), 'pointer': edit.get('pointer'), 'line': edit.get('line'), 'col': edit.get('col'),
        'end_col': edit.get('end_col'), 'attr': attr or edit.get('attr'), 'kind': kind or edit.get('kind'),
        'scope': scope or infer_scope(edit), 'shared_by': list(edit.get('shared_by') or []),
        'template_n': edit.get('template_n'), 'confidence': confidence if confidence is not None else edit.get('confidence'),
        'verified': verified_edit(edit, confidence),
    }
    if edit.get('via'):
        target['via'] = edit['via']
    if template:
        target['template'] = {k: template.get(k) for k in ('file', 'line', 'col') if template.get(k) is not None}
    if placement:
        target['placement'] = placement
    if edit.get('anchor') and kind == 'add':
        target['anchor'] = edit['anchor']
    target['key'] = target_key(target)
    return {k: v for k, v in target.items() if v not in (None, [], '')} | {'verified': target['verified']}


def target_key(target):
    if not target or not target.get('file'):
        return None
    if target.get('pointer'):
        return f"{target['file']}#{target['pointer']}" + (f"@{target['attr']}" if target.get('attr') else '')
    key = f"{target['file']}:{target.get('line') or 0}:{target.get('col') or 0}"
    return key + (f"@{target['attr']}" if target.get('attr') else '')


def platform_target(where, hosting=None):
    return {'kind': 'platform', 'scope': 'platform', 'where': where, 'hosting': hosting, 'verified': True,
            'key': 'platform:' + re.sub(r'\W+', '_', (hosting or where or 'site'))[:40]}


# ------------------------------------------------------------------ changes
def change_id(page_id, dedup_key, op, scope):
    # Shared targets (template/layout/shell/data template) get a page-independent id so the site pass can merge them.
    seed = (dedup_key + '|' + op) if scope in ('template', 'layout', 'shell', 'platform') else (page_id + '|' + dedup_key + '|' + op)
    return 'chg_' + hashlib.sha1(seed.encode()).hexdigest()[:10]


def make_change(ctx, check, field, current, value, *, op, target=None, rationale='', html=None, impact=None,
                confidence=0.8, depends_on=(), status='proposed', facts=(), extra=None, change_id_=None):
    page_id = ctx['page_id'] if ctx else 'site'
    key = (target or {}).get('key')
    dedup_key = (key + ('|' + field if op == 'insert_node' else '')) if key else 'field:' + field
    if op == 'insert_node' and key and (target or {}).get('placement'):
        dedup_key += '|' + target['placement']
    scope = (target or {}).get('scope') or 'unknown'
    if target and not target.get('verified') and op != 'platform_config' and status == 'proposed':
        status = 'needs_input'
    change = {
        'id': change_id_ or change_id(page_id, dedup_key, op, scope), 'dedup_key': dedup_key, 'check': check, 'field': field,
        'op': op, 'target': target, 'current': current, 'value': value,
        'framework_hint': (ctx or {}).get('framework_hint'), 'impact': impact or priority_for(field),
        'effort': EFFORT.get(op, 'M'), 'confidence': round(float(confidence), 2), 'depends_on': list(depends_on),
        'conflicts_with': [], 'supersedes': [], 'evidence': {'facts': list(facts)}, 'rationale': rationale, 'status': status,
    }
    if target is None and op != 'platform_config':
        change['status'] = 'needs_input'
        change['evidence']['target'] = 'no edit location in the exploration JSON; the owner must choose where to apply it'
    if html:
        change['html'] = html
    if extra:
        change.update(extra)
    return change


def advice(ctx, check, topic, text, *, impact='medium', evidence=None, extra=None):
    item = {'id': 'adv_' + hashlib.sha1(((ctx or {}).get('page_id', 'site') + '|' + check + '|' + topic).encode()).hexdigest()[:10],
            'check': check, 'topic': topic, 'advice': text, 'impact': impact}
    if evidence:
        item['evidence'] = evidence
    if extra:
        item.update(extra)
    return item


def client_input(ctx, needed, *, change=None, field=None):
    seed = (ctx or {}).get('page_id', 'site') + '|' + str(change) + '|' + needed
    return {'id': 'inp_' + hashlib.sha1(seed.encode()).hexdigest()[:10], 'needed': needed, 'change': change, 'field': field}


# ------------------------------------------------------------------ deterministic scope gate (audit §10)
def is_head_field(field):
    return field.startswith(HEAD_FIELDS)


def is_page_specific(field):
    return field.startswith(PAGE_SPECIFIC)


RECIPE_KEYS = (('head.description', 'description'), ('head.title', 'title'), ('technical.canonical', 'canonical'),
               ('technical.robots', 'robots'), ('head.json_ld', 'json_ld'), ('head.og', 'og'), ('head.twitter', 'og'),
               ('head.social', 'og'))


def recipe_target(ctx, field):
    recipe = ctx.get('recipe') or {}
    specific = next((recipe.get(key) for prefix, key in RECIPE_KEYS if field.startswith(prefix) and recipe.get(key)), None)
    target = {'kind': 'add', 'scope': 'page', 'where': specific or recipe.get('where') or 'route template',
              'mechanism': recipe.get('mechanism'), 'route': ctx['url'], 'verified': False,
              'key': f"route:{ctx['url']}:{field.split('[')[0]}"}
    if ctx.get('template') and not recipes.is_shell_file(ctx['template']):
        target['file'] = ctx['template']
    return target


def gate(changes, ctx):
    """Head changes on a shared shell/layout are blocked behind prerendering or a per-route head mechanism, and page
    specific content never targets a shared file. Returns (changes, needs_prerender, needs_per_route_head)."""
    head_scope = ctx.get('head_scope') or 'per-route'
    shared_shell = head_scope == 'shared-shell'
    needs_prerender = needs_per_route = False
    for change in changes:
        field, target = change['field'], change.get('target') or {}
        scope = target.get('scope')
        shared_by = target.get('shared_by') or []
        # Without shared_by from exploration, a shell/layout file counts as shared unless the stack gives every route
        # its own file (head_scope per-route, e.g. static HTML where index.html is just the home page).
        on_shared = scope in ('shell', 'layout') and (len(shared_by) > 1 or (not shared_by and head_scope != 'per-route'))
        if change['op'] == 'platform_config':
            continue
        if is_head_field(field) and (shared_shell or on_shared):
            dependency = 'chg_prerender' if shared_shell else 'chg_per_route_head'
            needs_prerender |= shared_shell
            needs_per_route |= not shared_shell
            change['status'] = 'blocked'
            if dependency not in change['depends_on']:
                change['depends_on'].append(dependency)
            if target:
                change['shell_location'] = target
            change['target'] = recipe_target(ctx, field)
            change['dedup_key'] = change['target']['key']
            change['id'] = change_id(ctx['page_id'], change['dedup_key'], change['op'], 'page')
            change['framework_hint'] = (f"{ctx.get('framework') or 'this stack'}: every route is served the same <head>; "
                                        if shared_shell else f"{ctx.get('framework') or 'this stack'}: this head is shared by several routes; ") + \
                (ctx.get('recipe') or {}).get('mechanism', '')
        elif is_page_specific(field) and on_shared:
            change['status'] = 'needs_input'
            change['evidence']['scope'] = (f'target {target.get("file")} is a shared {scope} ({len(shared_by) or "several"} routes); '
                                           'apply in the route\'s own component instead')
    return changes, needs_prerender, needs_per_route


def reference_problems(change):
    """Reference validator: body inserts never into <head>; no per-instance op on a shared template line."""
    target = change.get('target') or {}
    problems = []
    anchor = str(target.get('anchor') or target.get('placement') or '')
    if change['op'] == 'insert_node' and not is_head_field(change['field']) and re.search(r'(?i)<head|</head', anchor):
        problems.append('body content cannot be inserted into <head>')
    if change['op'] in ('set_text', 'set_attr') and target.get('scope') == 'template' \
            and change['field'].startswith(PER_INSTANCE_FIELDS) and not change['field'].endswith('_template'):
        problems.append('per-instance edit on a shared template line (every rendered item would change); edit the data '
                        'item or change the template once for all items')
    if target.get('kind') in ('add', 'computed') and target.get('line') == PLACEHOLDER_LINE and not target.get('anchor'):
        problems.append('line 1 is a placeholder, not an edit location')
    return problems


# ------------------------------------------------------------------ consolidation
def consolidate(changes, ctx):
    """Reference validation, dedup by dedup_key (first/highest impact wins, others recorded in supersedes), conflicts,
    then split into the default view (<= DEFAULT_VIEW_CHANGES) and changes_more."""
    for change in changes:
        problems = reference_problems(change)
        if problems:
            change['status'] = 'needs_input'
            change['evidence']['reference'] = problems
    ordered = sorted(changes, key=lambda c: (IMPACT_RANK.get(c['impact'], 3), -c['confidence']))
    kept, by_key, by_id = [], {}, {}
    for change in ordered:
        same = by_key.get(change['dedup_key']) or by_id.get(change['id'])
        if same is not None:
            if same['value'] == change['value'] or same['op'] == change['op']:
                if change['id'] != same['id']:
                    same['supersedes'].append(change['id'])
                continue
            same['conflicts_with'].append(change['id'])
            change['conflicts_with'].append(same['id'])
            change['id'] = change['id'] + '_b'
        by_key.setdefault(change['dedup_key'], change)
        by_id[change['id']] = change
        kept.append(change)
    limit = LIMITS['DEFAULT_VIEW_CHANGES'][1]
    rank = {'proposed': 0, 'blocked': 1, 'needs_input': 2}
    view = sorted(kept, key=lambda c: (IMPACT_RANK.get(c['impact'], 3), rank.get(c['status'], 3), -c['confidence']))
    main = [c for c in view if c['confidence'] >= 0.5][:limit]
    chosen = {c['id'] for c in main}
    return main, [c for c in view if c['id'] not in chosen]


class ChangeOperations:
    """Tool: build, target, gate, dedup and consolidate atomic change proposals (set_text, set_attr, set_json, ...)."""

    infer_scope = staticmethod(infer_scope)
    verified_edit = staticmethod(verified_edit)
    target_of = staticmethod(target_of)
    target_key = staticmethod(target_key)
    platform_target = staticmethod(platform_target)
    change_id = staticmethod(change_id)
    make_change = staticmethod(make_change)
    advice = staticmethod(advice)
    client_input = staticmethod(client_input)
    is_head_field = staticmethod(is_head_field)
    is_page_specific = staticmethod(is_page_specific)
    recipe_target = staticmethod(recipe_target)
    gate = staticmethod(gate)
    reference_problems = staticmethod(reference_problems)
    consolidate = staticmethod(consolidate)
