"""Shared model layer used by every agent that calls a model (gap workers, legacy extractors, on-page writer).

    model_tiers.py         tier -> model name, skill tier/ladder, reasoning effort (values in lib/models.py)
    chat_model_factory.py  ChatModelFactory / get_chat_model(): a ChatOpenAI client with usage tracking
    prompt_caching.py      cache_control system messages, JSON-schema structured output binding
    call_errors.py         auth vs transport error classification, retry with backoff
    usage_tracking.py      per-call tokens / cache / cost tally and JSONL trace (SEO_TRACE)
"""

from seo_agents.lib.models import DEFAULT_MODEL, TIERS
from seo_agents.llm.call_errors import AuthError, ModelCallError, TransportError, call_with_backoff, classify_error
from seo_agents.llm.chat_model_factory import ChatModelFactory, get_chat_model
from seo_agents.llm.model_tiers import ladder_for_skill, model_for_tier, reasoning_effort_for, tier_for_skill
from seo_agents.llm.prompt_caching import (cached_system_message, json_schema_format, model_name,
                                           supports_cache_control, with_json_schema)
from seo_agents.llm.usage_tracking import (UsageTracker, call_cost, record_usage, reset_usage, usage_scope,
                                           usage_since, usage_summary)

__all__ = ['DEFAULT_MODEL', 'TIERS', 'AuthError', 'ModelCallError', 'TransportError', 'call_with_backoff',
           'classify_error', 'ChatModelFactory', 'get_chat_model', 'ladder_for_skill', 'model_for_tier',
           'reasoning_effort_for', 'tier_for_skill', 'cached_system_message', 'json_schema_format', 'model_name',
           'supports_cache_control', 'with_json_schema', 'UsageTracker', 'call_cost', 'record_usage', 'reset_usage',
           'usage_scope', 'usage_since', 'usage_summary']
