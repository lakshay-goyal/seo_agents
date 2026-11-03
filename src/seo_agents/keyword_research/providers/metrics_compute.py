"""Deterministic metrics computed from provider data (provenance provider='computed'). Pure functions, no I/O.

trend_direction(monthly)        linear least-squares fit over the last 12 months.
                                residual CV = std(residuals) / mean. residual CV >= SEASONAL_CV -> 'seasonal'
                                (seasonality is checked first: a 12-month window cannot separate a seasonal swing
                                from a trend, and a seasonal keyword must not be read as declining).
                                otherwise relative change = slope * (n - 1) / mean:
                                >= TREND_THRESHOLD -> 'rising', <= -TREND_THRESHOLD -> 'declining', else 'stable'.
                                Fewer than MIN_TREND_POINTS known months or mean == 0 -> None (unknown).
seasonal_peak_months            months (1-12) whose volume >= mean * PEAK_RATIO, only when 'seasonal'.
click_opportunity(features)     1.0 minus CLICK_PENALTIES for each SERP feature present, clamped to [0, 1].
serp_authority_share(results)   share of top-10 organic results on HIGH_AUTHORITY domains or .gov/.edu/.ac.
content_alignment(kw, page)     per keyword stem (stopwords removed): weight of the strongest page zone containing it
                                (title / first heading = 1.0, other headings = 0.7, description/copy/alt = 0.4,
                                absent = 0); score = mean over stems, + PHRASE_BONUS when the whole keyword phrase is in
                                the title or first heading; clamped to [0, 1].
page_type_guess(url, title)     URL path / title rules -> product | category | article | tool | forum | home | other.
"""

from __future__ import annotations

import math
import re
from urllib.parse import urlparse

TREND_THRESHOLD = 0.20
SEASONAL_CV = 0.30
PEAK_RATIO = 1.25
MIN_TREND_POINTS = 6

# Share of clicks a SERP feature is assumed to absorb (sum is clamped). Unlisted features cost 0.
CLICK_PENALTIES = {
    'ai_overview': 0.25,
    'featured_snippet': 0.15,
    'answer_box': 0.15,
    'paid': 0.15,
    'shopping': 0.15,
    'popular_products': 0.10,
    'local_pack': 0.15,
    'map': 0.10,
    'knowledge_graph': 0.10,
    'video': 0.05,
    'top_stories': 0.05,
    'people_also_ask': 0.05,
    'images': 0.03,
    'carousel': 0.03,
    'twitter': 0.02,
}

HIGH_AUTHORITY = frozenset({
    'wikipedia.org', 'amazon.com', 'youtube.com', 'reddit.com', 'forbes.com', 'quora.com', 'facebook.com',
    'linkedin.com', 'instagram.com', 'pinterest.com', 'twitter.com', 'x.com', 'medium.com', 'nytimes.com',
    'theguardian.com', 'bbc.com', 'bbc.co.uk', 'cnn.com', 'webmd.com', 'healthline.com', 'mayoclinic.org',
    'investopedia.com', 'nerdwallet.com', 'ebay.com', 'walmart.com', 'target.com', 'etsy.com', 'apple.com',
    'microsoft.com', 'google.com', 'github.com', 'stackoverflow.com', 'imdb.com', 'tripadvisor.com', 'yelp.com',
    'britannica.com', 'merriam-webster.com', 'cnet.com', 'techradar.com', 'businessinsider.com', 'indeed.com',
    'glassdoor.com', 'hubspot.com', 'nih.gov',
})
# Marketplaces / directories / review aggregators: rank for everything, are not business competitors.
AGGREGATORS = frozenset({
    'g2.com', 'capterra.com', 'trustpilot.com', 'yellowpages.com', 'angi.com', 'thumbtack.com', 'houzz.com',
    'booking.com', 'expedia.com', 'alibaba.com', 'aliexpress.com', 'producthunt.com', 'getapp.com',
    'softwareadvice.com', 'clutch.co', 'bbb.org', 'zillow.com', 'craigslist.org', 'fiverr.com', 'upwork.com',
})
AUTHORITY_TLD = re.compile(r'\.(gov|edu|mil)(\.[a-z]{2})?$|\.ac\.[a-z]{2}$|\.gov\.[a-z]{2}$')

STOPWORDS = frozenset("""a an the and or but of for to in on at by with without from into onto about as is are was were be
been being it its this that these those i you he she we they my your our their me us them do does did can could should
would will shall may might must not no yes vs versus how what why which who whom when where near best top""".split())

PHRASE_BONUS = 0.1
ZONE_WEIGHTS = {'title': 1.0, 'headings': 0.7, 'copy': 0.4}


# ---------------------------------------------------------------- domains
def domain_of(url_or_domain: str | None) -> str:
    if not url_or_domain:
        return ''
    text = url_or_domain.strip().lower()
    host = urlparse(text if '//' in text else '//' + text).hostname or ''
    return host[4:] if host.startswith('www.') else host


def matches_domain(domain: str, candidates) -> bool:
    domain = domain_of(domain)
    return any(domain == c or domain.endswith('.' + c) for c in candidates)


def is_high_authority(domain: str) -> bool:
    domain = domain_of(domain)
    return matches_domain(domain, HIGH_AUTHORITY) or bool(AUTHORITY_TLD.search(domain))


def is_aggregator(domain: str) -> bool:
    return matches_domain(domain, AGGREGATORS)


# ---------------------------------------------------------------- trends
def _ordered_volumes(monthly):
    points = []
    for row in monthly or []:
        try:
            year, month = int(row['year']), int(row['month'])
        except (KeyError, TypeError, ValueError):
            continue
        volume = row.get('volume', row.get('search_volume'))
        if volume is None:
            continue
        points.append((year, month, float(volume)))
    points.sort()
    return points[-12:]


