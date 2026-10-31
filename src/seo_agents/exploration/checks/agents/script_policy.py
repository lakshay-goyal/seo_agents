"""Validate a small Python extraction language before any execution.

This module is also loaded by the isolated stdlib-only worker. Generated code has
no imports, host objects, filesystem, network, introspection, or arbitrary calls.
"""

import ast

BUILTINS = ('str', 'int', 'float', 'bool', 'len', 'list', 'dict', 'tuple', 'set',
            'sorted', 'enumerate', 'range', 'zip', 'min', 'max', 'sum', 'any', 'all',
            'abs', 'round', 'isinstance', 'reversed')
HELPERS = ('parse_html', 'json_loads', 're_findall', 're_sub', 're_escape', 'resolve_url')
METHODS = {'get', 'items', 'keys', 'values', 'update', 'setdefault', 'append', 'extend',
           'strip', 'lstrip', 'rstrip', 'split', 'rsplit', 'splitlines', 'join',
           'lower', 'upper', 'replace', 'startswith', 'endswith', 'find', 'rfind',
           'count', 'removeprefix', 'removesuffix', 'isdigit', 'isalnum', 'index',
           'copy', 'add', 'pop'}
NODES = {
    'Module', 'FunctionDef', 'arguments', 'arg', 'Return', 'Assign', 'AugAssign',
    'Expr', 'If', 'For', 'While', 'Break', 'Continue', 'Pass', 'Name', 'Load', 'Store',
    'Constant', 'Dict', 'List', 'Tuple', 'Set', 'Subscript', 'Slice', 'Attribute',
    'Call', 'keyword', 'BinOp', 'UnaryOp', 'BoolOp', 'Compare', 'IfExp',
    'ListComp', 'DictComp', 'SetComp', 'GeneratorExp', 'comprehension',
    'JoinedStr', 'FormattedValue', 'Add', 'Sub', 'Mult', 'Div', 'FloorDiv', 'Mod',
    'USub', 'UAdd', 'Not', 'And', 'Or', 'Eq', 'NotEq', 'Lt', 'LtE', 'Gt', 'GtE',
    'In', 'NotIn', 'Is', 'IsNot', 'BitOr', 'BitAnd',
}


def validate_script(source):
    if not isinstance(source, str) or len(source.encode()) > 100_000:
        raise ValueError('Expected a Python script of at most 100000 bytes')
    tree = ast.parse(source)
    problems = {}

    def reject(message, node=None, detail=''):
        item = problems.setdefault(message, {'count': 0, 'examples': []})
        item['count'] += 1
        if len(item['examples']) < 3:
            item['examples'].append('line ' + str(getattr(node, 'lineno', '?')) + (' ' + detail[:60] if detail else ''))

    top_functions = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    functions = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    if 'extract' not in top_functions:
        reject('Missing def extract(files, assets)')
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            reject('Script must contain only functions; move constants inside a function and remove imports', node, type(node).__name__)
    allowed_calls = set(BUILTINS) | set(HELPERS) | functions
    for node in ast.walk(tree):
        if type(node).__name__ not in NODES:
            reject('Unsupported Python syntax: ' + type(node).__name__, node)
        if isinstance(node, (ast.Name, ast.arg, ast.FunctionDef)):
            name = node.id if isinstance(node, ast.Name) else node.arg if isinstance(node, ast.arg) else node.name
            if name.startswith('_'):
                reject('Private names are unavailable; rename identifiers without a leading underscore', node, name)
        if isinstance(node, ast.FunctionDef) and (node.decorator_list or node.returns or node.type_params):
            reject('Decorators, annotations and type parameters are unavailable', node)
        if isinstance(node, ast.arg) and node.annotation:
            reject('Type annotations are unavailable', node)
        if isinstance(node, ast.Attribute):
            if node.attr not in METHODS or not isinstance(node.ctx, ast.Load):
                reject('Unavailable method: ' + node.attr[:60], node)
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id not in allowed_calls:
                    reject('Unavailable function: ' + node.func.id[:60], node)
            elif not isinstance(node.func, ast.Attribute):
                reject('Indirect calls are unavailable', node)
    entry = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'extract'), None)
    if entry and ([a.arg for a in entry.args.args] != ['files', 'assets'] or entry.args.posonlyargs
            or entry.args.kwonlyargs or entry.args.vararg or entry.args.kwarg or entry.args.defaults):
        reject('Entry point must be def extract(files, assets)', entry)
    if problems:
        descriptions = []
        for message, item in problems.items():
            description = message + ' (' + str(item['count']) + '; ' + ', '.join(item['examples']) + ')'
            if len('; '.join(descriptions + [description])) > 1700:
                break
            descriptions.append(description)
        if len(descriptions) < len(problems):
            descriptions.append(str(len(problems) - len(descriptions)) + ' further violation kinds omitted')
        raise ValueError('Extractor policy violations: ' + '; '.join(descriptions) + '. Fix all listed violations before resubmitting.')
    return tree
