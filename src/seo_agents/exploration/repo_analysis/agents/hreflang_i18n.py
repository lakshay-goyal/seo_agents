"""Locale routing, i18n libraries, hreflang alternates and <html lang>."""

from seo_agents.exploration.repo_analysis.tools.skill_runner import records_of, run_skill


def analyze_hreflang_i18n(index):
    result = run_skill(index, 'hreflang-i18n')
    result['html_lang_values'] = sorted({r['value'] for r in records_of(result, 'html-lang') if r.get('value')})
    return result
