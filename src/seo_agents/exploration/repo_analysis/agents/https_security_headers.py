"""HTTPS enforcement, security headers matrix and mixed-content candidates."""

from seo_agents.exploration.repo_analysis.tools.skill_runner import run_skill

HEADERS = ('hsts', 'https-redirect', 'csp', 'x-frame-options', 'x-content-type-options', 'referrer-policy', 'permissions-policy')


def analyze_https_security_headers(index):
    result = run_skill(index, 'https-security-headers')
    result['header_matrix'] = {h: {'declared': bool(result['signals'][h]['count']), 'files': result['signals'][h]['files']}
                               for h in HEADERS}
    return result
