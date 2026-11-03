"""NAP (name/address/phone), LocalBusiness schema, maps, hours and location pages.

Gated: unless a LocalBusiness-family schema, a postal address or geo coordinates exist, the
agent reports `status: not-applicable` and drops its noise (phone-like numbers, map libraries).
"""

import re

from seo_agents.repo_analysis.tools.signal_profiles.signal_profile_runner import records_of, run_signal_profile
from seo_agents.repo_analysis.agents.base_agent import RepoAnalysisAgent

LOCAL_TYPES = {'LocalBusiness', 'Restaurant', 'Store', 'MedicalBusiness', 'ProfessionalService', 'LegalService', 'Dentist',
               'AutoRepair', 'HomeAndConstructionBusiness', 'FinancialService', 'HealthAndBeautyBusiness',
               'LodgingBusiness', 'FoodEstablishment', 'CafeOrCoffeeShop', 'Bakery', 'BarOrPub', 'Hotel'}
GATE_SIGNALS = ('local-schema', 'postal-address', 'geo')


def _value(record):
    if record.get('value'):
        return record['value'].strip()
    text = record['matched_text'].strip()
    match = re.search(r'''telephone['"]\s*:\s*['"]([^'"]+)''', text)
    return (match.group(1) if match else re.sub(r'^tel:', '', text)).strip()


def analyze_local_business_presence(index, structured=None):
    result = run_signal_profile(index, 'local-business-presence')
    schema_local = sorted(set((structured or {}).get('schema_types') or {}) & LOCAL_TYPES)
    applicable = bool(schema_local) or any(result['signals'].get(s, {}).get('count') for s in GATE_SIGNALS)
    result['local_schema_types'] = schema_local
    if not applicable:
        result['status'] = 'not-applicable'
        result['note'] = 'No LocalBusiness schema, postal address or geo coordinates; local signals are not evaluated.'
        result['gaps'] = []
        for signal in result['signals'].values():
            signal.update(records=[], count=0, files=[])
        result['phone_values'], result['email_values'] = [], []
        return result
    result['status'] = 'applicable'
    result['phone_values'] = sorted({_value(r) for r in records_of(result, 'phone') if not r['matched_text'].strip().endswith(':')})
    emails = set()
    for record in records_of(result, 'email'):
        match = re.search(r'[\w.+-]+@[\w-]+\.[\w.]+', record['matched_text'])
        if match:
            emails.add(match.group(0).rstrip('.'))
    result['email_values'] = sorted(emails)
    result['nap_consistency_note'] = ('Multiple distinct phone values found; verify they are intentional.'
                                      if len(result['phone_values']) > 1 else None)
    return result


class LocalBusinessPresenceAgent(RepoAnalysisAgent):
    """Agent: NAP (name/address/phone), LocalBusiness schema, maps, hours and location pages."""

    name = 'local_business_presence'
    description = 'NAP (name/address/phone), LocalBusiness schema, maps, hours and location pages.'
    signal_profile = 'local-business-presence'
    requires = ('structured_data',)

    def run(self, index, results):
        return analyze_local_business_presence(index, results['structured_data'])
