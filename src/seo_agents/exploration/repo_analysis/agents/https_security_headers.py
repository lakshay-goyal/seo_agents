"""HTTPS enforcement, HSTS and mixed-content candidates (the SEO-relevant part); CSP, X-Frame-Options,
Permissions-Policy etc. are reported as optional signals only, never as gaps.

Hosts that add HSTS automatically (Vercel, Netlify, Cloudflare Pages, GitHub Pages, Firebase) suppress the
no-hsts gap; served headers measured by the site model remain the source of truth.
"""

from seo_agents.exploration.repo_analysis.tools.skill_runner import evaluate_gaps, run_skill

HEADERS = ('hsts', 'https-redirect', 'csp', 'x-frame-options', 'x-content-type-options', 'referrer-policy', 'permissions-policy')
OPTIONAL = ('csp', 'x-frame-options', 'x-content-type-options', 'referrer-policy', 'permissions-policy')
AUTO_HSTS_HOSTS = {'vercel.json': 'Vercel', 'netlify.toml': 'Netlify', '_redirects': 'Netlify/Cloudflare Pages',
                   '_headers': 'Netlify/Cloudflare Pages', 'wrangler.toml': 'Cloudflare', 'firebase.json': 'Firebase Hosting',
                   'CNAME': 'GitHub Pages'}


def auto_hsts_host(index):
    for path in index.paths(exclude_tests=True):
        name = path.rsplit('/', 1)[-1]
        if name in AUTO_HSTS_HOSTS:
            return AUTO_HSTS_HOSTS[name]
    return None


def analyze_https_security_headers(index):
    result = run_skill(index, 'https-security-headers')
    result['header_matrix'] = {h: {'declared': bool(result['signals'][h]['count']), 'files': result['signals'][h]['files'],
                                   **({'optional': True} if h in OPTIONAL else {})} for h in HEADERS}
    host = auto_hsts_host(index)
    if host:
        result['hosting_default_hsts'] = host
        return evaluate_gaps(result, ['hsts'])
    return result
