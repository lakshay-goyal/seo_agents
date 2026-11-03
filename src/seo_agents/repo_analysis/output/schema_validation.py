"""Tiny JSON Schema validator (the subset the exploration schemas use) + the schemas themselves.

Supported keywords: type, required, properties, additionalProperties (bool), items, enum, pattern,
minimum, maximum, anyOf, $ref (local "#/$defs/..."). No dependency on `jsonschema`.
"""

from __future__ import annotations

import re

SCHEMA_VERSION = 'seo-exploration/2.0'
BASE_ID = 'https://seo-agents.local/schemas/exploration/2.0/'

TYPES = {'object': dict, 'array': list, 'string': str, 'boolean': bool, 'null': type(None)}


def _type_ok(value, name):
    if name == 'integer':
        return isinstance(value, int) and not isinstance(value, bool)
    if name == 'number':
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, TYPES[name])


def validate(instance, schema, root=None, path='', errors=None, limit=50):
    """Return a list of 'path: message' strings (empty when valid)."""
    errors = [] if errors is None else errors
    root = root or schema
    if len(errors) >= limit:
        return errors
    if '$ref' in schema:
        target = root
        for part in schema['$ref'].lstrip('#/').split('/'):
            target = target[part]
        return validate(instance, target, root, path, errors, limit)
    if 'anyOf' in schema:
        if not any(not validate(instance, option, root, path, [], limit) for option in schema['anyOf']):
            errors.append(f'{path or "/"}: matches no allowed shape')
        return errors
    kind = schema.get('type')
    if kind is not None:
        kinds = kind if isinstance(kind, list) else [kind]
        if not any(_type_ok(instance, k) for k in kinds):
            errors.append(f'{path or "/"}: expected {kind}, got {type(instance).__name__}')
            return errors
    if 'enum' in schema and instance not in schema['enum']:
        errors.append(f'{path or "/"}: {instance!r} not in {schema["enum"]}')
    if isinstance(instance, str) and 'pattern' in schema and not re.search(schema['pattern'], instance):
        errors.append(f'{path or "/"}: {instance[:40]!r} does not match {schema["pattern"]}')
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if 'minimum' in schema and instance < schema['minimum']:
            errors.append(f'{path or "/"}: below minimum {schema["minimum"]}')
        if 'maximum' in schema and instance > schema['maximum']:
            errors.append(f'{path or "/"}: above maximum {schema["maximum"]}')
    if isinstance(instance, dict):
        for key in schema.get('required', []):
            if key not in instance:
                errors.append(f'{path}/{key}: required')
        properties = schema.get('properties', {})
        extra = schema.get('additionalProperties', True)
        for key, value in instance.items():
            if key in properties:
                validate(value, properties[key], root, f'{path}/{key}', errors, limit)
            elif extra is False:
                errors.append(f'{path}/{key}: unexpected property')
            elif isinstance(extra, dict):
                validate(value, extra, root, f'{path}/{key}', errors, limit)
    if isinstance(instance, list) and 'items' in schema:
        for index, value in enumerate(instance):
            validate(value, schema['items'], root, f'{path}/{index}', errors, limit)
    return errors


# ---------------------------------------------------------------- shared definitions (§11 EDIT / FACT / ISSUE)
EDIT_KINDS = ['literal', 'json', 'yaml', 'toml', 'markdown', 'i18n', 'prop', 'computed', 'add', 'platform']
SCOPES = ['shell', 'layout', 'template', 'data', 'literal']
DEFS = {
    'edit': {
        'type': 'object', 'required': ['kind'],
        'properties': {
            'kind': {'enum': EDIT_KINDS}, 'file': {'type': 'string', 'pattern': r'^(?!/)'},
            'line': {'type': 'integer', 'minimum': 1}, 'col': {'type': 'integer', 'minimum': 0},
            'end_line': {'type': 'integer', 'minimum': 1}, 'end_col': {'type': 'integer', 'minimum': 0},
            'pointer': {'type': 'string'}, 'attr': {'type': 'string'}, 'scope': {'enum': SCOPES},
            'template_n': {'type': 'integer', 'minimum': 1},
            'shared_by': {'type': 'array', 'items': {'type': 'string', 'pattern': '^r_'}},
            'verify': {'enum': ['grounded', 'heuristic', 'failed']}, 'evidence': {'type': 'string', 'pattern': '^ev_'},
        },
    },
    'fact': {
        'type': 'object', 'required': ['method'],
        'properties': {'method': {'type': 'string'}, 'conf': {'type': 'number', 'minimum': 0, 'maximum': 1},
                       'edit': {'$ref': '#/$defs/edit'}},
    },
    'issue': {
        'type': 'object', 'required': ['id', 'severity', 'scope', 'title', 'fix', 'status'],
        'properties': {
            'id': {'type': 'string', 'pattern': '^iss_'}, 'severity': {'enum': ['high', 'medium', 'low', 'info']},
            'scope': {'type': 'string', 'pattern': r'^(site|routes|route:r_\w+)$'},
            'routes': {'type': 'array', 'items': {'type': 'string', 'pattern': '^r_'}},
            'title': {'type': 'string'}, 'facts': {'type': 'array', 'items': {'type': 'string'}},
            'fix': {'type': 'object', 'required': ['kind'],
                    'properties': {'kind': {'enum': ['code', 'data', 'content', 'platform', 'build', 'none']}}},
            'status': {'enum': ['measured', 'lead']},
        },
    },
}


