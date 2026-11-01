"""JSON-LD/microdata locations, parsed literal JSON-LD blocks and Schema.org types."""

from collections import Counter
import json
import re

from seo_agents.exploration.repo_analysis.tools.skill_runner import records_of, run_skill

JSON_LD = re.compile(r'<script\b[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)


def analyze_structured_data(index):
    result = run_skill(index, 'structured-data')
    blocks = []
    for path in index.templates():
        for match in JSON_LD.finditer(index.files[path]):
            body = match.group(1).strip()
            entry = {'raw': body[:5000]}
            try:
                entry.update(parse_status='valid-json', value=json.loads(body))
            except ValueError as error:
                entry.update(parse_status='dynamic-or-invalid', detail=str(error)[:200])
            blocks.append(index.evidence('json-ld-block', path, match.start(), match.end(), **entry))
    result['json_ld_blocks'] = blocks
    types = Counter(r['value'] for r in records_of(result, 'schema-type', 'microdata') if r.get('value'))
    result['schema_types'] = dict(types.most_common())
    result['validation_scope'] = 'JSON syntax only; Schema.org/Rich Results eligibility not verified.'
    return result
