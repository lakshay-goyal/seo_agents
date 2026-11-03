"""JSON-LD/microdata locations, parsed JSON-LD blocks and Schema.org types (the single @type source).

Handles literal `<script type="application/ld+json">` bodies, JSX/Astro
`dangerouslySetInnerHTML={{__html: JSON.stringify(obj)}}` / `set:html={JSON.stringify(obj)}` (the object
literal is resolved in the same file), next-seo `<*JsonLd>` components, Nuxt `useSchemaOrg` and Liquid
`| json`. Only top-level types (incl. `@graph` members) count as `schema_types`; nested ones are listed
separately. Brand, local-business and AI-readiness agents reuse this output instead of re-running regexes.
"""

from collections import Counter
import json
import re

from seo_agents.repo_analysis.tools.signal_profiles.signal_profile_runner import records_of, run_signal_profile
from seo_agents.repo_analysis.agents.base_agent import RepoAnalysisAgent

# A self-closing tag has no body; a body never spans another <script>.
JSON_LD = re.compile(r'<script\b(?=[^>]*application/ld\+json)([^>]*?)(/?)>((?:(?!<script\b|</script).)*)(?:</script\s*>)?',
                     re.S | re.I)
STRINGIFY = re.compile(r'(?:__html\s*:|set:html\s*=\s*\{|\{)\s*JSON\.stringify\s*\(\s*')
HELPER = re.compile(r'<(\w*JsonLd)\b|\buseSchemaOrg\s*\(|\bdefine(Organization|Person|WebSite|WebPage|Article|Product|'
                    r'BreadcrumbList|LocalBusiness|Question|FAQPage)\s*\(')
TOP_LEVEL_TYPE = re.compile(r'''['"]?@type['"]?\s*:\s*(?:['"`](\w+)['"`]|\[([^\]]*)\])''')


def js_object_to_json(source):
    """Best-effort JS object/array literal -> Python value; None when it contains expressions."""
    out, i, n = [], 0, len(source)
    while i < n:
        char = source[i]
        if char in '\'"`':
            quote, j, buf = char, i + 1, []
            while j < n and source[j] != quote:
                if source[j] == '\\' and j + 1 < n:
                    buf.append(source[j:j + 2])
                    j += 2
                    continue
                if quote == '`' and source.startswith('${', j):
                    return None
                buf.append(source[j])
                j += 1
            text = ''.join(buf)
            if quote != '"':
                text = text.replace('\\\'', "'").replace('"', '\\"')
            out.append('"' + text.replace('\n', '\\n') + '"')
            i = j + 1
        elif source.startswith('//', i):
            i = source.find('\n', i) if source.find('\n', i) >= 0 else n
        elif source.startswith('/*', i):
            end = source.find('*/', i + 2)
            i = n if end < 0 else end + 2
        elif char.isalpha() or char in '_$@':
            j = i
            while j < n and (source[j].isalnum() or source[j] in '_$@.'):
                j += 1
            word = source[i:j]
            rest = source[j:].lstrip()
            if rest.startswith(':'):
                out.append(json.dumps(word))
            elif word in {'true', 'false', 'null'}:
                out.append(word)
            else:
                return None  # identifier/expression value
            i = j
        elif char == ',':
            rest = source[i + 1:].lstrip()
            if not rest.startswith(('}', ']')):
                out.append(',')
            i += 1
        else:
            out.append(char)
            i += 1
    try:
        return json.loads(''.join(out))
    except ValueError:
        return None


def balanced(text, start):
    """Text of the balanced {...} / [...] literal starting at `start` (string-aware)."""
    opener = text[start]
    closer = {'{': '}', '[': ']'}.get(opener)
    if not closer:
        return None
    depth, i, quote = 0, start, None
    while i < len(text):
        char = text[i]
        if quote:
            if char == '\\':
                i += 2
                continue
            if char == quote:
                quote = None
        elif char in '\'"`':
            quote = char
        elif char in '{[':
            depth += 1
        elif char in '}]':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
        i += 1
    return None


def resolve_identifier(text, name, before):
    """Object literal assigned to `name` in the same file (const/let/var/export const)."""
    pattern = re.compile(r'\b(?:const|let|var)\s+' + re.escape(name) + r'\b\s*(?::[^=]{0,80})?=\s*([\[{])')
    matches = list(pattern.finditer(text))
    if not matches:
        return None, None
    match = ([m for m in matches if m.start() < before] or matches)[-1]
    return balanced(text, match.start(1)), match.start(1)


def top_level_types(value):
    """(@type values of top-level nodes incl. @graph members, nested @type values)."""
    top, nested = [], []

    def as_list(raw):
        return [raw] if isinstance(raw, str) else [t for t in raw or [] if isinstance(t, str)] if isinstance(raw, list) else []

    def walk(node, depth):
        if isinstance(node, list):
            for item in node:
                walk(item, depth)
        elif isinstance(node, dict):
            (top if depth == 0 else nested).extend(as_list(node.get('@type')))
            for key, child in node.items():
                if key == '@graph':
                    walk(child, depth)
                elif key != '@type':
                    walk(child, depth + 1)

    walk(value, 0)
    return top, nested


