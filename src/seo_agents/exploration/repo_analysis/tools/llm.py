"""Chat-model factory with tiering, prompt caching, structured outputs, error classification
and per-call usage/cost logging with a JSONL trace.

Tiers (audit §14):
- `code`  : no model (parsers, renderers, validators) - the default for every deterministic fact;
- `small` : closed-set choices over a small packet (attribute_value, enumerate, intent label, alts);
- `haiku` : short multi-hop reading (explain_computed, route adapter first escalation);
- `mid`   : long coupled output (on-page page plan) and escalation after two validation failures;
- `large` : offline only (skill/adapter authoring, eval labelling); refused at runtime unless
            SEO_ALLOW_LARGE=1.

Environment:
  OPENROUTER_API_KEY, OPENROUTER_BASE_URL
  OPENROUTER_MODEL          default model (legacy extractor agents); also the small tier default
  OPENROUTER_SMALL_MODEL    small tier (default: OPENROUTER_MODEL or deepseek flash)
  OPENROUTER_HAIKU_MODEL    haiku tier (default: anthropic/claude-haiku-5.5)
  OPENROUTER_MID_MODEL      mid tier (default: anthropic/claude-sonnet-5.5)
  OPENROUTER_LARGE_MODEL    large tier, offline only (default: anthropic/claude-opus-5.5)
  OPENROUTER_MAX_TOKENS
  OPENROUTER_REASONING_EFFORT          global effort; never forces none/minimal on mid/large/escalation
  OPENROUTER_<TIER>_REASONING_EFFORT   per-tier effort (SMALL, HAIKU, MID, LARGE, DEFAULT)
  SEO_TRACE                 path of a JSONL trace file (one line per model call)
  SEO_MODEL_PRICES          optional JSON {model: [usd_in, usd_out(, usd_cache_read_multiplier)]} per Mtok
"""

from __future__ import annotations

from contextlib import contextmanager
import copy
import json
import os
import threading
import time

from seo_agents.log import log

