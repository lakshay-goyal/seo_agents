"""HTTP baseline entry point; source inspection cannot measure a page load."""

from seo_agents.exploration.checks.technical.runtime import observe_runtime


def measure_page_latency(bundle=None, *, site_url=None, max_pages=100, timeout=12):
    if bundle is None:
        return {'status': 'unavailable', 'reason': 'No enumerated concrete URL inventory supplied'}
    observe_runtime(bundle, site_url=site_url, max_pages=max_pages, timeout=timeout)
    return bundle['runtime']
