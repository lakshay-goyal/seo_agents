"""Shared data contracts for the keyword research stage. Every sub-package codes against these types only.

Stage position:  repo_analysis (seo_reports/exploration)  ->  keyword_research (seo_reports/keywords)  ->  onpage

    providers/    ResearchToolkit       third-party data (DataForSEO, Google Ads, Search Console, Trends, questions)
    generation/   KeywordGenerator      LLM: business profile + candidate keywords per page (never invents metrics)
    judge/        KeywordJudge          deterministic gates + Jev (TypeSafe) judgments + decision table in code
    mapping/      KeywordMapper         site-wide page <-> keyword assignment, cannibalization guard
    output/       KeywordOutputWriter   keyword_research.csv, keyword_map.json, pages/<id>.json, summary.json
    keyword_research.py  KeywordResearchWorkflow (LangGraph: generate -> enrich -> judge loop per page)

Rules every module follows:
  * A metric that a provider did not return is `None` (unknown), never 0. Every metric carries provenance.
  * The LLM proposes keywords and hypotheses only. Volumes, difficulty, SERP and trends come from providers.
  * Jev answers atomic judgments; code (judge/decision.py) maps judgments + metrics to accept / reject.
  * Every rejection carries a RejectionCode plus a human-readable reason (written to the CSV).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Protocol

SCHEMA_VERSION = 'keyword-research/1'


# ---------------------------------------------------------------- enums (string values are written to JSON/CSV)
class Intent(str, Enum):
    INFORMATIONAL = 'informational'
    COMMERCIAL = 'commercial'
    TRANSACTIONAL = 'transactional'
    NAVIGATIONAL = 'navigational'
    LOCAL = 'local'


class Tail(str, Enum):
    LONG = 'long'      # >= 3 words, specific need (current focus)
    SHORT = 'short'    # 1-2 words, head term (added gradually, see ResearchTargets.short_tail)


class KeywordKind(str, Enum):
    PRIMARY = 'primary'              # candidate for the page's one primary keyword
    SECONDARY = 'secondary'          # supporting variant / related term with the same intent
    QUESTION = 'question'            # AEO: a question the page should answer directly
    CONVERSATIONAL = 'conversational'  # GEO: natural-language prompt an AI answer engine would get


class Source(str, Enum):
    LLM_SEED = 'llm-seed'                    # generator, from page content + business profile
    COMPETITOR_REVERSE = 'competitor-reverse'  # provider: keywords a competitor URL ranks for
    RELATED_EXPANSION = 'related-expansion'  # provider: related / suggestion endpoints
    QUESTION_EXPANSION = 'question-expansion'  # provider: question / autocomplete / People-Also-Ask
    ADS_IDEAS = 'ads-ideas'                  # Google Ads KeywordPlanIdeaService
    GSC = 'gsc'                              # Search Console: the site already gets impressions for it


class Decision(str, Enum):
    ACCEPT = 'accept'
    REJECT = 'reject'
    REVIEW = 'review'    # judgments unsettled (Noul between thresholds) or data missing; a human decides


class RejectionCode(str, Enum):
    LOW_VOLUME = 'low_volume'
    HIGH_DIFFICULTY = 'high_difficulty'
    INTENT_MISMATCH = 'intent_mismatch'              # SERP/user intent does not match what the page offers
    NOT_RELEVANT = 'not_relevant_to_business'
    POOR_CONTENT_ALIGNMENT = 'poor_content_alignment'
    UNSATISFIABLE = 'page_cannot_satisfy_intent'
    LOW_CONVERSION = 'low_conversion_potential'
    COMPETITOR_BRAND = 'competitor_brand_term'
    DUPLICATE = 'duplicate_or_variant'               # same intent as a keyword already accepted for this page
    CANNIBALIZATION = 'cannibalization'              # another page owns this intent (mapping stage)
    SERP_DOMINATED = 'serp_dominated_by_authorities'
    ZERO_CLICK = 'low_click_opportunity'              # SERP features absorb clicks
    DECLINING_TREND = 'declining_trend'
    MISSING_DATA = 'missing_data'
    QUOTA_FULL = 'quota_full'                        # valid, but the page already has enough of this kind
    JUDGE_ERROR = 'judge_error'                      # the Jev call failed; never read as a "no"


# ---------------------------------------------------------------- inputs
@dataclass
class Market:
    """Target market for every provider call (DataForSEO location_code / language_code, Ads geo + language)."""

    country: str = 'US'               # ISO-3166 alpha-2
    location_code: int = 2840         # DataForSEO / Google Ads geo target constant (2840 = United States)
    language_code: str = 'en'
    language_name: str = 'English'


@dataclass
class ResearchTargets:
    """How many accepted keywords each page needs before its loop stops."""

    primary: int = 1
    secondary: int = 4
    question: int = 3
    conversational: int = 2
    short_tail: int = 0               # raise gradually (phase 2); long tail is the current focus
    max_iterations: int = 4
    candidates_per_iteration: int = 25
    # Hard gates (judge/gates.py); None disables a gate.
    min_volume: int | None = 10
    max_difficulty: int | None = 40   # 0-100 organic difficulty (DataForSEO); long-tail focus = low competition
    max_difficulty_short_tail: int | None = 55


@dataclass
class BusinessProfile:
    """What the site offers and to whom (generation/business_profiler.py, from the exploration JSON only)."""

    brand: str
    site_type: str                    # personal | business | ecommerce | publisher | docs | saas | other
    offering: str                     # one sentence: what is sold / provided
    audience: list[str] = field(default_factory=list)
    value_props: list[str] = field(default_factory=list)
    seed_topics: list[str] = field(default_factory=list)
    competitors: list[str] = field(default_factory=list)   # domains, given or discovered
    excluded_terms: list[str] = field(default_factory=list)  # competitor brands etc.
    market: Market = field(default_factory=Market)


@dataclass
class PageBrief:
    """One page as the keyword stage sees it (built from the on-page PageContextBuilder context)."""

    page_id: str
    url: str
    absolute_url: str | None
    page_type: str | None
    title: str | None
    description: str | None
    headings: list[str]
    copy: list[str]                   # visible copy excerpt, in document order
    images: list[dict]                # {'src', 'alt'}
    lang: str = 'en'
    existing_queries: list[str] = field(default_factory=list)  # exploration n-grams / GSC queries


# ---------------------------------------------------------------- candidates and evidence
@dataclass
class KeywordCandidate:
    keyword: str                      # lowercase, trimmed
    page_id: str
    kind: KeywordKind
    source: Source
    tail: Tail
    hypothesized_intent: Intent | None = None
    rationale: str = ''               # why the generator proposed it (user purpose it resolves)
    iteration: int = 0
    competitor_url: str | None = None  # set for Source.COMPETITOR_REVERSE


@dataclass
class Provenance:
    provider: str                     # 'dataforseo' | 'google_ads' | 'gsc' | 'google_trends' | 'autocomplete' | 'computed'
    endpoint: str
    retrieved_at: str                 # ISO-8601 UTC
    location: str | int | None = None
    language: str | None = None


@dataclass
class Metric:
    value: Any                        # None = unknown (never coerce to 0)
    provenance: Provenance | None = None


@dataclass
class SerpResult:
    position: int
    url: str
    domain: str
    title: str
    result_type: str                  # organic | featured_snippet | people_also_ask | video | shopping | local_pack | ...
    page_type_guess: str | None = None  # product | category | article | tool | forum | home | other (from URL/title)


@dataclass
class SerpSnapshot:
    keyword: str
    results: list[SerpResult]
    features: list[str]               # SERP feature types present above/among organic results
    people_also_ask: list[str]
    provenance: Provenance | None = None


@dataclass
class KeywordEvidence:
    """Everything known about one keyword, from providers + deterministic computation. Metric keys:

    search_volume (int/month), keyword_difficulty (0-100 organic), cpc (USD), ads_competition (0-1, paid, NOT organic),
    provider_intent (Intent value), monthly_searches (list[{'year','month','volume'}]), trend_direction
    ('rising'|'stable'|'declining'|'seasonal'), seasonal_peak_months (list[int]), region_interest (list[{'region','value'}]),
    gsc_impressions, gsc_clicks, gsc_ctr, gsc_position, content_alignment (0-1, computed from page copy),
    serp_authority_share (0-1 share of top-10 on high-authority domains), click_opportunity (0-1, computed from features).
    """

    keyword: str
    metrics: dict[str, Metric] = field(default_factory=dict)
    serp: SerpSnapshot | None = None
    competitor_urls: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)   # provider failures (recorded, never silently zeroed)

    def value(self, name, default=None):
        metric = self.metrics.get(name)
        return default if metric is None or metric.value is None else metric.value


@dataclass
class JudgeVerdict:
    keyword: str
    page_id: str
    decision: Decision
    composite_score: float | None     # 0-100 weighted sum (judge/scoring.py); None when rejected at a gate
    factor_scores: dict[str, float | None]  # normalized 0-1 per factor, None = unknown
    judgments: dict[str, Any]         # raw Jev answers {question_id: noul | choice | score} for audit
    rejection_codes: list[RejectionCode] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)   # human-readable, one per code or acceptance note
    assigned_kind: KeywordKind | None = None           # role it fills on the page when accepted
    jev_payload_ref: str | None = None                 # id of the logged request payload (rerunnable)


@dataclass
class KeywordRecord:
    """One row of keyword_research.csv: candidate + evidence + verdict (+ mapping outcome)."""

    candidate: KeywordCandidate
    evidence: KeywordEvidence | None
    verdict: JudgeVerdict | None
    mapped_page_id: str | None = None   # set by the mapper; differs from candidate.page_id when reassigned
    final_status: str = 'rejected'      # 'primary' | 'secondary' | 'question' | 'conversational' | 'short_tail' | 'review' | 'rejected'


@dataclass
class PageKeywordPlan:
    """Per-page output consumed by the on-page stage (seo_reports/keywords/keyword_map.json)."""

    page_id: str
    url: str
    intent: Intent | None
    journey_stage: str | None         # awareness | consideration | decision | retention
    primary: str | None
    secondary: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)       # AEO
    conversational: list[str] = field(default_factory=list)  # GEO
    short_tail: list[str] = field(default_factory=list)
    cluster: dict = field(default_factory=dict)              # {'role': pillar|cluster|standalone, 'pillar_url', 'related_urls'}
    competitor_urls: list[str] = field(default_factory=list)
    content_gaps: list[str] = field(default_factory=list)    # what competitors cover that the page does not
    iterations: int = 0
    status: str = 'complete'          # complete | partial (quota not met) | failed


def to_dict(obj):
    """Dataclass -> JSON-ready dict (enums as values)."""
    def convert(value):
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, dict):
            return {k: convert(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [convert(v) for v in value]
        return value
    return convert(asdict(obj))


# ---------------------------------------------------------------- component interfaces
class ResearchToolkit(Protocol):
    """providers/research_toolkit.py. Aggregates every configured provider; missing credentials -> provider skipped."""

    def available_providers(self) -> list[str]: ...
    def enrich(self, keywords: list[str], market: Market, *, page: PageBrief | None = None) -> dict[str, KeywordEvidence]: ...
    def expand(self, seeds: list[str], market: Market, *, limit: int = 50) -> list[KeywordCandidate]: ...
    def questions(self, seeds: list[str], market: Market, *, limit: int = 30) -> list[KeywordCandidate]: ...
    def serp(self, keyword: str, market: Market) -> SerpSnapshot | None: ...
    def competitor_urls(self, keywords: list[str], market: Market, *, own_domain: str | None, limit: int = 5) -> list[str]: ...
    def competitor_keywords(self, url: str, market: Market, *, limit: int = 100) -> list[KeywordCandidate]: ...
    def page_queries(self, page_url: str, market: Market) -> list[KeywordCandidate]: ...   # GSC
    def compare_trends(self, keywords: list[str], market: Market) -> dict[str, dict]: ...   # interest over time + regions


class KeywordGenerator(Protocol):
    """generation/keyword_generator.py (LLM)."""

    def profile_business(self, site_packet: dict, market: Market) -> BusinessProfile: ...
    def generate(self, page: PageBrief, business: BusinessProfile, *, needs: dict[str, int],
                 competitor_keywords: list[KeywordCandidate], accepted: list[KeywordRecord],
                 rejected: list[KeywordRecord], iteration: int, limit: int) -> list[KeywordCandidate]: ...


class KeywordJudge(Protocol):
    """judge/keyword_judge.py (gates + Jev + decision table)."""

    def judge(self, page: PageBrief, business: BusinessProfile, candidates: list[KeywordCandidate],
              evidence: dict[str, KeywordEvidence], *, accepted: list[KeywordRecord],
              targets: ResearchTargets) -> list[JudgeVerdict]: ...
    def same_intent(self, pairs: list[tuple[str, str]], evidence: dict[str, KeywordEvidence]) -> dict[tuple[str, str], float]: ...
