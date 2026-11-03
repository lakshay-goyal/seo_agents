"""Prompt-cache breakpoints and structured-output (JSON schema) binding for chat models."""

from __future__ import annotations

from seo_agents.lib.models import CACHEABLE_PREFIXES


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