def trend_analysis(monthly) -> tuple[str | None, list[int]]:
    points = _ordered_volumes(monthly)
    if len(points) < MIN_TREND_POINTS:
        return None, []
    values = [p[2] for p in points]
    n = len(values)
    mean = sum(values) / n
    if mean <= 0:
        return None, []
    xs = list(range(n))
    x_mean = (n - 1) / 2
    denom = sum((x - x_mean) ** 2 for x in xs)
    slope = sum((x - x_mean) * (y - mean) for x, y in zip(xs, values)) / denom
    intercept = mean - slope * x_mean
    residuals = [y - (intercept + slope * x) for x, y in zip(xs, values)]
    residual_cv = math.sqrt(sum(r * r for r in residuals) / n) / mean
    if residual_cv >= SEASONAL_CV:
        peaks = sorted({p[1] for p in points if p[2] >= mean * PEAK_RATIO})
        return 'seasonal', peaks
    change = slope * (n - 1) / mean
    if change >= TREND_THRESHOLD:
        return 'rising', []
    if change <= -TREND_THRESHOLD:
        return 'declining', []
    return 'stable', []


def trend_direction(monthly) -> str | None:
    return trend_analysis(monthly)[0]


def seasonal_peak_months(monthly) -> list[int]:
    return trend_analysis(monthly)[1]


# ---------------------------------------------------------------- SERP
def click_opportunity(features) -> float | None:
    if features is None:
        return None
    penalty = sum(CLICK_PENALTIES.get(f, 0.0) for f in set(features))
    return round(max(0.0, min(1.0, 1.0 - penalty)), 3)


def serp_authority_share(results, top: int = 10) -> float | None:
    organic = [r for r in results or [] if getattr(r, 'result_type', None) == 'organic'][:top]
    if not organic:
        return None
    strong = sum(1 for r in organic if is_high_authority(r.domain or r.url))
    return round(strong / len(organic), 3)


PRODUCT_PATH = re.compile(r'/(product|products|p|dp|item|items|sku|buy)/')
CATEGORY_PATH = re.compile(r'/(category|categories|collections?|c|shop|store|catalog|department)(/|$)')
ARTICLE_PATH = re.compile(r'/(blog|blogs|article|articles|news|guide|guides|learn|resources|post|posts|wiki|how-to|tutorials?)(/|$)')
FORUM_PATH = re.compile(r'/(forum|forums|community|discussions?|threads?|questions)(/|$)')
TOOL_WORDS = re.compile(r'\b(calculator|generator|checker|converter|tool|estimator|planner|template)s?\b')
FORUM_DOMAINS = ('reddit.com', 'quora.com', 'stackexchange.com', 'stackoverflow.com')
ARTICLE_TITLE = re.compile(r'\b(how to|what is|what are|why|guide|tips|ways|best|vs|review|explained)\b')


def page_type_guess(url: str | None, title: str | None = None) -> str:
    parsed = urlparse(url or '')
    path = (parsed.path or '/').lower()
    title_l = (title or '').lower()
    if matches_domain(parsed.hostname or '', FORUM_DOMAINS) or FORUM_PATH.search(path):
        return 'forum'
    if path in ('', '/') or re.fullmatch(r'/(index\.\w+|home)?/?', path):
        return 'home'
    if PRODUCT_PATH.search(path):
        return 'product'
    if TOOL_WORDS.search(path.replace('-', ' ')) or TOOL_WORDS.search(title_l):
        return 'tool'
    if CATEGORY_PATH.search(path):
        return 'category'
    if ARTICLE_PATH.search(path) or ARTICLE_TITLE.search(title_l):
        return 'article'
    return 'other'


# ---------------------------------------------------------------- content alignment
def stem(token: str) -> str:
    for suffix in ('ingly', 'edly', 'ing', 'ies', 'ied', 'es', 'ed', 'ly', 's'):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            if suffix in ('ies', 'ied'):
                return token[:-3] + 'y'
            if suffix == 'es' and not token.endswith(('ses', 'xes', 'zes', 'ches', 'shes')):
                return token[:-1]
            return token[:-len(suffix)]
    return token


def tokens(text: str | None) -> list[str]:
    return re.findall(r'[a-z0-9]+', (text or '').lower())


def stems(text: str | None, *, drop_stopwords: bool = False) -> set[str]:
    return {stem(t) for t in tokens(text) if not (drop_stopwords and t in STOPWORDS)}


def _clean_heading(text):
    return re.sub(r'^\s*h[1-6]\s*[:\-]\s*', '', text or '', flags=re.I)


def content_alignment(keyword: str, page) -> float | None:
    if page is None:
        return None
    wanted = stems(keyword, drop_stopwords=True) or stems(keyword)
    if not wanted:
        return None
    headings = [_clean_heading(h) for h in (page.headings or [])]
    top_text = ' '.join([page.title or ''] + headings[:1])
    zones = {
        'title': stems(top_text),
        'headings': stems(' '.join(headings[1:])),
        'copy': stems(' '.join([page.description or ''] + list(page.copy or [])
                                + [str((img or {}).get('alt') or '') for img in page.images or []])),
    }
    total = 0.0
    for s in wanted:
        total += max((ZONE_WEIGHTS[z] for z, bag in zones.items() if s in bag), default=0.0)
    score = total / len(wanted)
    phrase = ' '.join(tokens(keyword))
    if phrase and phrase in ' '.join(tokens(top_text)):
        score += PHRASE_BONUS
    return round(max(0.0, min(1.0, score)), 3)