def schema_doc(name, body):
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema', '$id': BASE_ID + name + '.schema.json',
            'title': name, '$defs': DEFS, **body}


SCHEMAS = {
    'manifest': schema_doc('manifest', {
        'type': 'object', 'required': ['schema', 'snapshot_sha256', 'framework', 'lens', 'files', 'coverage'],
        'properties': {'schema': {'enum': [SCHEMA_VERSION]}, 'files': {'type': 'array', 'items': {
            'type': 'object', 'required': ['path', 'sha256', 'bytes', 'role'],
            'properties': {'role': {'enum': ['planner', 'shard', 'page', 'schema']}}}}}}),
    'summary': schema_doc('summary', {
        'type': 'object', 'required': ['schema', 'issues'],
        'properties': {'issues': {'type': 'array', 'items': {'$ref': '#/$defs/issue'}}}}),
    'routes': schema_doc('routes', {
        'type': 'object', 'required': ['routes'],
        'properties': {'routes': {'type': 'array', 'items': {
            'type': 'object', 'required': ['route_id', 'url'],
            'properties': {'route_id': {'type': 'string', 'pattern': '^r_'}, 'url': {'type': 'string'},
                           'render': {'enum': ['csr', 'ssr', 'ssg', 'isr', 'static', 'hybrid', 'unknown']},
                           'head_scope': {'enum': ['shared-shell', 'shared-layout', 'per-route']}}}}}}),
    'page': schema_doc('page', {
        'type': 'object', 'required': ['route_id', 'url'],
        'properties': {'route_id': {'type': 'string', 'pattern': '^r_'},
                       'head': {'type': 'object', 'additionalProperties': {'$ref': '#/$defs/fact'}},
                       'headings': {'type': 'array', 'items': {'type': 'object', 'required': ['level'],
                                                               'properties': {'level': {'type': 'integer', 'minimum': 1, 'maximum': 6},
                                                                              'edit': {'$ref': '#/$defs/edit'}}}},
                       'link_ids': {'type': 'array', 'items': {'type': 'string', 'pattern': '^lnk_'}},
                       'image_ids': {'type': 'array', 'items': {'type': 'string', 'pattern': '^img_'}}}}),
    'images': schema_doc('images', {
        'type': 'object', 'required': ['images'],
        'properties': {'images': {'type': 'array', 'items': {
            'type': 'object', 'required': ['img_id', 'used_on'],
            'properties': {'img_id': {'type': 'string', 'pattern': '^img_'},
                           'alt_status': {'enum': ['missing', 'empty', 'literal', 'expression']},
                           'alt_edit': {'$ref': '#/$defs/edit'}, 'src_edit': {'$ref': '#/$defs/edit'},
                           'used_on': {'type': 'array', 'items': {'type': 'string', 'pattern': '^r_'}}}}}}}),
    'links': schema_doc('links', {
        'type': 'object', 'required': ['links'],
        'properties': {'links': {'type': 'array', 'items': {
            'type': 'object', 'required': ['link_id', 'kind', 'used_on'],
            'properties': {'link_id': {'type': 'string', 'pattern': '^lnk_'},
                           'kind': {'enum': ['internal', 'external', 'anchor', 'js-only', 'contact']},
                           'href_edit': {'$ref': '#/$defs/edit'}, 'anchor_edit': {'$ref': '#/$defs/edit'},
                           'used_on': {'type': 'array', 'items': {'type': 'string', 'pattern': '^r_'}}}}}}}),
    'metadata': schema_doc('metadata', {'type': 'object', 'required': ['routes']}),
    'structured_data': schema_doc('structured_data', {'type': 'object', 'required': ['routes']}),
    'site': schema_doc('site', {'type': 'object'}),
    'entity': schema_doc('entity', {'type': 'object'}),
    'performance': schema_doc('performance', {'type': 'object', 'required': ['routes']}),
    'keywords': schema_doc('keywords', {'type': 'object', 'required': ['routes']}),
}

FILE_SCHEMA = {'manifest.json': 'manifest', 'summary.json': 'summary', 'routes.json': 'routes', 'images.json': 'images',
               'links.json': 'links', 'metadata.json': 'metadata', 'structured_data.json': 'structured_data',
               'site.json': 'site', 'entity.json': 'entity', 'performance.json': 'performance', 'keywords.json': 'keywords'}


def schema_for(path):
    if path.startswith('pages/'):
        return SCHEMAS['page']
    name = FILE_SCHEMA.get(path)
    return SCHEMAS[name] if name else None
