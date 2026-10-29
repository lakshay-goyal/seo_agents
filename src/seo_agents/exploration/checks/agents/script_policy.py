"""Validate a small Python extraction language before any execution.

This module is also loaded by the isolated stdlib-only worker. Generated code has
no imports, host objects, filesystem, network, introspection, or arbitrary calls.
"""

import ast

BUILTINS = ('str', 'int', 'float', 'bool', 'len', 'list', 'dict', 'tuple', 'set',
            'sorted', 'enumerate', 'range', 'zip', 'min', 'max', 'sum', 'any', 'all',
            'abs', 'round', 'isinstance')
HELPERS = ('parse_html', 'json_loads', 're_findall', 're_sub', 're_escape', 'resolve_url')
METHODS = {'get', 'items', 'keys', 'values', 'update', 'setdefault', 'append', 'extend',
           'strip', 'lstrip', 'rstrip', 'split', 'rsplit', 'splitlines', 'join',
           'lower', 'upper', 'replace', 'startswith', 'endswith', 'find', 'rfind',
           'count', 'removeprefix', 'removesuffix', 'isdigit', 'isalnum', 'index',
           'copy', 'add'}
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
    functions = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    if 'extract' not in functions or any(not isinstance(n, ast.FunctionDef) for n in tree.body):
        raise ValueError('Script must contain only functions, including extract(files, assets)')
    allowed_calls = set(BUILTINS) | set(HELPERS) | functions
    for node in ast.walk(tree):
        if type(node).__name__ not in NODES:
            raise ValueError('Unsupported Python syntax: ' + type(node).__name__)
        if isinstance(node, (ast.Name, ast.arg, ast.FunctionDef)):
            name = node.id if isinstance(node, ast.Name) else node.arg if isinstance(node, ast.arg) else node.name
            if name.startswith('_'):
                raise ValueError('Private names are unavailable')
        if isinstance(node, ast.FunctionDef) and (node.decorator_list or node.returns or node.type_params):
            raise ValueError('Decorators, annotations and type parameters are unavailable')
        if isinstance(node, ast.arg) and node.annotation:
            raise ValueError('Type annotations are unavailable')
        if isinstance(node, ast.Attribute):
            if node.attr not in METHODS or not isinstance(node.ctx, ast.Load):
                raise ValueError('Unavailable method: ' + node.attr)
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id not in allowed_calls:
                    raise ValueError('Unavailable function: ' + node.func.id)
            elif not isinstance(node.func, ast.Attribute):
                raise ValueError('Indirect calls are unavailable')
    entry = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'extract')
    if ([a.arg for a in entry.args.args] != ['files', 'assets'] or entry.args.posonlyargs
            or entry.args.kwonlyargs or entry.args.vararg or entry.args.kwarg or entry.args.defaults):
        raise ValueError('Entry point must be def extract(files, assets)')
    return tree
