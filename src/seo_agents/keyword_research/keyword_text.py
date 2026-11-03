"""Deterministic keyword text helpers shared by the workflow, the mapper and the writer (no model, no network)."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

STOPWORDS = set('a an the and or of for to in on at by with from is are be can do does how what why when where which who '
                'i my me we our you your it its this that vs best top near about into'.split())
WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)


def normalize(keyword):
    """Lowercase, trimmed, single-spaced (the identity of a keyword everywhere in the stage)."""
    return re.sub(r'\s+', ' ', str(keyword or '')).strip().lower()


def stem(word):
    if len(word) > 4 and word.endswith('ies'):
        return word[:-3] + 'y'
    if len(word) > 3 and word.endswith('s') and not word.endswith('ss'):
        return word[:-1]
    return word


def tokens(text, *, drop=()):
    """Content tokens (stopwords removed, light plural stemming)."""
    dropped = set(drop)
    return {stem(w) for w in WORD.findall(normalize(text)) if w not in STOPWORDS and stem(w) not in dropped}


def jaccard(left, right):
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def coverage(keyword, corpus_tokens):
    """Share of the keyword's content tokens present in the page copy (0-1; None when the keyword has none)."""
    words = tokens(keyword)
    if not words:
        return None
    return len(words & corpus_tokens) / len(words)


def domain_of(url):
    """example.com for https://www.example.com/x, or for a bare domain."""
    if not url:
        return None
    text = str(url).strip()
    host = urlsplit(text if '//' in text else '//' + text).netloc.lower()
    host = host.split('@')[-1].split(':')[0]
    return host[4:] if host.startswith('www.') else (host or None)
