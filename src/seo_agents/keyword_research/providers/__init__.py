"""Deterministic third-party keyword data (DataForSEO, Google Ads, Search Console, Google Trends, autocomplete).

The LLM never invents metrics: every number in KeywordEvidence comes from here, with provenance.
"""

from seo_agents.keyword_research.providers.http_cache import HttpTransport, JsonCache, ProviderError
from seo_agents.keyword_research.providers.research_toolkit import ProviderResearchToolkit

__all__ = ['HttpTransport', 'JsonCache', 'ProviderError', 'ProviderResearchToolkit']
