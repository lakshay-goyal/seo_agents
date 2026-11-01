"""Organization/Person entity, sameAs, social profiles, authorship and brand consistency."""

import re

from seo_agents.exploration.repo_analysis.tools.skill_runner import records_of, run_skill


def analyze_brand_entity_signals(index):
    result = run_skill(index, 'brand-entity-signals')
    result['social_profiles'] = sorted({r['matched_text'].rstrip('/.') for r in records_of(result, 'social-profile')})
    result['entity_types'] = sorted({r['value'] for r in records_of(result, 'entity-schema') if r.get('value')})
    same_as = set()
    for record in records_of(result, 'same-as'):
        same_as.update(re.findall(r'https?://[^\s"\'`,\]]+', record['code']))
    result['same_as_urls'] = sorted(same_as)
    result['profiles_missing_from_same_as'] = sorted(p for p in result['social_profiles']
                                                     if same_as and not any(p.rstrip('/') == s.rstrip('/') for s in same_as))
    return result
