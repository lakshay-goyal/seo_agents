"""Per-call token usage, cache reads/writes and cost: process-wide tally plus an optional JSONL trace (SEO_TRACE).

Environment:
  SEO_TRACE           path of a JSONL trace file (one line per model call; no prompt or completion text)
  SEO_MODEL_PRICES    optional JSON {model: [usd_in, usd_out(, usd_cache_read_multiplier)]} per Mtok
"""

from __future__ import annotations

from contextlib import contextmanager
import copy
import json
import os
import threading
import time

from seo_agents.lib.models import CACHE_MULTIPLIERS, PRICES
from seo_agents.utils.step_logger import log

_LOCK = threading.Lock()


def _empty_usage():
    return {'calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'cached_input_tokens': 0, 'cache_write_tokens': 0,
            'cost_usd': 0.0, 'by_model': {}}


USAGE = _empty_usage()


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


def cache_multipliers(model, rate=None):
    if rate and len(rate) >= 3:
        return float(rate[2]), (float(rate[3]) if len(rate) >= 4 else 1.0)
    for prefix, multipliers in CACHE_MULTIPLIERS.items():
        if str(model or '').startswith(prefix):
            return multipliers
    return 1.0, 1.0


def call_cost(model, input_tokens, output_tokens, cached=0, written=0):
    rate = price(model)
    if not rate:
        return None
    read_x, write_x = cache_multipliers(model, rate)
    plain = max(0, input_tokens - cached - written)
    return (plain * rate[0] + cached * rate[0] * read_x + written * rate[0] * write_x + output_tokens * rate[1]) / 1e6


def record_usage(model, tier, label, usage, seconds=None, finish_reason=None):
    """Aggregate usage and append one JSONL trace line (no prompt or completion text)."""
    input_tokens = int(usage.get('input_tokens') or 0)
    output_tokens = int(usage.get('output_tokens') or 0)
    details = usage.get('input_token_details') or {}
    cached = int(details.get('cache_read') or 0)
    written = int(details.get('cache_creation') or details.get('cache_write') or 0)
    cost = call_cost(model, input_tokens, output_tokens, cached, written)
    with _LOCK:
        USAGE['calls'] += 1
        USAGE['input_tokens'] += input_tokens
        USAGE['output_tokens'] += output_tokens
        USAGE['cached_input_tokens'] += cached
        USAGE['cache_write_tokens'] += written
        if cost:
            USAGE['cost_usd'] = round(USAGE['cost_usd'] + cost, 6)
        row = USAGE['by_model'].setdefault(model, {'calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'cost_usd': 0.0})
        row['calls'] += 1
        row['input_tokens'] += input_tokens
        row['output_tokens'] += output_tokens
        if cost:
            row['cost_usd'] = round(row.get('cost_usd', 0.0) + cost, 6)
    line = {'ts': round(time.time(), 3), 'model': model, 'tier': tier, 'label': label, 'input_tokens': input_tokens,
            'output_tokens': output_tokens, 'cached_input_tokens': cached, 'cache_write_tokens': written,
            'cache_hit_rate': round(cached / input_tokens, 3) if input_tokens else None,
            'cost_usd': round(cost, 6) if cost is not None else None, 'seconds': seconds, 'finish_reason': finish_reason}
    log('model call: ' + json.dumps({k: v for k, v in line.items() if k != 'ts'}))
    path = os.getenv('SEO_TRACE')
    if path:
        with _LOCK, open(path, 'a', encoding='utf-8') as stream:
            stream.write(json.dumps(line) + '\n')
    return line


def _summarise(usage):
    summary = copy.deepcopy(usage)
    summary['cost_usd'] = round(summary['cost_usd'], 6)
    summary['cache_hit_rate'] = round(summary['cached_input_tokens'] / summary['input_tokens'], 3) if summary['input_tokens'] else None
    return summary


def usage_summary():
    with _LOCK:
        return _summarise(USAGE)


def reset_usage():
    """Start a fresh process-wide tally (call at the start of each run)."""
    with _LOCK:
        USAGE.clear()
        USAGE.update(_empty_usage())


def usage_since(before):
    """Usage recorded after `before` (a usage_summary() snapshot): per-run scoping without a reset."""
    now = usage_summary()
    delta = _empty_usage()
    for key in ('calls', 'input_tokens', 'output_tokens', 'cached_input_tokens', 'cache_write_tokens'):
        delta[key] = now.get(key, 0) - (before or {}).get(key, 0)
    delta['cost_usd'] = round(now['cost_usd'] - (before or {}).get('cost_usd', 0.0), 6)
    for model, row in now['by_model'].items():
        old = ((before or {}).get('by_model') or {}).get(model, {})
        diff = {k: round(row.get(k, 0) - old.get(k, 0), 6) for k in ('calls', 'input_tokens', 'output_tokens', 'cost_usd')}
        if diff['calls']:
            delta['by_model'][model] = diff
    return _summarise(delta)


@contextmanager
def usage_scope():
    """`with usage_scope() as scope: ...; scope['usage']` -> usage for this block only."""
    holder = {'before': usage_summary(), 'usage': None}
    try:
        yield holder
    finally:
        holder['usage'] = usage_since(holder['before'])


try:
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError:  # pragma: no cover
    BaseCallbackHandler = object


class UsageTracker(BaseCallbackHandler):
    """LangChain callback: per-call tokens, cache reads/writes, cost and latency."""

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
