"""NAP (name/address/phone), LocalBusiness schema, maps, hours and location pages."""

import re

from seo_agents.exploration.repo_analysis.tools.skill_runner import records_of, run_skill


def analyze_local_business_presence(index):
    result = run_skill(index, 'local-business-presence')
    result['phone_values'] = sorted({re.sub(r'^tel:', '', r['matched_text']).strip() for r in records_of(result, 'phone')
                                     if not r['matched_text'].strip().endswith(':')})
    result['email_values'] = sorted({re.sub(r'^mailto:', '', r['matched_text']) for r in records_of(result, 'email')
                                     if r['matched_text'].startswith('mailto:')})
    result['nap_consistency_note'] = ('Multiple distinct phone values found; verify they are intentional.'
                                      if len(result['phone_values']) > 1 else None)
    return result
