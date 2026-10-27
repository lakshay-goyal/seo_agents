"""Bounded, allowlisted interpretation of React syntax; no JavaScript execution.

Tree-sitter builds the syntax tree. Only literal data, pure transformations,
component composition and local public-file fetches are interpreted. Browser and
network side effects are never executed. Unsupported values become coverage gaps.
"""

from dataclasses import dataclass
import html
import json
import re
from pathlib import Path
from urllib.parse import quote

from tree_sitter import Language, Parser
import tree_sitter_javascript
import tree_sitter_typescript


class Unknown:
    def __bool__(self):
        return False


UNKNOWN = Unknown()


def text(node):
    return node.text.decode('utf-8') if node else ''


def field(node, name):
    return node.child_by_field_name(name) if node else None


def walk(node):
    yield node
    for child in node.named_children:
        yield from walk(child)


@dataclass
class Function:
    node: object
    env: dict
    module: object


@dataclass
class Element:
    tag: str
    attrs: dict
    children: list
    file: str
    line: int
    key: str
    bindings: list
    state: str = 'initial'


@dataclass
class Setter:
    env: dict
    name: str


@dataclass
class Response:
    value: object


@dataclass
class Promise:
    value: object


class Module:
    def __init__(self, engine, path):
        self.engine, self.path = engine, path
        data = path.read_bytes()
        language = tree_sitter_typescript.language_tsx() if path.suffix in {'.tsx', '.ts'} else tree_sitter_javascript.language()
        self.root = Parser(Language(language)).parse(data).root_node
        self.env = {'window': {'location': {'pathname': engine.url}}, '__bindings': [], '__instance': ''}
        self.default = None
        self.exports = {}
        if self.root.has_error:
            engine.gap(path, self.root, 'syntax_parse_error')
        # Register functions first so same-file component references can resolve.
        for outer in self.root.named_children:
            node = outer.named_children[-1] if outer.type == 'export_statement' and outer.named_children else outer
            if node.type in {'function_declaration', 'generator_function_declaration'}:
                name = text(field(node, 'name'))
                self.env[name] = Function(node, self.env, self)
                if outer.type == 'export_statement':
                    self.exports[name] = self.env[name]
                    if 'default' in text(outer).split('function', 1)[0]:
                        self.default = self.env[name]
        for node in self.root.named_children:
            if node.type == 'import_statement':
                self.imports(node)
            elif node.type in {'lexical_declaration', 'variable_declaration'}:
                engine.declare(node, self.env, self)
            elif node.type == 'export_statement':
                for child in node.named_children:
                    if child.type in {'lexical_declaration', 'variable_declaration'}:
                        engine.declare(child, self.env, self)
                        for decl in child.named_children:
                            name = text(field(decl, 'name'))
                            self.exports[name] = self.env.get(name, UNKNOWN)
                    elif child.type == 'identifier' and 'default' in text(node):
                        self.default = self.env.get(text(child), UNKNOWN)
                    elif child.type in {'arrow_function', 'function_expression'}:
                        self.default = Function(child, self.env, self)
        if self.default is None:
            self.default = self.env.get('App')
        if self.default is None:
            for call in walk(self.root):
                if call.type == 'call_expression' and text(field(call, 'function')).endswith('.render'):
                    args = field(call, 'arguments')
                    for jsx in walk(args):
                        opening = field(jsx, 'open_tag') or jsx
                        name = text(field(opening, 'name')) if jsx.type.startswith('jsx_') else ''
                        if isinstance(self.env.get(name), Function):
                            self.default = self.env[name]
                            break

    def imports(self, node):
        source = field(node, 'source')
        name = self.engine.evaluate(source, self.env, self)
        if not isinstance(name, str):
            return
        target = self.engine.resolve(self.path, name)
        clause = next((c for c in node.named_children if c.type == 'import_clause'), None)
        if not clause:
            return
        for child in clause.named_children:
            if child.type == 'identifier':
                local, imported = text(child), 'default'
                self.bind_import(local, imported, target, name)
            elif child.type == 'named_imports':
                for spec in child.named_children:
                    original = text(field(spec, 'name'))
                    local = text(field(spec, 'alias')) or original
                    self.bind_import(local, original, target, name)

    def bind_import(self, local, imported, target, package):
        if not target:
            # Only recognized framework primitives have built-in semantics.
            self.env[local] = ('builtin', imported if imported != 'default' else package)
            return
        if target.suffix == '.json':
            self.env[local] = json.loads(target.read_text())
            self.env.setdefault('__origins', {})[local] = str(target.relative_to(self.engine.repo))
        elif target.suffix in {'.jsx', '.tsx', '.js', '.ts'}:
            module = self.engine.module(target)
            if module is None:
                self.engine.gap(self.path, self.root, 'cyclic_module_import')
                self.env[local] = UNKNOWN
            else:
                self.env[local] = module.default if imported == 'default' else module.exports.get(imported, UNKNOWN)
        else:
            self.env[local] = '/' + str(target.relative_to(self.engine.repo))


