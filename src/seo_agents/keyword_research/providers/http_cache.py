"""On-disk JSON cache + retrying HTTP transport shared by every keyword provider.

Cache key = sha256(method, url, params, json payload). Credentials (headers, basic auth) are never part of the key
and never written to disk or logs.

Env:
    SEO_KEYWORD_CACHE           '0' disables the cache (default on)
    SEO_KEYWORD_CACHE_DIR       cache directory (default .cache/keyword_research)
    SEO_KEYWORD_CACHE_TTL_DAYS  entry lifetime in days (default 14)
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Callable

import httpx

from seo_agents.utils.step_logger import log

RETRY_STATUS = {429, 500, 502, 503, 504}


class ProviderError(RuntimeError):
    """A provider call failed. The message is safe to log (no credentials)."""


def env_flag(name, default=True):
    raw = os.getenv(name)
    if raw is None or raw.strip() == '':
        return default
    return raw.strip().lower() not in {'0', 'false', 'no', 'off'}


def env_number(name, default, cast=float):
    raw = os.getenv(name)
    if raw is None or raw.strip() == '':
        return default
    try:
        return cast(raw)
    except ValueError:
        log('keyword providers: ignoring invalid ' + name + '=' + repr(raw))
        return default


class JsonCache:
    def __init__(self, directory: str | Path, ttl_days: float = 14, enabled: bool = True):
        self.directory = Path(directory)
        self.ttl_seconds = ttl_days * 86400
        self.enabled = enabled
        self.hits = 0
        self.misses = 0

    @classmethod
    def from_env(cls):
        return cls(os.getenv('SEO_KEYWORD_CACHE_DIR') or '.cache/keyword_research',
                   env_number('SEO_KEYWORD_CACHE_TTL_DAYS', 14.0),
                   env_flag('SEO_KEYWORD_CACHE', True))

    @staticmethod
    def key(method: str, url: str, params: Any = None, payload: Any = None) -> str:
        blob = json.dumps([method.upper(), url, params, payload], sort_keys=True, default=str)
        return hashlib.sha256(blob.encode('utf-8')).hexdigest()

    def _path(self, key):
        return self.directory / key[:2] / (key + '.json')

    def get(self, key: str):
        if not self.enabled:
            return None
        path = self._path(key)
        try:
            entry = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.misses += 1
            return None
        if time.time() - entry.get('stored_at', 0) > self.ttl_seconds:
            self.misses += 1
            return None
        self.hits += 1
        return entry.get('data')

    def set(self, key: str, data: Any, *, endpoint: str = ''):
        if not self.enabled:
            return
        path = self._path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix('.tmp')
            tmp.write_text(json.dumps({'stored_at': time.time(), 'endpoint': endpoint, 'data': data}), encoding='utf-8')
            tmp.replace(path)
        except OSError as error:
            log('keyword providers: cache write failed (' + type(error).__name__ + ')')


@dataclass
class Fetched:
    data: Any
    cached: bool


class HttpTransport:
    """httpx wrapper: JSON in/out, cache, retries with exponential backoff on 429/5xx and network errors."""

    def __init__(self, client: httpx.Client | None = None, cache: JsonCache | None = None, *,
                 max_retries: int = 3, backoff: float = 1.0, timeout: float = 60.0,
                 sleep: Callable[[float], None] = time.sleep):
        self.client = client or httpx.Client(timeout=timeout)
        self.cache = cache or JsonCache('.cache/keyword_research', enabled=False)
        self.max_retries = max_retries
        self.backoff = backoff
        self.sleep = sleep
        self.calls = 0

    @classmethod
    def from_env(cls, client: httpx.Client | None = None):
        return cls(client, JsonCache.from_env())

    def fetch(self, method: str, url: str, *, json_body: Any = None, params: dict | None = None,
              headers: dict | None = None, auth: Any = None, cacheable: bool = True,
              data: dict | None = None, parse: str = 'json') -> Fetched:
        key = JsonCache.key(method, url, params, json_body if json_body is not None else None)
        if cacheable:
            hit = self.cache.get(key)
            if hit is not None:
                return Fetched(hit, True)
        attempt = 0
        while True:
            attempt += 1
            self.calls += 1
            try:
                response = self.client.request(method, url, json=json_body, params=params, headers=headers,
                                               auth=auth, data=data)
            except httpx.HTTPError as error:
                if attempt <= self.max_retries:
                    self._wait(attempt, None)
                    continue
                raise ProviderError(_safe_url(url) + ': network error ' + type(error).__name__) from None
            if response.status_code in RETRY_STATUS and attempt <= self.max_retries:
                self._wait(attempt, response.headers.get('retry-after'))
                continue
            if response.status_code >= 400:
                raise ProviderError(_safe_url(url) + ': HTTP ' + str(response.status_code) + ' ' + _snippet(response))
            try:
                payload = response.json() if parse == 'json' else response.text
            except ValueError:
                raise ProviderError(_safe_url(url) + ': response is not JSON') from None
            if cacheable:
                self.cache.set(key, payload, endpoint=_safe_url(url))
            return Fetched(payload, False)

    def _wait(self, attempt, retry_after):
        delay = self.backoff * (2 ** (attempt - 1))
        if retry_after:
            try:
                delay = max(delay, min(float(retry_after), 60.0))
            except ValueError:
                pass
        self.sleep(delay)


def _safe_url(url):
    return str(url).split('?', 1)[0]


def _snippet(response):
    text = response.text or ''
    return text[:200].replace('\n', ' ')


def utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
