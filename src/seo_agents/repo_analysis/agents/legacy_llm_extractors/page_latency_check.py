"""HTTP baseline entry point; source inspection cannot measure a page load."""

from seo_agents.repo_analysis.tools.http.runtime_observation import observe_runtime


def measure_page_latency(bundle=None, *, site_url=None, max_pages=100, timeout=12):
    if bundle is None:
        return {'status': 'unavailable', 'reason': 'No enumerated concrete URL inventory supplied'}
    observe_runtime(bundle, site_url=site_url, max_pages=max_pages, timeout=timeout)
    return bundle['runtime']


class PageLatencyCheck:
    """Check: HTTP baseline (status, headers, time to headers) for the evidence bundle's concrete URLs."""

    name = 'page_latency'

    def measure(self, bundle=None, *, site_url=None, max_pages=100, timeout=12):
        return measure_page_latency(bundle, site_url=site_url, max_pages=max_pages, timeout=timeout)