class Engine:
    def __init__(self, repo, url='/'):
        self.repo, self.url = Path(repo).resolve(), url
        self.modules, self.gaps, self.interactions = {}, [], []
        self.calls = 0
        self.state_overrides = {}
        self.state = 'initial'
        self.depth = 0
        self.dependencies = []
        self.current_data_source = None

    def gap(self, path, node, reason):
        record = {'file': str(path.relative_to(self.repo)), 'line': node.start_point.row + 1 if node else 1, 'reason': reason}
        if record not in self.gaps:
            self.gaps.append(record)

    def resolve(self, path, name):
        if name.startswith('.'):
            base = path.parent / name
        elif name.startswith('@/'):
            base = (self.repo / 'src' if (self.repo / 'src').is_dir() else self.repo) / name[2:]
        else:
            return None
        candidates = [base] + [Path(str(base) + ext) for ext in ('.tsx', '.jsx', '.ts', '.js', '.json')] + [base / ('index' + ext) for ext in ('.tsx', '.jsx', '.ts', '.js')]
        return next((p.resolve() for p in candidates if p.is_file() and p.resolve().is_relative_to(self.repo)), None)

    def module(self, path):
        path = path.resolve()
        if path in self.modules:
            return self.modules[path]
        self.modules[path] = None
        module = Module(self, path)
        self.modules[path] = module
        return module

    def bind(self, pattern, value, env, module):
        if not pattern:
            return
        if pattern.type in {'identifier', 'shorthand_property_identifier_pattern'}:
            env[text(pattern)] = value
        elif pattern.type in {'assignment_pattern', 'object_assignment_pattern'}:
            left = field(pattern, 'left') or pattern.named_children[0]
            right = field(pattern, 'right') or pattern.named_children[-1]
            self.bind(left, self.evaluate(right, env, module) if isinstance(value, Unknown) else value, env, module)
        elif pattern.type == 'array_pattern':
            for index, child in enumerate(pattern.named_children):
                self.bind(child, value[index] if isinstance(value, (list, tuple)) and index < len(value) else UNKNOWN, env, module)
        elif pattern.type == 'object_pattern':
            for child in pattern.named_children:
                if child.type == 'pair_pattern':
                    key = text(field(child, 'key'))
                    self.bind(field(child, 'value'), value.get(key, UNKNOWN) if isinstance(value, dict) else UNKNOWN, env, module)
                else:
                    key = text(child.named_children[0]) if child.type == 'object_assignment_pattern' else text(child)
                    self.bind(child, value.get(key, UNKNOWN) if isinstance(value, dict) else UNKNOWN, env, module)

    def declare(self, node, env, module):
        for decl in node.named_children:
            if decl.type != 'variable_declarator':
                continue
            pattern, value_node = field(decl, 'name'), field(decl, 'value')
            # State setters are modeled, never called as real JavaScript.
            if value_node and value_node.type == 'call_expression' and text(field(value_node, 'function')) == 'useState' and pattern.type == 'array_pattern':
                names = pattern.named_children
                name = text(names[0])
                args = field(value_node, 'arguments').named_children
                initial = self.evaluate(args[0], env, module) if args else UNKNOWN
                if isinstance(initial, Function):
                    initial = UNKNOWN
                env[name] = self.state_overrides.get(name, initial)
                if len(names) > 1:
                    env[text(names[1])] = Setter(env, name)
            else:
                self.bind(pattern, self.evaluate(value_node, env, module), env, module)

    def invoke(self, function, args, component=False):
        if not isinstance(function, Function):
            return UNKNOWN
        self.depth += 1
        if self.depth > 80:
            self.depth -= 1
            self.gap(function.module.path, function.node, 'component_or_function_depth_limit')
            return UNKNOWN
        try:
            env = dict(function.env)
            params = field(function.node, 'parameters')
            params = params.named_children if params else [field(function.node, 'parameter')]
            for index, pattern in enumerate(params):
                self.bind(pattern, args[index] if index < len(args) else UNKNOWN, env, function.module)
            body = field(function.node, 'body')
            if body.type != 'statement_block':
                return self.evaluate(body, env, function.module)
            for statement in body.named_children:
                if statement.type in {'lexical_declaration', 'variable_declaration'}:
                    self.declare(statement, env, function.module)
                elif statement.type == 'return_statement':
                    return self.evaluate(statement.named_children[0], env, function.module) if statement.named_children else None
                elif statement.type == 'if_statement':
                    condition = self.evaluate(field(statement, 'condition'), env, function.module)
                    if not isinstance(condition, Unknown):
                        branch = field(statement, 'consequence') if condition else field(statement, 'alternative')
                        if branch:
                            statements = branch.named_children if branch.type == 'statement_block' else [branch]
                            for st in statements:
                                if st.type == 'return_statement':
                                    return self.evaluate(st.named_children[0], env, function.module) if st.named_children else None
                elif statement.type == 'expression_statement':
                    call = statement.named_children[0]
                    # Interpret only local fetch effects; ignore DOM effects and event listeners.
                    if text(field(call, 'function')) == 'useEffect':
                        if any(n.type == 'call_expression' and text(field(n, 'function')) == 'fetch' for n in walk(call)):
                            args_node = field(call, 'arguments')
                            self.invoke(self.evaluate(args_node.named_children[0], env, function.module), [])
                    else:
                        self.evaluate(call, env, function.module)
            return None
        finally:
            self.depth -= 1

    def evaluate(self, node, env, module):
        if node is None:
            return UNKNOWN
        self.calls += 1
        if self.calls > 200000:
            self.gap(module.path, node, 'expression_budget_exceeded')
            return UNKNOWN
        kind = node.type
        children = node.named_children
        if kind in {'parenthesized_expression', 'jsx_expression', 'as_expression', 'non_null_expression'}:
            value = self.evaluate(children[0], env, module) if children else None
            if kind == 'jsx_expression' and isinstance(value, Unknown):
                self.gap(module.path, node, 'unresolved_rendered_expression')
            return value
        if kind in {'string', 'string_fragment'}:
            raw = text(node)
            if kind == 'string_fragment':
                return raw
            try:
                return json.loads(raw) if raw.startswith('"') else bytes(raw[1:-1], 'utf-8').decode('unicode_escape') if '\\' in raw else raw[1:-1]
            except (ValueError, UnicodeError):
                return raw[1:-1]
        if kind == 'number':
            return float(text(node)) if '.' in text(node) else int(text(node))
        if kind in {'true', 'false', 'null', 'undefined'}:
            return {'true': True, 'false': False, 'null': None, 'undefined': None}[kind]
        if kind in {'identifier', 'shorthand_property_identifier'}:
            return env.get(text(node), UNKNOWN)
        if kind == 'template_string':
            result = []
            for child in children:
                value = self.evaluate(child.named_children[0], env, module) if child.type == 'template_substitution' else text(child)
                if isinstance(value, Unknown):
                    return UNKNOWN
                result.append(str(value))
            return ''.join(result)
        if kind == 'array':
            return [self.evaluate(c, env, module) for c in children]
        if kind == 'object':
            value = {}
            for child in children:
                if child.type == 'pair':
                    key = field(child, 'key')
                    value[text(key).strip('"\'')] = self.evaluate(field(child, 'value'), env, module)
                elif child.type == 'shorthand_property_identifier':
                    value[text(child)] = env.get(text(child), UNKNOWN)
                elif child.type == 'spread_element':
                    part = self.evaluate(child.named_children[0], env, module)
                    if isinstance(part, dict):
                        value.update(part)
            return value
        if kind in {'arrow_function', 'function_expression'}:
            return Function(node, env, module)
        if kind in {'member_expression', 'subscript_expression'}:
            obj = self.evaluate(field(node, 'object'), env, module)
            key_node = field(node, 'property') or field(node, 'index')
            key = text(key_node) if kind == 'member_expression' else self.evaluate(key_node, env, module)
            if isinstance(obj, dict):
                return obj.get(key, UNKNOWN)
            if isinstance(obj, Response):
                return True if key == 'ok' else UNKNOWN
            if isinstance(obj, (list, str)) and key == 'length':
                return len(obj)
            if isinstance(obj, (list, str)) and isinstance(key, int):
                return obj[key] if -len(obj) <= key < len(obj) else UNKNOWN
            return UNKNOWN
        if kind in {'binary_expression', 'ternary_expression'}:
            if kind == 'ternary_expression':
                condition = self.evaluate(field(node, 'condition'), env, module)
                if isinstance(condition, Unknown):
                    self.gap(module.path, node, 'unresolved_condition')
                    return UNKNOWN
                return self.evaluate(field(node, 'consequence') if condition else field(node, 'alternative'), env, module)
            left_node, right_node = field(node, 'left'), field(node, 'right')
            left = self.evaluate(left_node, env, module)
            op = text(field(node, 'operator'))
            if op == '&&':
                return self.evaluate(right_node, env, module) if left else left
            if op in {'||', '??'}:
                return left if (left is not None and not isinstance(left, Unknown) if op == '??' else bool(left)) else self.evaluate(right_node, env, module)
            right = self.evaluate(right_node, env, module)
            if isinstance(left, Unknown) or isinstance(right, Unknown):
                return UNKNOWN
            try:
                return {'===': lambda: left == right, '==': lambda: left == right, '!==': lambda: left != right, '!=': lambda: left != right, '+': lambda: left + right, '>': lambda: left > right, '<': lambda: left < right}.get(op, lambda: UNKNOWN)()
            except (TypeError, ValueError):
                return UNKNOWN
        if kind == 'unary_expression':
            value = self.evaluate(field(node, 'argument'), env, module)
            return not bool(value) if text(node).startswith('!') else UNKNOWN
        if kind == 'call_expression':
            return self.call(node, env, module)
        if kind in {'jsx_element', 'jsx_self_closing_element'}:
            return self.jsx(node, env, module)
        if kind == 'jsx_text':
            return html.unescape(re.sub(r'\s+', ' ', text(node)))
        if kind in {'comment', 'regex'}:
            return UNKNOWN
        self.gap(module.path, node, 'unsupported_expression:' + kind)
        return UNKNOWN

    def call(self, node, env, module):
        fn = field(node, 'function')
        args_node = field(node, 'arguments')
        args = [self.evaluate(n, env, module) for n in args_node.named_children] if args_node else []
        name = text(fn)
        if name in {'String', 'encodeURIComponent'}:
            return (str(args[0]) if name == 'String' else quote(str(args[0]), safe='')) if args and not isinstance(args[0], Unknown) else UNKNOWN
        if name == 'createPortal':
            return args[0] if args else None
        if name == 'fetch' and args and isinstance(args[0], str):
            path = (self.repo / 'public' / args[0].lstrip('/')).resolve()
            if path.is_relative_to(self.repo / 'public') and path.is_file():
                self.current_data_source = str(path.relative_to(self.repo))
                self.dependencies.append(self.current_data_source)
                return Promise(Response(path.read_text()))
            self.gap(module.path, node, 'remote_or_missing_fetch_not_executed')
            return UNKNOWN
        if fn.type == 'member_expression' and field(fn, 'object').type == 'regex' and text(field(fn, 'property')) == 'test':
            if text(field(fn, 'object')) == r'/^\d+\. /m' and args and isinstance(args[0], str):
                return bool(re.search(r'^\d+\. ', args[0], re.M))
            return UNKNOWN
        if name == 'Object.entries' and args and isinstance(args[0], dict):
            return list(map(list, args[0].items()))
        if fn.type == 'member_expression':
            obj = self.evaluate(field(fn, 'object'), env, module)
            method = text(field(fn, 'property'))
            if isinstance(obj, Promise):
                if method == 'then' and args:
                    result = self.invoke(args[0], [obj.value]) if isinstance(args[0], Function) else self.apply_setter(args[0], obj.value)
                    return Promise(result)
                if method == 'catch':
                    return obj
            if isinstance(obj, Response) and method in {'json', 'text'}:
                try:
                    return json.loads(obj.value) if method == 'json' else obj.value
                except ValueError:
                    return UNKNOWN
            if method in {'map', 'flatMap'} and isinstance(obj, list) and args and isinstance(args[0], Function):
                values = []
                callback = args[0]
                if len(obj) > 10000:
                    self.gap(module.path, node, 'list_expansion_limit_10000')
                for index, item in enumerate(obj[:10000]):
                    closure = dict(callback.env)
                    closure['__instance'] = env.get('__instance', '') + f'/{node.start_byte}:{index}'
                    root_name = text(field(fn, 'object')).split('.')[0]
                    origin = env.get('__origins', {}).get(root_name)
                    if origin:
                        closure['__bindings'] = env.get('__bindings', []) + [{'file': origin, 'json_pointer': '/' + str(index)}]
                    value = self.invoke(Function(callback.node, closure, callback.module), [item, index])
                    values.extend(value if method == 'flatMap' and isinstance(value, list) else [value])
                return values
            if isinstance(obj, (str, list)):
                try:
                    if method == 'slice':
                        return obj[slice(args[0] if args else 0, args[1] if len(args) > 1 else None)]
                    if method == 'join' and isinstance(obj, list):
                        return (args[0] if args else ',').join(str(v) for v in obj)
                    if method == 'split' and isinstance(obj, str):
                        arg_node = args_node.named_children[0] if args_node.named_children else None
                        return re.split(r'\n\s*\n', obj) if arg_node and arg_node.type == 'regex' else obj.split(args[0])
                    if method == 'trim' and isinstance(obj, str):
                        return obj.strip()
                    if method == 'startsWith':
                        return obj.startswith(args[0])
                    if method == 'endsWith':
                        return obj.endswith(args[0])
                    if method == 'padStart':
                        return obj.rjust(args[0], args[1] if len(args) > 1 else ' ')
                    if method == 'replace':
                        pattern = args_node.named_children[0]
                        raw = text(pattern)
                        if pattern.type == 'regex':
                            if raw.startswith('/^---'):
                                return re.sub(r'^---\s*\n[\s\S]*?\n---\s*\n', '', obj, count=1)
                            if raw == r'/\/$/':
                                return re.sub(r'/$', '', obj)
                            if raw == r'/^\d+\. /':
                                return re.sub(r'^\d+\. ', '', obj)
                            return UNKNOWN
                        return obj.replace(args[0], args[1], 1)
                except (TypeError, ValueError, IndexError):
                    return UNKNOWN
        function = self.evaluate(fn, env, module)
        if isinstance(function, Function):
            return self.invoke(function, args)
        if isinstance(function, Setter):
            return self.apply_setter(function, args[0] if args else None)
        if name not in {'useRef', 'useEffect', 'useState'}:
            self.gap(module.path, node, 'call_not_allowlisted:' + name[:80])
        return UNKNOWN

    def apply_setter(self, setter, value):
        if isinstance(setter, Setter):
            setter.env[setter.name] = value
            if self.current_data_source:
                setter.env.setdefault('__origins', {})[setter.name] = self.current_data_source
                setter.env['__bindings'] = setter.env.get('__bindings', []) + [{'file': self.current_data_source, 'json_pointer': ''}]
            return None
        return UNKNOWN

    def jsx(self, node, env, module):
        opening = field(node, 'open_tag') or node
        name_node = field(opening, 'name')
        tag = text(name_node)
        primitive = env.get(tag)
        if primitive == ('builtin', 'Route'):
            route_path = next((a for a in opening.named_children if a.type == 'jsx_attribute' and text(a.named_children[0]) == 'path'), None)
            if route_path and len(route_path.named_children) > 1:
                value = self.evaluate(route_path.named_children[1], env, module)
                if isinstance(value, str) and value != self.url:
                    return None
        attrs, unresolved = {}, []
        for attr in opening.named_children:
            if attr.type != 'jsx_attribute':
                continue
            key = text(attr.named_children[0])
            value = self.evaluate(attr.named_children[1], env, module) if len(attr.named_children) > 1 else True
            if isinstance(value, Unknown):
                unresolved.append(key.lower())
            elif not isinstance(value, Function):
                attrs[key] = value
            # Discover state changes from literal click handlers, without executing handlers.
            if key == 'onClick' and isinstance(value, Function):
                for call in walk(value.node):
                    if call.type != 'call_expression':
                        continue
                    setter = self.evaluate(field(call, 'function'), env, module)
                    arguments = field(call, 'arguments')
                    if isinstance(setter, Setter) and arguments and arguments.named_children:
                        new_value = self.evaluate(arguments.named_children[0], env, module)
                        if isinstance(new_value, dict) and env.get('__component'):
                            self.interactions.append((env['__component'], setter.name, new_value))
        children = []
        if node.type == 'jsx_element':
            for child in node.named_children:
                if child.type not in {'jsx_opening_element', 'jsx_closing_element'}:
                    children.append(self.evaluate(child, env, module))
        if not tag:
            return children
        if tag[0].isupper():
            component = env.get(tag, UNKNOWN)
            if isinstance(component, Function):
                props = {**attrs, 'children': children}
                closure = dict(component.env)
                closure.update({'__instance': env.get('__instance', '') + '/' + str(node.start_byte), '__bindings': env.get('__bindings', [])})
                component = Function(component.node, closure, component.module)
                closure['__component'] = (component, props)
                return self.invoke(component, [props], component=True)
            if component == ('builtin', 'next/image'):
                tag = 'Image'
            elif component == ('builtin', 'Route'):
                return attrs.get('element', children)
            elif any(component == ('builtin', name) for name in ('Routes', 'BrowserRouter', 'StrictMode')):
                return children
            elif component == ('builtin', 'next/link'):
                tag = 'a'
            elif component == ('builtin', 'next/head') or tag == 'Head':
                tag = 'head'
            else:
                self.gap(module.path, node, 'unresolved_component:' + tag)
                return children
        attrs = {key.lower(): value for key, value in attrs.items()}
        if unresolved:
            attrs['unresolved_attributes'] = unresolved
            self.gap(module.path, node, 'unresolved_attributes:' + ','.join(unresolved))
        return Element(tag, attrs, children, str(module.path.relative_to(self.repo)), node.start_point.row + 1,
                       str(module.path.relative_to(self.repo)) + ':' + str(node.start_byte) + env.get('__instance', ''), env.get('__bindings', []), self.state)


