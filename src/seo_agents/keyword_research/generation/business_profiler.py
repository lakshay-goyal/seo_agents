"""Business profile (once per site) from the exploration-derived site packet. Grounded in the packet only."""

from __future__ import annotations

from seo_agents.keyword_research.contracts import BusinessProfile, Market
from seo_agents.keyword_research.generation.candidate_validation import validate_business_profile
from seo_agents.keyword_research.generation.llm_writer import KeywordWriter

SKILL = 'business-profile'


class BusinessProfiler:
    """profile(site_packet, market) -> BusinessProfile (model: AGENT_MODELS['keyword_research.business-profile'])."""

    def __init__(self, writer: KeywordWriter | None = None):
        self.writer = writer or KeywordWriter()
        self.last_meta: dict = {}

    def profile(self, site_packet: dict, market: Market | None = None) -> BusinessProfile:
        market = market or Market()
        packet = {'site': site_packet,
                  'market': {'country': market.country, 'language': market.language_name}}
        profile, self.last_meta = self.writer.write(
            SKILL, packet, lambda answer: validate_business_profile(answer, site_packet, market))
        return profile
