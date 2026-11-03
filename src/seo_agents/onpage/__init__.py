"""On-page SEO stage: reads the exploration JSON (seo_reports/) and writes current-vs-expected proposals
to seo_reports/onpage/. Never reads or edits client source.

Entry point: `onpage_analysis.OnpageAnalysisWorkflow` / `run_onpage()` in onpage_analysis.py.
"""

from seo_agents.onpage.onpage_analysis import OnpageAnalysisWorkflow, build_graph, run_onpage

__all__ = ['OnpageAnalysisWorkflow', 'build_graph', 'run_onpage']