def flatten(value):
    if isinstance(value, list):
        for item in value:
            yield from flatten(item)
    elif value is not None and not isinstance(value, (Unknown, bool)):
        yield value


def plain(value):
    parts = []
    for item in flatten(value):
        if isinstance(item, Element):
            if item.tag not in {'svg', 'script', 'style', 'head'} and item.attrs.get('aria-hidden') not in {'true', True}:
                parts.append(plain(item.children))
        elif isinstance(item, (str, int, float)):
            parts.append(str(item))
    return re.sub(r'\s+', ' ', ' '.join(parts)).strip()


def collect(value):
    """Project the composed source tree into the category-independent inventory."""
    from types import SimpleNamespace
    result = SimpleNamespace(file='composed-react-tree', blocks=[], links=[], media=[], headings=[], metadata=[], landmarks={}, sections=[])

    def visit(value, section='body', section_id=None, heading=None):
        for element in flatten(value):
            if not isinstance(element, Element):
                continue
            tag, attrs = element.tag.lower(), element.attrs
            if attrs.get('hidden') in {True, 'true'} or attrs.get('aria-hidden') in {True, 'true'}:
                continue
            if tag in {'svg', 'script', 'style'}:
                continue
            parent_section_id = section_id
            if tag in {'main', 'article', 'section', 'nav', 'header', 'footer', 'aside'}:
                section = tag
                section_id = element.key
                result.landmarks[tag] = result.landmarks.get(tag, 0) + 1
                result.sections.append({'parent_section_id': parent_section_id, 'source_id': element.key, 'tag': tag, 'id': attrs.get('id'), 'labelledby': attrs.get('aria-labelledby'), 'label': attrs.get('aria-label')})
            evidence = {'source_id': element.key, 'file': element.file, 'line': element.line,
                        'section': section, 'section_id': section_id, 'ui_state': element.state,
                        'data_bindings': element.bindings}
            body = plain(element.children)
            if re.fullmatch(r'h[1-6]', tag):
                result.headings.append({**evidence, 'level': int(tag[1]), 'text': body, 'id': attrs.get('id')})
            if tag in {'a', 'link'} and attrs.get('href') and isinstance(attrs['href'], str) and tag == 'a':
                image_labels = [v.attrs.get('alt', '') for v in flatten(element.children) if isinstance(v, Element) and v.tag in {'img', 'Image'}]
                result.links.append({**evidence, 'href': attrs['href'], 'anchor_text': body, 'accessible_name': attrs.get('aria-label') or body or ' '.join(image_labels), 'rel': attrs.get('rel'), 'target': attrs.get('target')})
            if tag in {'img', 'image', 'video', 'audio', 'source', 'track', 'iframe'}:
                result.media.append({**evidence, 'kind': 'next-image' if element.tag == 'Image' else tag, **{k: str(v).lower() if isinstance(v, bool) else str(v) if isinstance(v, (int, float)) else v for k, v in attrs.items()}})
            if tag in {'meta', 'link'}:
                result.metadata.append({**evidence, **attrs})
            if tag == 'title':
                result.metadata.append({**evidence, 'title': body})
            if tag in {'head', 'meta', 'link', 'title'}:
                continue
            if tag in {'input', 'button', 'textarea', 'select'}:
                for key in ('placeholder', 'aria-label', 'title'):
                    if isinstance(attrs.get(key), str):
                        result.blocks.append({**evidence, 'tag': tag, 'text': attrs[key], 'text_source': key})
            for child in flatten(element.children):
                if isinstance(child, (str, int, float)) and str(child).strip():
                    result.blocks.append({**evidence, 'tag': tag, 'text': re.sub(r'\s+', ' ', str(child)).strip(), 'context_text': body})
                elif isinstance(child, Element):
                    visit(child, section, section_id, heading)
    visit(value)
    headings_by_id = {h['id']: h['text'] for h in result.headings if h.get('id')}
    sections_by_id = {s['source_id']: s for s in result.sections}
    for kind in ('blocks', 'links', 'headings', 'media'):
        for item in getattr(result, kind):
            source = sections_by_id.get(item.get('section_id'))
            visited = set()
            while source and source['source_id'] not in visited:
                visited.add(source['source_id'])
                label = headings_by_id.get(source.get('labelledby')) or source.get('label')
                if label:
                    item['section_heading'] = label
                    break
                source = sections_by_id.get(source.get('parent_section_id'))
    return result


