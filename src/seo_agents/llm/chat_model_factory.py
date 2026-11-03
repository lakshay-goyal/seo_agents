"""Build a LangChain chat model for a tier (OpenRouter, OpenAI-compatible) with usage tracking attached.

Environment:
  OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MAX_TOKENS
  OPENROUTER_MODEL / OPENROUTER_<TIER>_MODEL          model per tier (defaults in lib/models.py)
  OPENROUTER_REASONING_EFFORT                         global effort; never forces none/minimal on mid/large/escalation
  OPENROUTER_<TIER>_REASONING_EFFORT                  per-tier effort (SMALL, HAIKU, MID, LARGE, DEFAULT)
"""

from __future__ import annotations

import os

from seo_agents.lib.models import (DEFAULT_MAX_TOKENS, DEFAULT_TIMEOUT_SECONDS, DEFAULT_TRANSPORT_RETRIES,
                                   OPENROUTER_BASE_URL)
from seo_agents.llm.model_tiers import model_for_tier, reasoning_effort_for
from seo_agents.llm.usage_tracking import UsageTracker


class ChatModelFactory:
    """`ChatModelFactory().create('mid', label='onpage:page-plan')` -> a ready ChatOpenAI client."""

    def create(self, tier='default', *, max_tokens=None, reasoning_effort=None, label=None, temperature=0,
               escalation=False, max_retries=DEFAULT_TRANSPORT_RETRIES, timeout=DEFAULT_TIMEOUT_SECONDS):
        api_key = os.getenv('OPENROUTER_API_KEY')
        if not api_key:
            raise RuntimeError('OPENROUTER_API_KEY not set')
        from langchain_openai import ChatOpenAI
        model = model_for_tier(tier)
        base_url = os.getenv('OPENROUTER_BASE_URL', OPENROUTER_BASE_URL)
        try:
            max_tokens = max_tokens or int(os.getenv('OPENROUTER_MAX_TOKENS', str(DEFAULT_MAX_TOKENS)))
        except ValueError as error:
            raise ValueError('OPENROUTER_MAX_TOKENS must be a positive integer') from error
        if max_tokens < 1:
            raise ValueError('OPENROUTER_MAX_TOKENS must be a positive integer')
        effort = reasoning_effort_for(tier, model, explicit=reasoning_effort, escalation=escalation)
        options = {} if effort == 'default' else {'extra_body': {'reasoning': {'effort': effort}}}
        return ChatOpenAI(model=model, api_key=api_key, base_url=base_url, temperature=temperature,
                          timeout=timeout, max_retries=max_retries, max_tokens=max_tokens,
                          callbacks=[UsageTracker(model, tier, label)], **options)


def get_chat_model(tier='default', *, max_tokens=None, reasoning_effort=None, label=None, temperature=0, escalation=False,
                   max_retries=DEFAULT_TRANSPORT_RETRIES, timeout=DEFAULT_TIMEOUT_SECONDS):
    """Function form of `ChatModelFactory().create(...)`."""
    return ChatModelFactory().create(tier, max_tokens=max_tokens, reasoning_effort=reasoning_effort, label=label,
                                     temperature=temperature, escalation=escalation, max_retries=max_retries,
                                     timeout=timeout)
