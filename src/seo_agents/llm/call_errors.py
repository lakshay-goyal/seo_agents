"""Provider error classification and transport retries (auth errors abort, transport errors back off)."""

from __future__ import annotations

import time


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