def app_entry(repo):
    """Follow Vite's module script instead of mistaking the mount shell for a page."""
    index = repo / 'index.html'
    if index.is_file():
        content = index.read_text(errors='replace')
        script = re.search(r'<script\b[^>]*\bsrc=["\']([^"\']+\.(?:jsx?|tsx?))["\']', content)
        if script:
            path = (repo / script[1].lstrip('/')).resolve()
            if path.is_file() and path.is_relative_to(repo):
                return path
    for name in ('src/App.tsx', 'src/App.jsx', 'App.tsx', 'App.jsx'):
        if (repo / name).is_file():
            return repo / name
    return None


def discover_app(repo):
    repo = Path(repo).resolve()
    entry = app_entry(repo)
    if not entry:
        return None
    engine = Engine(repo)
    main = engine.module(entry)
    if not isinstance(main.default, Function):
        if not (repo / 'index.html').is_file():
            return None
        raise ValueError('Unresolved React mount entry: ' + str(entry.relative_to(repo)) + '; refusing an HTML-shell-only report.')
    urls, patterns = {'/'}, set()
    dispatch_names = {'window.location.pathname'}
    for module in engine.modules.values():
        if module:
            for declaration in walk(module.root):
                if declaration.type == 'variable_declarator' and 'window.location.pathname' in text(field(declaration, 'value')):
                    dispatch_names.add(text(field(declaration, 'name')))
    for module in list(engine.modules.values()):
        if module is None:
            continue
        for node in walk(module.root):
            if node.type in {'jsx_opening_element', 'jsx_self_closing_element'} and text(field(node, 'name')) == 'Route':
                for attr in node.named_children:
                    if attr.type == 'jsx_attribute' and text(attr.named_children[0]) == 'path' and len(attr.named_children) > 1:
                        value = engine.evaluate(attr.named_children[1], module.env, module)
                        if isinstance(value, str) and value.startswith('/') and not any(c in value for c in ':*'):
                            urls.add(value)
                        else:
                            engine.gap(module.path, attr, 'unresolved_dynamic_router_path')
            if node.type == 'binary_expression' and text(field(node, 'operator')) in {'===', '=='}:
                left, right = field(node, 'left'), field(node, 'right')
                if text(left) in dispatch_names and right.type == 'string':
                    value = engine.evaluate(right, module.env, module)
                    if isinstance(value, str) and value.startswith('/'):
                        urls.add(value)
            if node.type == 'call_expression':
                fn = field(node, 'function')
                args = field(node, 'arguments')
                if fn and fn.type == 'member_expression' and text(field(fn, 'object')) in dispatch_names and text(field(fn, 'property')) == 'startsWith' and args.named_children:
                    prefix = engine.evaluate(args.named_children[0], module.env, module)
                    if isinstance(prefix, str):
                        patterns.add(prefix)
                if text(fn) == 'fetch' and args and args.named_children:
                    value = engine.evaluate(args.named_children[0], module.env, module)
                    if isinstance(value, str) and value.startswith('/'):
                        path = (repo / 'public' / value.lstrip('/')).resolve()
                        if path.is_relative_to(repo / 'public') and path.is_file() and path.suffix == '.json':
                            def links(value):
                                if isinstance(value, dict):
                                    for key, v in value.items():
                                        if key in {'link', 'href', 'url'} and isinstance(v, str):
                                            yield v
                                        yield from links(v)
                                elif isinstance(value, list):
                                    for v in value:
                                        yield from links(v)
                            for link in links(json.loads(path.read_text())):
                                if any(link.startswith(prefix) for prefix in patterns):
                                    urls.add(link)
    # Pattern discovery may follow fetch discovery in syntax order; resolve again.
    if patterns:
        for module in list(engine.modules.values()):
            if module:
                for node in walk(module.root):
                    if node.type == 'call_expression' and text(field(node, 'function')) == 'fetch':
                        args = field(node, 'arguments')
                        value = engine.evaluate(args.named_children[0], module.env, module) if args.named_children else UNKNOWN
                        if isinstance(value, str) and value.startswith('/'):
                            path = (repo / 'public' / value.lstrip('/')).resolve()
                            if path.is_relative_to(repo / 'public') and path.is_file() and path.suffix == '.json':
                                for link in re.findall(r'"(?:link|href|url)"\s*:\s*"([^\"]+)"', path.read_text()):
                                    if any(link.startswith(prefix) for prefix in patterns):
                                        urls.add(link)
    return entry, sorted(urls), sorted(patterns)