DEFAULT_MODEL = 'deepseek/deepseek-v4.1-flash'
DEFAULT_HAIKU_MODEL = 'anthropic/claude-haiku-5.5'
DEFAULT_MID_MODEL = 'anthropic/claude-sonnet-5.5'
DEFAULT_LARGE_MODEL = 'anthropic/claude-opus-5.5'
TIERS = ('small', 'haiku', 'mid', 'large')
EFFORTS = {'default', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'}
WEAK_EFFORTS = {'none', 'minimal'}
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
_LOCK = threading.Lock()


def _empty_usage():
    return {'calls': 0, 'input_tokens': 0, 'output_tokens': 0, 'cached_input_tokens': 0, 'cache_write_tokens': 0,
            'cost_usd': 0.0, 'by_model': {}}


USAGE = _empty_usage()


# ---------------------------------------------------------------- tiers
def model_for_tier(tier='default', *, allow_large=False):
    base = os.getenv('OPENROUTER_MODEL', DEFAULT_MODEL)
    if tier == 'small':
        return os.getenv('OPENROUTER_SMALL_MODEL', base)
    if tier == 'haiku':
        return os.getenv('OPENROUTER_HAIKU_MODEL', DEFAULT_HAIKU_MODEL)
    if tier == 'mid':
        return os.getenv('OPENROUTER_MID_MODEL', DEFAULT_MID_MODEL)
    if tier == 'large':
        if not (allow_large or os.getenv('SEO_ALLOW_LARGE') == '1'):
            raise ValueError('Tier large models are offline-only; not used in the runtime path (set SEO_ALLOW_LARGE=1 offline)')
        return os.getenv('OPENROUTER_LARGE_MODEL', DEFAULT_LARGE_MODEL)
    return base


def tier_for_skill(meta, default='small'):
    """Tier declared by a skill's `tier:` frontmatter, if it is a runtime tier; else `default`."""
    tier = str((meta or {}).get('tier') or '').strip().lower()
    return tier if tier in TIERS and tier != 'large' else default


def ladder_for_skill(meta, default=('small', 'small', 'mid')):
    """Escalation ladder from `ladder: small,haiku,mid` frontmatter, else `default`.

    A skill that only declares `tier: X` starts at X and still escalates to mid after two failures.
    """
    raw = (meta or {}).get('ladder')
    if raw:
        steps = [s.strip().lower() for s in str(raw).split(',') if s.strip().lower() in TIERS and s.strip().lower() != 'large']
        if steps:
            return steps
    tier = tier_for_skill(meta, default[0])
    if tier == 'mid':
        return ['mid', 'mid']
    return [tier, tier, 'mid']


def reasoning_effort_for(tier, model, *, explicit=None, escalation=False):
    """Explicit > OPENROUTER_<TIER>_REASONING_EFFORT > global (never weakening mid/large/escalation) > default."""
    if explicit:
        effort = explicit
    else:
        per_tier = os.getenv('OPENROUTER_' + (tier if tier in TIERS else 'default').upper() + '_REASONING_EFFORT')
        global_effort = os.getenv('OPENROUTER_REASONING_EFFORT')
        strong = tier in {'mid', 'large'} or escalation
        if per_tier:
            effort = per_tier
        elif global_effort and not (strong and global_effort in WEAK_EFFORTS):
            effort = global_effort
        elif model == DEFAULT_MODEL or tier == 'small':
            # Flash can spend the whole cap on thinking; small-tier answers are short structured choices.
            effort = 'none'
        else:
            effort = 'default'
    if effort not in EFFORTS:
        raise ValueError('Invalid OPENROUTER_REASONING_EFFORT')
    if escalation and (effort in WEAK_EFFORTS or effort == 'default'):
        effort = 'low'  # an escalation must actually reason
    return effort


def get_chat_model(tier='default', *, max_tokens=None, reasoning_effort=None, label=None, temperature=0, escalation=False,
                   max_retries=2, timeout=90):
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
    effort = reasoning_effort_for(tier, model, explicit=reasoning_effort, escalation=escalation)
    options = {} if effort == 'default' else {'extra_body': {'reasoning': {'effort': effort}}}
    return ChatOpenAI(model=model, api_key=api_key, base_url=base_url, temperature=temperature,
                      timeout=timeout, max_retries=max_retries, max_tokens=max_tokens,
                      callbacks=[UsageTracker(model, tier, label)], **options)


# ---------------------------------------------------------------- prompt caching + structured output
def model_name(model):
    if model is None or isinstance(model, str):
        return model
    return getattr(model, 'model_name', None) or getattr(model, 'model', None)


def supports_cache_control(model):
    name = model_name(model)
    return name is None or str(name).startswith(CACHEABLE_PREFIXES)


def cached_system_message(text, model=None):
    """System message whose static text is a prompt-cache breakpoint (OpenRouter -> Anthropic/Gemini).

    Other providers cache automatically by prefix, so they get a plain string (no content blocks).
    """
    from langchain_core.messages import SystemMessage
    if not supports_cache_control(model):
        return SystemMessage(content=text)
    return SystemMessage(content=[{'type': 'text', 'text': text, 'cache_control': {'type': 'ephemeral'}}])


def json_schema_format(schema, name, strict=False):
    return {'type': 'json_schema', 'json_schema': {'name': name, 'strict': strict, 'schema': schema}}


def with_json_schema(model, schema, name, strict=False):
    """Bind an OpenAI-style `response_format` JSON schema; fakes without `bind` are returned unchanged."""
    bind = getattr(model, 'bind', None)
    if bind is None:
        return model
    try:
        bound = bind(response_format=json_schema_format(schema, name, strict))
    except Exception:  # noqa: BLE001 - a client that cannot bind falls back to prompt-only JSON
        return model
    return bound


# ---------------------------------------------------------------- error classification
class ModelCallError(RuntimeError):
    pass


class AuthError(ModelCallError):
    """401/402/403 or exhausted credits: retrying cannot help; abort the run."""


class TransportError(ModelCallError):
    """Timeouts, connection failures, 408/409/429/5xx: retry with backoff, never as a validation retry."""


def _status(exc):
    for attribute in ('status_code', 'status', 'http_status'):
        value = getattr(exc, attribute, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, 'response', None)
    value = getattr(response, 'status_code', None)
    return value if isinstance(value, int) else None


def classify_error(exc):
    """'auth' | 'transport' | None (not a provider failure: treat as an invalid answer)."""
    if isinstance(exc, AuthError):
        return 'auth'
    if isinstance(exc, TransportError):
        return 'transport'
    status = _status(exc)
    if status is None and isinstance(exc, (ValueError, KeyError, TypeError)):
        return None  # parse/validation problems are answer quality, not provider failures
    text = (type(exc).__name__ + ' ' + str(exc)).lower()
    if status in {401, 402, 403} or any(s in text for s in ('insufficient credits', 'invalid api key', 'authenticationerror',
                                                           'permissiondenied', 'no auth credentials', 'api key not set',
                                                           'openrouter_api_key not set')):
        return 'auth'
    if status in {408, 409, 425, 429} or (isinstance(status, int) and status >= 500):
        return 'transport'
    if any(s in text for s in ('timeout', 'timed out', 'connection', 'apiconnectionerror', 'ratelimit', 'rate limit',
                               'temporarily unavailable', 'overloaded', 'remotedisconnected', 'bad gateway')):
        return 'transport'
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return 'transport'
    return None


def call_with_backoff(fn, *, attempts=3, base_delay=1.0, sleep=time.sleep):
    """Run fn(); transport errors retry with exponential backoff, auth errors raise AuthError at once."""
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as error:  # noqa: BLE001 - classified below
            kind = classify_error(error)
            if kind == 'auth':
                raise AuthError(str(error)[:300]) from error
            if kind != 'transport':
                raise
            if attempt == attempts - 1:
                raise TransportError(str(error)[:300]) from error
            sleep(base_delay * (2 ** attempt))
    raise TransportError('unreachable')  # pragma: no cover


# ---------------------------------------------------------------- usage and cost
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