def literal_types(source):
    """Top-level @type values in an unparsable (dynamic) object literal: depth-1 keys only."""
    found, depth = [], 0
    i = 0
    while i < len(source):
        char = source[i]
        if char in '{[':
            depth += 1
        elif char in '}]':
            depth -= 1
        elif depth == 1 or (depth == 2 and source.lstrip().startswith('[')):
            match = TOP_LEVEL_TYPE.match(source, i)
            if match:
                found += [match.group(1)] if match.group(1) else re.findall(r'''['"`](\w+)['"`]''', match.group(2))
                i = match.end()
                continue
        i += 1
    return found


def _block(index, path, start, end, entry):
    return index.evidence('json-ld-block', path, start, end, **entry)


def json_ld_blocks(index):
    blocks = []
    for path in index.templates():
        text = index.files[path]
        for match in JSON_LD.finditer(text):
            attrs, self_closing, body = match.group(1), match.group(2), (match.group(3) or '').strip()
            stringify = STRINGIFY.search(attrs + (body if not self_closing else ''))
            entry = {}
            if stringify:
                origin = attrs + body
                arg_start = stringify.end()
                rest = origin[arg_start:]
                if rest[:1] in '{[':
                    literal = balanced(origin, arg_start)
                    entry['source'] = 'inline-object'
                else:
                    name = re.match(r'[\w$.]+', rest)
                    literal, _ = resolve_identifier(text, name.group(0).split('.')[0], match.start()) if name else (None, None)
                    entry['source'] = 'identifier:' + (name.group(0) if name else '?')
                value = js_object_to_json(literal) if literal else None
                if value is not None:
                    entry.update(parse_status='resolved-object', value=value)
                else:
                    entry.update(parse_status='dynamic', raw=(literal or rest)[:2000], literal_types=literal_types(literal or ''))
            elif self_closing:
                continue
            elif re.search(r'\{\{|\{%|<\?|<%=|\$\{', body):
                entry.update(parse_status='template', raw=body[:2000], literal_types=literal_types(body),
                             source='liquid-json' if re.search(r'\|\s*json\b', body) else 'template')
            else:
                try:
                    entry.update(parse_status='valid-json', value=json.loads(body))
                except ValueError as error:
                    value = js_object_to_json(body)
                    if value is not None:
                        entry.update(parse_status='valid-json-relaxed', value=value)
                    else:
                        entry.update(parse_status='invalid', raw=body[:2000], detail=str(error)[:200])
            if 'value' in entry:
                entry['top_level_types'], entry['nested_types'] = top_level_types(entry['value'])
            else:
                entry['top_level_types'], entry['nested_types'] = entry.get('literal_types', []), []
            blocks.append(_block(index, path, match.start(), match.end(), entry))
    return blocks


def helper_blocks(index):
    """next-seo components, Nuxt useSchemaOrg/define*() helpers: component name implies the type."""
    found = []
    for path in index.templates():
        text = index.files[path]
        for match in HELPER.finditer(text):
            if match.group(1):
                name = match.group(1)[:-len('JsonLd')] or 'Unknown'
                types = [{'Article': 'Article', 'Product': 'Product', 'FAQPage': 'FAQPage', 'Breadcrumb': 'BreadcrumbList',
                          'Organization': 'Organization', 'LocalBusiness': 'LocalBusiness', 'Logo': 'Organization',
                          'SocialProfile': 'Person', 'Event': 'Event', 'Recipe': 'Recipe', 'Course': 'Course',
                          'SiteLinksSearchBox': 'WebSite', 'Video': 'VideoObject', 'Profile': 'ProfilePage'}.get(name, name)]
            elif match.group(2):
                types = [match.group(2)]
            else:
                start = text.find('(', match.start()) + 1
                literal = balanced(text, start) if text[start:start + 1] in '[{' else None
                types = [t for call in re.findall(r'\bdefine(\w+)\s*\(', literal or '') for t in [call]]
            found.append(index.evidence('json-ld-helper-call', path, match.start(), match.end(),
                                        top_level_types=types, parse_status='helper'))
    return found


def schema_types_of(result):
    """Counter of top-level schema types from a structured_data result (for other agents)."""
    return Counter(t for block in result.get('json_ld_blocks', []) + result.get('helper_blocks', [])
                   for t in block.get('top_level_types', []))


def analyze_structured_data(index):
    result = run_signal_profile(index, 'structured-data')
    blocks = json_ld_blocks(index)
    helpers = helper_blocks(index)
    result['json_ld_blocks'] = blocks
    result['helper_blocks'] = helpers
    types = Counter(t for b in blocks + helpers for t in b['top_level_types'])
    types.update(r['value'] for r in records_of(result, 'microdata') if r.get('value'))
    result['schema_types'] = dict(types.most_common())
    result['nested_schema_types'] = dict(Counter(t for b in blocks for t in b['nested_types']).most_common())
    result['validation_scope'] = 'JSON syntax and top-level @type only; Schema.org/Rich Results eligibility not verified.'
    return result


class StructuredDataAgent(RepoAnalysisAgent):
    """Agent: JSON-LD/microdata locations, parsed JSON-LD blocks and Schema.org types (the single @type source)."""

    name = 'structured_data'
    description = 'JSON-LD/microdata locations, parsed JSON-LD blocks and Schema.org types (the single @type source).'
    signal_profile = 'structured-data'
    requires = ()

    def run(self, index, results):
        return analyze_structured_data(index)
