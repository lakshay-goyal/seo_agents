"""Chat-model factory with tiering, per-call usage/cost logging and a JSONL trace.

Tiers (doc §13):
- tier 0 `code`  : no model (parsers, renderers, validators);
- tier 1 `small` : gap items with <=10 candidates, classification, data enumeration;
- tier 2 `mid`   : escalation after two failed small-model validations, one-time
                   adapter generation for an unseen framework;
- tier 3 `large` : offline only (skill/adapter authoring); never in the runtime path.

Environment:
  OPENROUTER_API_KEY, OPENROUTER_BASE_URL
  OPENROUTER_MODEL          default model (legacy extractor agents); also the small tier default
  OPENROUTER_SMALL_MODEL    tier-1 model (default: OPENROUTER_MODEL or deepseek flash)
  OPENROUTER_MID_MODEL      tier-2 model (default: anthropic/claude-sonnet-5.5)
  OPENROUTER_MAX_TOKENS, OPENROUTER_REASONING_EFFORT
  SEO_TRACE                 path of a JSONL trace file (one line per model call)
  SEO_MODEL_PRICES          optional JSON {model: [usd_per_mtok_in, usd_per_mtok_out]}
"""

from __future__ import annotations

import json
import os
import threading
import time

from seo_agents.log import log

DEFAULT_MODEL = 'deepseek/deepseek-v4.1-flash'
DEFAULT_MID_MODEL = 'anthropic/claude-sonnet-5.5'
EFFORTS = {'default', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'}
# Rough public list prices (USD per million tokens, input/output); override with SEO_MODEL_PRICES.
PRICES = {
    'deepseek/deepseek-v4.1-flash': (0.10, 0.40),
    'anthropic/claude-haiku-5.5': (1.0, 5.0),
    'anthropic/claude-sonnet-5.5': (3.0, 15.0),
    'anthropic/claude-opus-5.5': (5.0, 25.0),
}
_LOCK = threading.Lock()
USAGE = {'calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'cached_input_tokens': 0, 'cost_usd': 0.0, 'by_model': {}}


def model_for_tier(tier='default'):
    base = os.getenv('OPENROUTER_MODEL', DEFAULT_MODEL)
    if tier == 'small':
        return os.getenv('OPENROUTER_SMALL_MODEL', base)
    if tier == 'mid':
        return os.getenv('OPENROUTER_MID_MODEL', DEFAULT_MID_MODEL)
    if tier == 'large':
        raise ValueError('Tier 3 (large) models are offline-only; not used in the runtime path')
    return base


def get_chat_model(tier='default', *, max_tokens=None, reasoning_effort=None, label=None, temperature=0):
    api_key = os.getenv('OPENROUTER_API_KEY')
    if not api_key:
        raise RuntimeError('OPENROUTER_API_KEY not set')
    from langchain_openai import ChatOpenAI
    model = model_for_tier(tier)
    base_url = os.getenv('OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1')
    try:
        max_tokens = max_tokens or int(os.getenv('OPENROUTER_MAX_TOKENS', '32768'))
    except ValueError as error:
        raise ValueError('OPENROUTER_MAX_TOKENS must be a positive integer') from error
    if max_tokens < 1:
        raise ValueError('OPENROUTER_MAX_TOKENS must be a positive integer')
    # OpenRouter counts reasoning and visible output against the same budget. Flash can
    # spend the entire cap on thinking even at low effort, leaving no plan. Small-tier
    # gap workers answer short structured questions and never need reasoning.
    default_effort = 'none' if model == DEFAULT_MODEL or tier == 'small' else 'default'
    effort = reasoning_effort or os.getenv('OPENROUTER_REASONING_EFFORT', default_effort)
    if effort not in EFFORTS:
        raise ValueError('Invalid OPENROUTER_REASONING_EFFORT')
    options = {} if effort == 'default' else {'extra_body': {'reasoning': {'effort': effort}}}
    return ChatOpenAI(model=model, api_key=api_key, base_url=base_url, temperature=temperature,
                      timeout=90, max_retries=2, max_tokens=max_tokens,
                      callbacks=[UsageTracker(model, tier, label)], **options)


def price(model):
    custom = os.getenv('SEO_MODEL_PRICES')
    if custom:
        try:
            table = json.loads(custom)
            if model in table:
                return tuple(table[model])
        except (ValueError, TypeError):
            pass
    return PRICES.get(model)


def record_usage(model, tier, label, usage, seconds=None, finish_reason=None):
    """Aggregate usage and append one JSONL trace line (no prompt or completion text)."""
    input_tokens = int(usage.get('input_tokens') or 0)
    output_tokens = int(usage.get('output_tokens') or 0)
    cached = int((usage.get('input_token_details') or {}).get('cache_read') or 0)
    rate = price(model)
    cost = ((input_tokens - cached) * rate[0] + cached * rate[0] * 0.1 + output_tokens * rate[1]) / 1e6 if rate else None
    with _LOCK:
        USAGE['calls'] += 1
        USAGE['input_tokens'] += input_tokens
        USAGE['output_tokens'] += output_tokens
        USAGE['cached_input_tokens'] += cached
        if cost:
            USAGE['cost_usd'] = round(USAGE['cost_usd'] + cost, 6)
        row = USAGE['by_model'].setdefault(model, {'calls': 0, 'input_tokens': 0, 'output_tokens': 0})
        row['calls'] += 1
        row['input_tokens'] += input_tokens
        row['output_tokens'] += output_tokens
    line = {'ts': round(time.time(), 3), 'model': model, 'tier': tier, 'label': label, 'input_tokens': input_tokens,
            'output_tokens': output_tokens, 'cached_input_tokens': cached,
            'cache_hit_rate': round(cached / input_tokens, 3) if input_tokens else None,
            'cost_usd': round(cost, 6) if cost is not None else None, 'seconds': seconds, 'finish_reason': finish_reason}
    log('model call: ' + json.dumps({k: v for k, v in line.items() if k != 'ts'}))
    path = os.getenv('SEO_TRACE')
    if path:
        with _LOCK, open(path, 'a', encoding='utf-8') as stream:
            stream.write(json.dumps(line) + '\n')
    return line


def usage_summary():
    with _LOCK:
        summary = json.loads(json.dumps(USAGE))
    summary['cache_hit_rate'] = round(summary['cached_input_tokens'] / summary['input_tokens'], 3) if summary['input_tokens'] else None
    return summary


try:
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError:  # pragma: no cover
    BaseCallbackHandler = object


class UsageTracker(BaseCallbackHandler):
    """LangChain callback: per-call tokens, cache reads, cost and latency."""

    def __init__(self, model, tier, label):
        self.model, self.tier, self.label = model, tier, label
        self.started = {}

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self.started[run_id] = time.perf_counter()

    def on_llm_end(self, response, *, run_id, **kwargs):
        seconds = round(time.perf_counter() - self.started.pop(run_id, time.perf_counter()), 3)
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, 'message', None)
                usage = getattr(message, 'usage_metadata', None) or {}
                finish = (getattr(message, 'response_metadata', None) or {}).get('finish_reason')
                record_usage(self.model, self.tier, self.label, usage, seconds, finish)
