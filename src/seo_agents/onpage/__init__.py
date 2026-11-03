"""On-page SEO stage: reads the exploration JSON (seo_reports/) and writes current-vs-expected proposals
to seo_reports/onpage/. Never reads or edits client source."""

from seo_agents.onpage.graph import build_graph, run_onpage

__all__ = ['build_graph', 'run_onpage']
