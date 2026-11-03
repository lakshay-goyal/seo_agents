"""Organization/Person entity, sameAs, social profiles, authorship and brand consistency.

Entity types come from the structured-data agent (single @type source). Share/intent URLs and
repository file links are never social profiles; `sameAs` arrays are parsed in full.
"""

import re

from seo_agents.repo_analysis.tools.signal_profiles.signal_profile_runner import records_of, run_signal_profile
from seo_agents.repo_analysis.agents.base_agent import RepoAnalysisAgent

ENTITY_TYPES = {'Organization', 'Person', 'WebSite', 'Corporation', 'Brand', 'ProfilePage', 'NewsMediaOrganization',
                'LocalBusiness', 'OnlineStore', 'EducationalOrganization'}
NOT_A_PROFILE = re.compile(r'/(?:intent|share|sharer|shareArticle|sharing)\b|/(?:blob|tree|issues|pull|commit|releases|raw|edit)/'
                           r'|[?&](?:text|url|u)=', re.I)
SAME_AS_BLOCK = re.compile(r'''['"]?sameAs['"]?\s*:\s*(\[[^\]]*\]|['"`][^'"`]+['"`])''', re.S)


def normalise_profile(url):
    return re.sub(r'^https?://(?:www\.)?', 'https://', url.rstrip('/.').split('?', 1)[0]).lower()


def analyze_brand_entity_signals(index, structured=None):
    result = run_signal_profile(index, 'brand-entity-signals')
    profiles = {r['matched_text'].rstrip('/.') for r in records_of(result, 'social-profile')
                if not NOT_A_PROFILE.search(r['matched_text'])}
    result['social_profiles'] = sorted(profiles)
    if structured is not None:
        types = set(structured.get('schema_types') or {})
        result['entity_types'] = sorted(types & ENTITY_TYPES)
        result['entity_types_source'] = 'structured_data'
    else:
        result['entity_types'] = sorted({r['value'] for r in records_of(result, 'entity-schema') if r.get('value')})
    same_as = set()
    for record in records_of(result, 'same-as'):
        path = record['location']['file']
        text = index.files.get(path, record.get('code', ''))
        lines = index.lines(path) if path in index.files else text.splitlines()
        start = sum(len(line) + 1 for line in lines[:record['location']['line_start'] - 1])
        match = SAME_AS_BLOCK.search(text, start)
        if match:
            same_as.update(u.rstrip('/') for u in re.findall(r'https?://[^\s"\'`,\]]+', match.group(1)))
    for block in (structured or {}).get('json_ld_blocks', []):
        stack = [block.get('value')]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                raw = node.get('sameAs')
                same_as.update(u.rstrip('/') for u in ([raw] if isinstance(raw, str) else raw or []) if isinstance(u, str))
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    result['same_as_urls'] = sorted(same_as)
    normalised = {normalise_profile(s) for s in same_as}
    result['profiles_missing_from_same_as'] = sorted(p for p in result['social_profiles']
                                                     if same_as and normalise_profile(p) not in normalised)
    return result


class BrandEntitySignalsAgent(RepoAnalysisAgent):
    """Agent: Organization/Person entity, sameAs, social profiles, authorship and brand consistency."""

    name = 'brand_entity_signals'
    description = 'Organization/Person entity, sameAs, social profiles, authorship and brand consistency.'
    signal_profile = 'brand-entity-signals'
    requires = ('structured_data',)

    def run(self, index, results):
        return analyze_brand_entity_signals(index, results['structured_data'])
