"""Which model every agent uses. Change a value here to move an agent to a cheaper or stronger model.

Tiers (the runtime picks the model of a tier, an environment variable can override it):

    default : OPENROUTER_MODEL          (legacy extractor agents; also the small tier when OPENROUTER_SMALL_MODEL is unset)
    small   : OPENROUTER_SMALL_MODEL    closed-set choices over a small packet
    haiku   : OPENROUTER_HAIKU_MODEL    short multi-hop reading (first escalation of some gap workers)
    mid     : OPENROUTER_MID_MODEL      long coupled output (on-page page plan) and escalation after two validation failures
    large   : OPENROUTER_LARGE_MODEL    offline only (skill authoring, eval labelling); refused unless SEO_ALLOW_LARGE=1

`AGENT_MODELS` assigns a starting tier (and optional escalation ladder, temperature, self-consistency votes) to every
agent that calls a model. Agents without an entry here never call a model (they are deterministic code).
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------- tier -> model
DEFAULT_MODEL = 'deepseek/deepseek-v4.1-flash'
DEFAULT_HAIKU_MODEL = 'anthropic/claude-haiku-5.5'
DEFAULT_MID_MODEL = 'anthropic/claude-sonnet-5.5'
DEFAULT_LARGE_MODEL = 'anthropic/claude-opus-5.5'

TIERS = ('small', 'haiku', 'mid', 'large')
# Environment variable that overrides each tier's model, and the model used when it is unset
# (None = fall back to OPENROUTER_MODEL / DEFAULT_MODEL).
TIER_MODEL_ENV = {
    'small': ('OPENROUTER_SMALL_MODEL', None),
    'haiku': ('OPENROUTER_HAIKU_MODEL', DEFAULT_HAIKU_MODEL),
    'mid': ('OPENROUTER_MID_MODEL', DEFAULT_MID_MODEL),
    'large': ('OPENROUTER_LARGE_MODEL', DEFAULT_LARGE_MODEL),
}

OPENROUTER_BASE_URL = 'https://openrouter.ai/api/v1'
DEFAULT_MAX_TOKENS = 32768           # OPENROUTER_MAX_TOKENS overrides
DEFAULT_TIMEOUT_SECONDS = 90
DEFAULT_TRANSPORT_RETRIES = 2

# ---------------------------------------------------------------- reasoning effort
REASONING_EFFORTS = {'default', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'}
WEAK_REASONING_EFFORTS = {'none', 'minimal'}  # never applied to mid/large tiers or escalations

# ---------------------------------------------------------------- pricing (cost accounting only)
# Rough public list prices (USD per million tokens, input/output); override with SEO_MODEL_PRICES.
PRICES = {
    'deepseek/deepseek-v4.1-flash': (0.10, 0.40),
    'anthropic/claude-haiku-5.5': (1.0, 5.0),
    'anthropic/claude-sonnet-5.5': (3.0, 15.0),
    'anthropic/claude-opus-5.5': (5.0, 25.0),
}
# Provider cache pricing as multipliers of the input price: (read, write).
CACHE_MULTIPLIERS = {'anthropic/': (0.1, 1.25), 'openai/': (0.5, 1.0), 'google/': (0.25, 1.0), 'deepseek/': (0.1, 1.0)}
CACHEABLE_PREFIXES = ('anthropic/', 'google/')  # providers that need explicit cache_control breakpoints


# ---------------------------------------------------------------- per-agent model settings
@dataclass(frozen=True)
class AgentModelSettings:
    """Model settings for one agent.

    tier         starting tier ('default', 'small', 'haiku', 'mid')
    ladder       tiers per validation attempt, e.g. ('small', 'haiku', 'mid'); None = (tier, tier, 'mid')
    temperature  sampling temperature (None = the caller's default, 0)
    votes        self-consistency votes on enum fields (gap-worker judgment tasks)
    """

    tier: str
    ladder: tuple | None = None
    temperature: float | None = None
    votes: int | None = None

    def as_skill_meta(self):
        """The same keys a SKILL.md frontmatter used to declare (`tier`, `ladder`, `temperature`, `votes`) as strings."""
        meta = {'tier': self.tier}
        if self.ladder:
            meta['ladder'] = ','.join(self.ladder)
        if self.temperature is not None:
            meta['temperature'] = str(self.temperature)
        if self.votes is not None:
            meta['votes'] = str(self.votes)
        return meta


AGENT_MODELS = {
    # Legacy LLM category extractors (repo_analysis/agents/legacy_llm_extractors): one agent per category.
    'legacy_extractor': AgentModelSettings(tier='default'),

    # Gap workers (repo_analysis/agents/gap_workers): one validated small-model task per attribution gap.
    'gap_worker.attribute-value': AgentModelSettings(tier='small'),
    'gap_worker.explain-computed': AgentModelSettings(tier='small', ladder=('small', 'haiku', 'mid')),
    'gap_worker.topic-intent': AgentModelSettings(tier='small', votes=3),
    'gap_worker.eeat-classify': AgentModelSettings(tier='small', votes=3),
    'gap_worker.enumerate-instances': AgentModelSettings(tier='small'),
    'gap_worker.route-adapter': AgentModelSettings(tier='small', ladder=('small', 'haiku', 'mid')),

    # On-page content writer (onpage/checks): the four checklist items that call a model.
    'onpage.search-intent': AgentModelSettings(tier='small', temperature=0),
    'onpage.page-plan': AgentModelSettings(tier='mid', temperature=0.3),
    'onpage.media-links': AgentModelSettings(tier='small', temperature=0),
    'onpage.eeat-aeo-advice': AgentModelSettings(tier='small', temperature=0),

    # Keyword research generator (keyword_research/generation): business profile once per site, ideation per page.
    'keyword_research.business-profile': AgentModelSettings(tier='small', ladder=('small', 'haiku', 'mid'), temperature=0),
    'keyword_research.keyword-ideation': AgentModelSettings(tier='mid', temperature=0.4),
}

# Output-token caps
GAP_WORKER_MAX_TOKENS_BY_TIER = {'small': 1500}  # every other tier: GAP_WORKER_MAX_TOKENS
GAP_WORKER_MAX_TOKENS = 6000
ONPAGE_WRITER_MAX_TOKENS = 16000                  # SEO_ONPAGE_MAX_TOKENS overrides (reasoning + output)
ONPAGE_MID_REASONING_EFFORT = 'low'               # SEO_ONPAGE_REASONING_EFFORT overrides; mid endpoints reject 'none'
KEYWORD_WRITER_MAX_TOKENS = 12000                 # SEO_KEYWORD_MAX_TOKENS overrides (reasoning + output)
KEYWORD_MID_REASONING_EFFORT = 'low'              # SEO_KEYWORD_REASONING_EFFORT overrides; mid endpoints reject 'none'


def agent_model(agent):
    """AgentModelSettings for an agent key such as 'gap_worker.topic-intent' or 'onpage.page-plan' (None if unknown)."""
    return AGENT_MODELS.get(agent)


def skill_model_meta(agent):
    """Frontmatter-style model keys for an agent ({} when the agent calls no model)."""
    settings = AGENT_MODELS.get(agent)
    return settings.as_skill_meta() if settings else {}


def gap_worker_max_tokens(tier):
    return GAP_WORKER_MAX_TOKENS_BY_TIER.get(tier, GAP_WORKER_MAX_TOKENS)
