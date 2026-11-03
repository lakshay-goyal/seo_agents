"""Free question source (AnswerThePublic-style): Google autocomplete expanded with question words and prepositions.

    GET https://suggestqueries.google.com/complete/search?client=firefox&q=...&hl=..&gl=..  -> ["q", ["s1", ...]]

Polite: sleeps SLEEP_SECONDS between live (non-cached) requests, at most SEO_KEYWORD_AUTOCOMPLETE_MAX requests per run
(default 40). SEO_KEYWORD_AUTOCOMPLETE=0 disables it. People-Also-Ask questions come from the DataForSEO SERP in
research_toolkit.py. Autocomplete returns suggestions only, no metrics.
"""

from __future__ import annotations

import re
import time

from seo_agents.keyword_research.providers.http_cache import HttpTransport, ProviderError, env_flag, env_number

URL = 'https://suggestqueries.google.com/complete/search'
SLEEP_SECONDS = 1.0

QUESTION_WORDS = ('what', 'how', 'why', 'which', 'who', 'when', 'where', 'can', 'is', 'are', 'does', 'do',
                  'should', 'will')
PREFIX_MODIFIERS = ('what', 'how', 'why', 'which', 'can', 'is', 'best', 'where', 'when', 'who')
SUFFIX_MODIFIERS = ('vs', 'for', 'near', 'without', 'with', 'to', 'like', 'or', 'and', 'is')
QUESTION_START = re.compile(r'^(' + '|'.join(QUESTION_WORDS) + r')\b')


def is_question(text: str) -> bool:
    return bool(QUESTION_START.match(text.strip().lower())) or text.strip().endswith('?')


def expansion_queries(seed: str) -> list[str]:
    seed = seed.strip().lower()
    return [m + ' ' + seed for m in PREFIX_MODIFIERS] + [seed + ' ' + m for m in SUFFIX_MODIFIERS]


class AutocompleteQuestions:
    name = 'autocomplete'

    def __init__(self, transport: HttpTransport, *, enabled: bool = True, max_requests: int = 40,
                 sleep=time.sleep, sleep_seconds: float = SLEEP_SECONDS):
        self.transport = transport
        self.enabled = enabled
        self.max_requests = max_requests
        self.sleep = sleep
        self.sleep_seconds = sleep_seconds
        self.requests = 0

    @classmethod
    def from_env(cls, transport: HttpTransport):
        return cls(transport, enabled=env_flag('SEO_KEYWORD_AUTOCOMPLETE', True),
                   max_requests=int(env_number('SEO_KEYWORD_AUTOCOMPLETE_MAX', 40, int)))

    @property
    def available(self) -> bool:
        return self.enabled

    @property
    def budget_left(self) -> int:
        return max(0, self.max_requests - self.requests)

    def suggest(self, query: str, market) -> list[str]:
        if not self.enabled:
            raise ProviderError('autocomplete: disabled')
        params = {'client': 'firefox', 'ie': 'utf-8', 'oe': 'utf-8', 'q': query, 'hl': market.language_code, 'gl': market.country.lower()}
        fetched = self.transport.fetch('GET', URL, params=params)
        if not fetched.cached:
            self.requests += 1
            if self.sleep_seconds:
                self.sleep(self.sleep_seconds)
        data = fetched.data
        if not isinstance(data, list) or len(data) < 2 or not isinstance(data[1], list):
            return []
        return [str(s).strip().lower() for s in data[1] if str(s).strip()]

    def expand(self, seed: str, market, *, limit: int = 30) -> list[str]:
        """Suggestions for seed + modifiers that still contain a seed token. Stops at the request budget."""
        seed_tokens = set(re.findall(r'[a-z0-9]+', seed.lower()))
        out, seen = [], {seed.strip().lower()}
        for query in expansion_queries(seed):
            if self.budget_left <= 0 or len(out) >= limit:
                break
            for suggestion in self.suggest(query, market):
                if suggestion in seen or not seed_tokens & set(re.findall(r'[a-z0-9]+', suggestion)):
                    continue
                seen.add(suggestion)
                out.append(suggestion)
        out.sort(key=lambda s: not is_question(s))   # stable: questions first
        return out[:limit]