def application_pages(repo):
    repo = Path(repo).resolve()
    discovery = discover_app(repo)
    if not discovery:
        return None
    entry, urls, patterns = discovery
    results = []
    for url in urls:
        engine = Engine(repo, url)
        module = engine.module(entry)
        tree = engine.invoke(module.default, [{}], component=True)
        parser = collect(tree)
        baseline = {kind: {(x['source_id'], x.get('text'), x.get('src'), x.get('href')) for x in getattr(parser, kind)} for kind in ('blocks', 'headings', 'media', 'links')}
        interactions = list(engine.interactions)
        # Each statically resolved object-valued selection is a separate UI state.
        for index, (component_info, state_name, value) in enumerate(interactions):
            component, props = component_info
            engine.state_overrides = {state_name: value}
            engine.state = f'interaction:{state_name}:{index}'
            variant = collect(engine.invoke(component, [props], component=True))
            for kind in baseline:
                for item in getattr(variant, kind):
                    key = (item['source_id'], item.get('text'), item.get('src'), item.get('href'))
                    if key not in baseline[kind]:
                        getattr(parser, kind).append(item)
        # HTML head applies to every SPA route; its empty body is not a page.
        index = repo / 'index.html'
        if index.is_file():
            from seo_agents.exploration.checks.tools.inventory import PageParser
            head_parser = PageParser('index.html')
            head_parser.feed(index.read_text())
            parser.metadata.extend(head_parser.metadata)
        results.append({'url': url, 'template': str(module.default.module.path.relative_to(repo)), 'is_dynamic': False,
                        'parsers': [parser], 'coverage': {'mode': 'static-source', 'complete': False,
                        'limitations': engine.gaps, 'route_patterns': patterns, 'entrypoint': str(entry.relative_to(repo)),
                        'resolved_interaction_states': len(interactions), 'resolved_local_fetches': sorted(set(engine.dependencies)),
                        'note': 'Composed from allowlisted source semantics. CSS visibility, arbitrary runtime/network data and browser behavior remain unverified.'}})
    return results
