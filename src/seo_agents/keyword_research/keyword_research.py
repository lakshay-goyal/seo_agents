"""Keyword research: the LangGraph workflow that finds, measures, judges and maps keywords for every page.

Outer graph (site level, one pass):

    START
      -> load                  PageSelector (ExplorationReportsLoader + PageContextBuilder) -> PageBrief per page,
                               own domain, Market + ResearchTargets (CLI / SEO_KEYWORD_*), available providers
      -> business_profile      generator.profile_business(site packet) once; CLI competitors merged in
      -> competitor_discovery  per page: seed queries -> toolkit.competitor_urls -> toolkit.competitor_keywords(url)
                               (REVERSE research on the top competitor URLs) + toolkit.page_queries (GSC demand);
                               the most relevant SEO_KEYWORD_COMPETITOR_ENRICH of them are enriched up front so the
                               generator sees their volume / difficulty / position
      -> research_loop         pages in parallel (SEO_KEYWORD_CONCURRENCY), each runs the compiled page loop below
      -> map                   KeywordMapper: cannibalization guard (judge.same_intent), one primary per page,
                               PageKeywordPlan per page (slots, intent, journey, cluster, competitor URLs, gaps)
      -> write                 KeywordOutputWriter -> seo_reports/keywords/
    END

Page loop (a compiled per-page subgraph with a real conditional edge, invoked once per page):

    START -> generate -> expand -> enrich -> judge --should_continue--> generate   (needs left, budget left, new input)
                ^                                         |
                +-----------------------------------------+-------------> finish -> END
                                                            quota met | iteration >= max_iterations | no new candidates

    generate  needs = targets - accepted per slot; generator.generate(... accepted = this page's accepts + every other
              page's accepts so far (anti-cannibalization), rejected (with codes and reasons), competitor_evidence ...)
    expand    provider expansions: toolkit.expand on last iteration's accepts (or the page seeds), toolkit.questions
              when questions are still needed; they join a per-page pool together with the competitor/GSC keywords,
              and the most relevant pool items (needed kinds first) fill the batch up to candidates_per_iteration
    enrich    toolkit.enrich(batch, page=...) + toolkit.compare_trends in groups of 5 (seasonality, regions)
    judge     judge.judge(...) -> one KeywordRecord per candidate (a missing verdict is REVIEW + judge_error)
    finish    pool items never judged are recorded as `unevaluated` so the CSV holds every candidate

Why a per-page subgraph rather than one big batch state: pages are independent until mapping, each page stops at a
different iteration, and a subgraph keeps the loop a plain LangGraph conditional edge over one page's state while
the outer graph stays linear. The research_loop node fans the pages out over a ThreadPoolExecutor exactly like the
on-page CheckNode does. Components (toolkit, generator, judge) are injected or built lazily from their modules
(providers/, generation/, judge/), so this module imports before they exist. Tools: lib/tool_mapping.py.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import inspect
from threading import Lock
from dataclasses import replace
from datetime import datetime, timezone
import os
import traceback
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from seo_agents.keyword_research.contracts import (BusinessProfile, Decision, JudgeVerdict, KeywordEvidence, KeywordKind,
                                                   KeywordRecord, Market, Metric, Provenance, RejectionCode,
                                                   ResearchTargets, Source)
from seo_agents.keyword_research.keyword_text import domain_of, normalize, tokens
from seo_agents.keyword_research.mapping.keyword_mapper import KeywordMapper, final_status, slot_of
from seo_agents.keyword_research.output.keyword_output_writer import KeywordOutputWriter
from seo_agents.keyword_research.page_selection import PageSelector, seed_queries
from seo_agents.utils.state_reducers import merge_dicts
from seo_agents.utils.step_logger import done, log, start

WORKFLOW_NODES = ('load', 'business_profile', 'competitor_discovery', 'research_loop', 'map', 'write')
PAGE_LOOP_NODES = ('generate', 'expand', 'enrich', 'judge', 'finish')
SLOT_TARGETS = ('primary', 'secondary', 'question', 'conversational', 'short_tail')
LANGUAGE_NAMES = {'en': 'English', 'es': 'Spanish', 'fr': 'French', 'de': 'German', 'it': 'Italian', 'pt': 'Portuguese',
                  'nl': 'Dutch', 'hi': 'Hindi', 'ja': 'Japanese', 'ko': 'Korean', 'zh': 'Chinese', 'ar': 'Arabic',
                  'ru': 'Russian', 'pl': 'Polish', 'sv': 'Swedish', 'tr': 'Turkish'}
TREND_GROUP = 5   # Google Trends compares at most 5 terms per request
NOT_METRICS = {'provenance', 'error', 'errors', 'keyword', 'batch'}


class KeywordResearchConfigError(RuntimeError):
    """The stage cannot run (e.g. no OPENROUTER_API_KEY for the generator)."""


def env_int(name, default):
    value = os.getenv(name)
    return int(value) if value not in (None, '') else default


def market_from(country=None, location_code=None, language=None):
    language = (language or os.getenv('SEO_KEYWORD_LANGUAGE') or 'en').lower()
    return Market(country=(country or os.getenv('SEO_KEYWORD_COUNTRY') or 'US').upper(),
                  location_code=int(location_code or env_int('SEO_KEYWORD_LOCATION_CODE', 2840)),
                  language_code=language, language_name=LANGUAGE_NAMES.get(language, language))


def targets_from(targets=None, max_iterations=None):
    targets = targets or ResearchTargets()
    iterations = max_iterations or env_int('SEO_KEYWORD_MAX_ITERATIONS', 0)
    return replace(targets, max_iterations=iterations) if iterations else targets


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def needs_for(targets, records):
    """Accepted keywords still needed per slot ('primary' | 'secondary' | 'question' | 'conversational' | 'short_tail')."""
    have = {name: 0 for name in SLOT_TARGETS}
    for record in records:
        slot = slot_of(record)
        if slot:
            have[slot] += 1
    return {name: max(0, getattr(targets, name) - have[name]) for name in SLOT_TARGETS}


def admit(candidates, page_id, seen, *, iteration=None, **overrides):
    """New, normalized, page-bound candidates (skips keywords this page has already seen)."""
    out = []
    for candidate in candidates or []:
        keyword = normalize(candidate.keyword)
        if not keyword or keyword in seen:
            continue
        seen.add(keyword)
        changes = {'keyword': keyword, 'page_id': page_id, **overrides}
        if iteration is not None:
            changes['iteration'] = iteration
        out.append(replace(candidate, **changes))
    return out


def relevance_order(candidates, page, needs):
    """Needed kinds first, then token overlap with the page (title, headings, copy, queries), then input order."""
    corpus = tokens(' '.join([page.title or '', page.description or ''] + page.headings + page.copy[:20]
                             + page.existing_queries))

    def wanted(candidate):
        kind = candidate.kind.value
        if candidate.kind == KeywordKind.SECONDARY and candidate.tail.value == 'short':
            kind = 'short_tail'
        return needs.get(kind, 0) > 0 or (candidate.kind == KeywordKind.SECONDARY and needs.get('primary', 0) > 0)
    scored = [(0 if wanted(c) else 1, -len(tokens(c.keyword) & corpus), i, c) for i, c in enumerate(candidates)]
    return [c for *_, c in sorted(scored, key=lambda row: row[:3])]


def missing_verdict(candidate, why):
    return JudgeVerdict(keyword=candidate.keyword, page_id=candidate.page_id, decision=Decision.REVIEW, composite_score=None,
                        factor_scores={}, judgments={}, rejection_codes=[RejectionCode.JUDGE_ERROR],
                        reasons=['judge error: ' + why + ' (not a rejection; a human decides)'])


def merge_trends(evidence, data, market):
    """Fold toolkit.compare_trends() output for one keyword into its evidence (never overwrites a known metric)."""
    if not isinstance(data, dict):
        return
    provenance = data.get('provenance') if isinstance(data.get('provenance'), Provenance) else \
        Provenance(provider='google_trends', endpoint='compare_trends', retrieved_at=now(), location=market.country,
                   language=market.language_code)
    for key in ('error', 'errors'):
        if data.get(key):
            evidence.errors += data[key] if isinstance(data[key], list) else [str(data[key])]
    for key, value in data.items():
        if key in NOT_METRICS:
            continue
        found = value if isinstance(value, Metric) else Metric(value=value, provenance=provenance)
        current = evidence.metrics.get(key)
        if found.value is not None and (current is None or current.value is None):
            evidence.metrics[key] = found


# ---------------------------------------------------------------- state
class KeywordResearchState(TypedDict, total=False):
    reports_dir: str
    out_dir: str
    site_url: str
    only_pages: list
    cli_competitors: list
    market: Any
    targets: Any
    pages: dict                 # page_id -> PageBrief
    origin: str
    own_domain: str
    site_packet: dict
    business: Any
    providers: list
    warnings: list
    jev_mode: str
    competitors: Annotated[dict, merge_dicts]    # page_id -> {'urls', 'keywords', 'errors'}
    page_results: Annotated[dict, merge_dicts]   # page_id -> {'records', 'iterations', 'stop_reason', 'status', ...}
    mapping: Any
    costs: dict
    judge_filters: dict
    generated_at: str
    written: dict


class PageLoopState(TypedDict, total=False):
    page: Any
    business: Any
    market: Any
    targets: Any
    seeds: list
    competitor_keywords: list   # reverse + GSC candidates (also seed the pool)
    site_pages: list            # [{'page_id', 'url', 'title'}] of every selected page (generator anti-cannibalization)
    iteration: int
    needs: dict
    batch: list                 # candidates judged in this iteration
    pool: list                  # provider / competitor candidates waiting for a batch slot
    seen: set
    evidence: dict              # keyword -> KeywordEvidence
    records: list               # every KeywordRecord of this page so far
    fresh_accepts: list         # keywords accepted in the last iteration (next expansion seeds)
    errors: list
    stop_reason: str


def stop_reason(state):
    """None while the page loop should continue, else why it stops."""
    targets = state['targets']
    if not any(needs_for(targets, state.get('records') or []).values()):
        return 'quota_met'
    if state.get('iteration', 0) >= targets.max_iterations:
        return 'max_iterations'
    if not state.get('batch'):
        return 'no_new_candidates'
    return None


# ---------------------------------------------------------------- page loop nodes
class GenerateNode:
    """Page loop: ask the generator for candidates that fill the remaining needs, with accept/reject feedback."""

    name = 'generate'

    def __init__(self, workflow):
        self.workflow = workflow

    def __call__(self, state):
        page, targets, records = state['page'], state['targets'], state.get('records') or []
        iteration = state.get('iteration', 0) + 1
        needs = needs_for(targets, records)
        accepted = self.workflow.site_accepted(page.page_id, [r for r in records if slot_of(r)])
        rejected = [r for r in records if r.verdict is not None and r.verdict.decision == Decision.REJECT]
        seen, errors = set(state.get('seen') or ()), list(state.get('errors') or [])
        competitor_keywords = (state.get('competitor_keywords') or [])[:60]
        evidence = state.get('evidence') or {}
        extra = self.workflow.generator_extras(
            competitor_evidence={c.keyword: evidence[c.keyword] for c in competitor_keywords if c.keyword in evidence},
            site_pages=state.get('site_pages') or [])
        generator = self.workflow.generator
        try:
            generated = generator.generate(page, state['business'], needs=needs, competitor_keywords=competitor_keywords,
                                           accepted=accepted, rejected=rejected, iteration=iteration,
                                           limit=targets.candidates_per_iteration, **extra)
            meta = getattr(generator, 'last_meta', None)
            if not generated and isinstance(meta, dict) and meta.get('failed'):
                errors.append(f'generate#{iteration}: generator failed: ' + '; '.join(map(str, meta.get('errors') or []))[:300])
        except Exception as error:  # noqa: BLE001 - a failed generation still lets provider expansions run
            log(f'keyword {page.page_id} generate #{iteration}: {type(error).__name__}: {error}')
            errors.append(f'generate#{iteration}: {type(error).__name__}: {str(error)[:200]}')
            generated = []
        batch = admit(generated, page.page_id, seen, iteration=iteration)
        return {'iteration': iteration, 'needs': needs, 'batch': batch, 'seen': seen, 'errors': errors}


class ExpandNode:
    """Page loop: provider expansions (related + questions) join the pool; the most relevant fill the batch."""

    name = 'expand'

    def __init__(self, workflow):
        self.workflow = workflow

    def __call__(self, state):
        page, market, needs, iteration = state['page'], state['market'], state['needs'], state['iteration']
        toolkit, seen = self.workflow.toolkit, set(state.get('seen') or ())
        batch, errors = list(state.get('batch') or []), list(state.get('errors') or [])
        seeds = list(state.get('fresh_accepts') or []) or (state.get('seeds') or []) + [c.keyword for c in batch[:5]]
        found = []
        if seeds and (needs['primary'] or needs['secondary'] or needs['short_tail']):
            found += self.call(lambda: toolkit.expand(seeds[:10], market, limit=self.workflow.expand_limit), 'expand', errors)
        if seeds and needs['question']:
            found += self.call(lambda: toolkit.questions(seeds[:5], market, limit=self.workflow.question_limit),
                               'questions', errors)
        pool = list(state.get('pool') or []) + admit(found, page.page_id, seen)
        room = max(0, state['targets'].candidates_per_iteration)
        ordered = relevance_order(pool, page, needs)
        taken = [replace(c, iteration=iteration) for c in ordered[:room]]
        return {'batch': batch + taken, 'pool': ordered[room:], 'seen': seen, 'errors': errors}

    @staticmethod
    def call(fn, label, errors):
        try:
            return list(fn() or [])
        except Exception as error:  # noqa: BLE001 - a provider failure is recorded, never fatal
            errors.append(f'{label}: {type(error).__name__}: {str(error)[:200]}')
            return []


class EnrichNode:
    """Page loop: metrics for the batch (toolkit.enrich) + trend comparisons in groups of 5."""

    name = 'enrich'

    def __init__(self, workflow):
        self.workflow = workflow

    def __call__(self, state):
        page, market, toolkit = state['page'], state['market'], self.workflow.toolkit
        evidence, errors = dict(state.get('evidence') or {}), list(state.get('errors') or [])
        batch = [c.keyword for c in state.get('batch') or []]
        keywords = [k for k in batch if k not in evidence]   # competitor keywords may be enriched already
        found = {}
        if keywords:
            try:
                found = {normalize(k): v for k, v in (toolkit.enrich(keywords, market, page=page) or {}).items()}
            except Exception as error:  # noqa: BLE001
                errors.append(f'enrich: {type(error).__name__}: {str(error)[:200]}')
        fresh = {}
        for keyword in batch:
            if keyword in evidence:
                known = evidence[keyword]
                fresh[keyword] = replace(known, metrics=dict(known.metrics), errors=list(known.errors))
            else:
                fresh[keyword] = found.get(keyword) or KeywordEvidence(keyword=keyword,
                                                                       errors=['no evidence returned by any provider'])
        need_trends = [k for k in batch if fresh[k].value('trend_direction') is None or fresh[k].value('region_interest') is None]
        for start_at in range(0, len(need_trends), TREND_GROUP):
            group = need_trends[start_at:start_at + TREND_GROUP]
            try:
                trends = {normalize(k): v for k, v in (toolkit.compare_trends(group, market) or {}).items()}
            except Exception as error:  # noqa: BLE001
                errors.append(f'compare_trends: {type(error).__name__}: {str(error)[:200]}')
                continue
            for keyword in group:
                merge_trends(fresh[keyword], trends.get(keyword), market)
        return {'evidence': {**evidence, **fresh}, 'errors': errors}


class JudgeNode:
    """Page loop: verdict per candidate -> KeywordRecord (every candidate is recorded, accepted or not)."""

    name = 'judge'

    def __init__(self, workflow):
        self.workflow = workflow

    def __call__(self, state):
        page, batch, targets = state['page'], list(state.get('batch') or []), state['targets']
        records, evidence, errors = list(state.get('records') or []), state.get('evidence') or {}, list(state.get('errors') or [])
        if not batch:
            return {'fresh_accepts': []}
        why = 'no verdict returned for this keyword'
        try:
            verdicts = self.workflow.judge.judge(page, state['business'], batch,
                                                 {c.keyword: evidence[c.keyword] for c in batch if c.keyword in evidence},
                                                 accepted=[r for r in records if slot_of(r)], targets=targets) or []
        except Exception as error:  # noqa: BLE001 - a judge failure is REVIEW, never a silent "no"
            log(f'keyword {page.page_id} judge #{state["iteration"]}: {type(error).__name__}: {error}')
            why = f'{type(error).__name__}: {str(error)[:200]}'
            errors.append('judge: ' + why)
            verdicts = []
        by_keyword = {normalize(v.keyword): v for v in verdicts}
        fresh = []
        for candidate in batch:
            record = KeywordRecord(candidate=candidate, evidence=evidence.get(candidate.keyword),
                                   verdict=by_keyword.get(candidate.keyword) or missing_verdict(candidate, why))
            record.final_status = final_status(record)
            records.append(record)
            if slot_of(record):
                fresh.append(candidate.keyword)
        self.workflow.register_accepted(page.page_id, [r for r in records if slot_of(r)])
        counts = {}
        for record in records[-len(batch):]:
            counts[record.verdict.decision.value] = counts.get(record.verdict.decision.value, 0) + 1
        log(f'keyword {page.page_id} iteration {state["iteration"]}: {len(batch)} judged ('
            + ', '.join(f'{k}={v}' for k, v in sorted(counts.items())) + ')')
        return {'records': records, 'fresh_accepts': fresh, 'errors': errors}


class FinishNode:
    """Page loop: record why the loop stopped; pool items never judged become `unevaluated` rows."""

    name = 'finish'

    def __call__(self, state):
        records = list(state.get('records') or [])
        for candidate in state.get('pool') or []:
            records.append(KeywordRecord(candidate=candidate, evidence=None, verdict=None, final_status='unevaluated'))
        return {'records': records, 'pool': [], 'stop_reason': stop_reason(state) or 'max_iterations'}


def should_continue(state):
    return 'finish' if stop_reason(state) else 'generate'


# ---------------------------------------------------------------- outer nodes
class LoadNode:
    """Node: exploration JSON -> PageBrief per selectable page (SEO_KEYWORD_MAX_PAGES), own domain, providers."""

    name = 'load'

    def __init__(self, workflow):
        self.workflow = workflow

    def __call__(self, state):
        begun = start('keyword research: load exploration JSON')
        limit = env_int('SEO_KEYWORD_MAX_PAGES', 25)
        reports, origin, pages, packet = PageSelector().select(state['reports_dir'], only=state.get('only_pages'),
                                                               limit=limit, site_url=state.get('site_url'))
        out_dir = state.get('out_dir') or str(reports.root / 'keywords')
        self.workflow.out_dir = out_dir   # the Jev judge logs its payloads under <out_dir>/jev/
        warnings = []
        try:
            providers = list(self.workflow.toolkit.available_providers() or [])
        except Exception as error:  # noqa: BLE001
            providers = []
            warnings.append(f'provider discovery failed: {type(error).__name__}: {error}')
        if not providers:
            banner = ('DEGRADED: no keyword data provider configured (DataForSEO / Google Ads / Search Console). Metrics are '
                      'unknown; keywords that need data are sent to REVIEW.')
            warnings.append(banner)
            log('!' * 20 + ' ' + banner + ' ' + '!' * 20)
        log(f'keyword research: {len(pages)} pages, origin={origin}, providers={providers or "none"}')
        done('keyword research: load exploration JSON', begun)
        return {'pages': pages, 'origin': origin, 'own_domain': domain_of(origin), 'site_packet': packet,
                'providers': providers, 'warnings': warnings, 'jev_mode': self.workflow.jev_mode(),
                'out_dir': out_dir}


class BusinessProfileNode:
    """Node: one business profile for the whole site (+ CLI competitors)."""

    name = 'business_profile'

    def __init__(self, workflow):
        self.workflow = workflow

    def __call__(self, state):
        begun = start('keyword research: business profile')
        market, warnings = state['market'], list(state.get('warnings') or [])
        try:
            business = self.workflow.generator.profile_business(state['site_packet'], market)
        except Exception as error:  # noqa: BLE001 - fall back to what the exploration already knows
            log(f'keyword research: profile_business failed ({type(error).__name__}: {error}); using a minimal profile')
            warnings.append(f'business profile failed ({type(error).__name__}); minimal profile from the exploration used')
            packet = state['site_packet']
            business = BusinessProfile(brand=(packet.get('brand_candidates') or [packet.get('domain') or 'site'])[0],
                                       site_type=packet.get('site_type_guess') or 'other', offering='')
        competitors = list(business.competitors or [])
        for item in state.get('cli_competitors') or []:
            domain = domain_of(item)
            if domain and domain not in competitors and domain != state.get('own_domain'):
                competitors.append(domain)
        business = replace(business, competitors=competitors, market=market)
        log(f'keyword research: business={business.brand!r} ({business.site_type}), competitors={competitors}')
        done('keyword research: business profile', begun)
        return {'business': business, 'warnings': warnings}


class CompetitorDiscoveryNode:
    """Node: per page, competitor URLs for its seed queries, their ranking keywords (reverse research), GSC queries."""

    name = 'competitor_discovery'

    def __init__(self, workflow):
        self.workflow = workflow

    def discover(self, page, state):
        toolkit, market, own = self.workflow.toolkit, state['market'], state.get('own_domain')
        business = state['business']
        seeds = seed_queries(page) + [s.lower() for s in (business.seed_topics or [])[:2] if s]
        errors, keywords, gsc = [], [], []
        try:
            urls = [u for u in toolkit.competitor_urls(seeds, market, own_domain=own, limit=self.workflow.competitor_urls)
                    or [] if domain_of(u) != own]
        except Exception as error:  # noqa: BLE001
            errors.append(f'competitor_urls: {type(error).__name__}: {str(error)[:200]}')
            urls = []
        for url in urls[:self.workflow.competitor_urls]:
            try:
                found = toolkit.competitor_keywords(url, market, limit=self.workflow.competitor_keyword_limit) or []
            except Exception as error:  # noqa: BLE001
                errors.append(f'competitor_keywords {url}: {type(error).__name__}: {str(error)[:200]}')
                continue
            keywords += [replace(c, page_id=page.page_id, source=Source.COMPETITOR_REVERSE,
                                 competitor_url=c.competitor_url or url) for c in found]
        try:
            gsc = [replace(c, page_id=page.page_id, source=Source.GSC)
                   for c in toolkit.page_queries(page.absolute_url or page.url, market) or []]
        except Exception as error:  # noqa: BLE001
            errors.append(f'page_queries: {type(error).__name__}: {str(error)[:200]}')
        evidence = {}
        ranked = relevance_order(admit(gsc + keywords, page.page_id, set()), page, {})[:self.workflow.competitor_enrich]
        if ranked:
            try:
                evidence = {normalize(k): v for k, v in (toolkit.enrich([c.keyword for c in ranked], market, page=page)
                                                         or {}).items()}
            except Exception as error:  # noqa: BLE001
                errors.append(f'enrich competitor keywords: {type(error).__name__}: {str(error)[:200]}')
        return page.page_id, {'urls': urls, 'keywords': keywords, 'gsc': gsc, 'seeds': seeds, 'errors': errors,
                              'evidence': evidence}

    def __call__(self, state):
        begun = start('keyword research: competitor discovery')
        pages = state['pages']
        with ThreadPoolExecutor(max_workers=self.workflow.concurrency) as pool:
            found = dict(pool.map(lambda page: self.discover(page, state), list(pages.values())))
        updated = {}
        for pid, page in pages.items():
            queries = [c.keyword for c in found[pid]['gsc']]
            updated[pid] = replace(page, existing_queries=list(dict.fromkeys(list(page.existing_queries) + queries)))
            log(f"keyword {pid}: {len(found[pid]['urls'])} competitor URLs, {len(found[pid]['keywords'])} reverse keywords, "
                f"{len(queries)} GSC queries" + (f", {len(found[pid]['errors'])} provider errors" if found[pid]['errors'] else ''))
        done('keyword research: competitor discovery', begun)
        return {'competitors': found, 'pages': updated}


class ResearchLoopNode:
    """Node: run the compiled page loop for every page (SEO_KEYWORD_CONCURRENCY pages at a time)."""

    name = 'research_loop'

    def __init__(self, workflow):
        self.workflow = workflow

    def run_page(self, page, state, loop):
        competitors = (state.get('competitors') or {}).get(page.page_id) or {}
        targets = state['targets']
        seen = set()
        pool = admit(list(competitors.get('gsc') or []) + list(competitors.get('keywords') or []), page.page_id, seen)
        initial = {'page': page, 'business': state['business'], 'market': state['market'], 'targets': targets,
                   'seeds': competitors.get('seeds') or seed_queries(page), 'competitor_keywords': pool, 'iteration': 0,
                   'site_pages': [{'page_id': p.page_id, 'url': p.url, 'title': p.title} for p in state['pages'].values()],
                   'pool': pool, 'seen': seen, 'evidence': dict(competitors.get('evidence') or {}), 'records': [],
                   'fresh_accepts': [], 'batch': [],
                   'errors': list(competitors.get('errors') or [])}
        try:
            final = loop.invoke(initial, {'recursion_limit': targets.max_iterations * (len(PAGE_LOOP_NODES) + 1) + 10})
        except Exception as error:  # noqa: BLE001 - one page must not stop the stage
            log(f'keyword {page.page_id}: page loop failed: {type(error).__name__}: {error}')
            return page.page_id, {'records': [], 'iterations': 0, 'stop_reason': 'error', 'status': 'failed',
                                  'competitor_urls': competitors.get('urls') or [],
                                  'error': type(error).__name__ + ': ' + str(error)[:300] + '\n' + traceback.format_exc(limit=3)[-600:]}
        log(f"keyword {page.page_id}: stopped after {final.get('iteration', 0)} iterations ({final.get('stop_reason')})")
        return page.page_id, {'records': final.get('records') or [], 'iterations': final.get('iteration', 0),
                              'stop_reason': final.get('stop_reason'), 'status': 'done',
                              'competitor_urls': competitors.get('urls') or [], 'errors': final.get('errors') or []}

    def __call__(self, state):
        begun = start('keyword research: research loop')
        loop = self.workflow.build_page_loop()
        with ThreadPoolExecutor(max_workers=self.workflow.concurrency) as pool:
            results = dict(pool.map(lambda page: self.run_page(page, state, loop), list(state['pages'].values())))
        done('keyword research: research loop', begun)
        return {'page_results': results}


class MapNode:
    """Node: site-wide mapping (KeywordMapper)."""

    name = 'map'

    def __init__(self, workflow):
        self.workflow = workflow

    def __call__(self, state):
        begun = start('keyword research: map keywords to pages')
        mapping = KeywordMapper(self.workflow.judge).map(state['pages'], state.get('page_results') or {},
                                                         business=state.get('business'), targets=state['targets'])
        done('keyword research: map keywords to pages', begun)
        return {'mapping': mapping}


class WriteNode:
    """Node: write seo_reports/keywords/."""

    name = 'write'

    def __init__(self, workflow):
        self.workflow = workflow

    def __call__(self, state):
        state = {**state, 'costs': self.workflow.costs(), 'judge_filters': self.workflow.judge_filters(),
                 'generated_at': now()}
        written = KeywordOutputWriter().write(state['out_dir'], state)
        log('keyword research: wrote ' + str(len(written['files'])) + ' files to ' + written['out_dir'])
        return {'written': written, 'costs': state['costs'], 'judge_filters': state['judge_filters'],
                'generated_at': state['generated_at']}


# ---------------------------------------------------------------- workflow
def default_toolkit():
    from seo_agents.keyword_research.providers.research_toolkit import ProviderResearchToolkit
    return ProviderResearchToolkit.from_env()


def default_generator():
    from seo_agents.keyword_research.generation.keyword_generator import LlmKeywordGenerator
    return LlmKeywordGenerator()


def default_judge(evidence_dir=None):
    from seo_agents.keyword_research.judge.keyword_judge import JevKeywordJudge
    return JevKeywordJudge.from_env(evidence_dir=evidence_dir)


class KeywordResearchWorkflow:
    """load -> business_profile -> competitor_discovery -> research_loop (page subgraph) -> map -> write.

    toolkit / generator / judge implement contracts.ResearchToolkit / KeywordGenerator / KeywordJudge; when omitted
    the provider / LLM / Jev implementations are built on first use.
    """

    def __init__(self, toolkit=None, generator=None, judge=None, *, targets=None, concurrency=None):
        self._toolkit, self._generator, self._judge = toolkit, generator, judge
        self.targets = targets
        self.concurrency = max(1, concurrency or env_int('SEO_KEYWORD_CONCURRENCY', 3))
        self.competitor_urls = env_int('SEO_KEYWORD_COMPETITOR_URLS', 3)
        self.competitor_keyword_limit = env_int('SEO_KEYWORD_COMPETITOR_KEYWORDS', 100)
        self.expand_limit = env_int('SEO_KEYWORD_EXPAND_LIMIT', 50)
        self.question_limit = env_int('SEO_KEYWORD_QUESTION_LIMIT', 30)
        self.competitor_enrich = env_int('SEO_KEYWORD_COMPETITOR_ENRICH', 30)
        self.out_dir = None
        self._accepted, self._accepted_lock = {}, Lock()
        self._generator_params = None

    # ------------------------------------------------------------ site-wide accepted keywords (shared by page threads)
    def register_accepted(self, page_id, records):
        with self._accepted_lock:
            self._accepted[page_id] = list(records)

    def site_accepted(self, page_id, own):
        """This page's accepted records + every other page's accepts so far (the generator avoids their intents)."""
        with self._accepted_lock:
            others = [r for pid, rows in self._accepted.items() if pid != page_id for r in rows]
        return list(own) + others

    def generator_extras(self, **extras):
        """Optional generate() kwargs (competitor_evidence, site_pages) the generator in use accepts."""
        if self._generator_params is None:
            try:
                params = inspect.signature(self.generator.generate).parameters
                self._generator_params = {'*'} if any(p.kind == p.VAR_KEYWORD for p in params.values()) else set(params)
            except (TypeError, ValueError):
                self._generator_params = set()
        return {k: v for k, v in extras.items() if '*' in self._generator_params or k in self._generator_params}

    # ------------------------------------------------------------ components (lazy)
    @property
    def toolkit(self):
        if self._toolkit is None:
            self._toolkit = default_toolkit()
        return self._toolkit

    @property
    def generator(self):
        if self._generator is None:
            self._generator = default_generator()
        return self._generator

    @property
    def judge(self):
        if self._judge is None:
            self._judge = default_judge(self.out_dir)
        return self._judge

    def jev_mode(self):
        mode = getattr(self.judge, 'mode', None)
        return mode() if callable(mode) else mode

    def judge_filters(self):
        filters = getattr(self.judge, 'filters', None)
        filters = filters() if callable(filters) else filters
        return filters if isinstance(filters, dict) else {}

    def costs(self):
        """{'toolkit'|'generator'|'judge': usage} from components that expose `usage()` (or a `usage` dict)."""
        out = {}
        for name, component in (('toolkit', self._toolkit), ('generator', self._generator), ('judge', self._judge)):
            usage = getattr(component, 'usage', None)
            try:
                usage = usage() if callable(usage) else usage
            except Exception:  # noqa: BLE001 - cost reporting is best effort
                usage = None
            if usage:
                out[name] = usage
        return out

    # ------------------------------------------------------------ graphs
    def build_page_loop(self):
        graph = StateGraph(PageLoopState)
        for node in (GenerateNode(self), ExpandNode(self), EnrichNode(self), JudgeNode(self), FinishNode()):
            graph.add_node(node.name, node)
        graph.add_edge(START, 'generate')
        graph.add_edge('generate', 'expand')
        graph.add_edge('expand', 'enrich')
        graph.add_edge('enrich', 'judge')
        graph.add_conditional_edges('judge', should_continue, {'generate': 'generate', 'finish': 'finish'})
        graph.add_edge('finish', END)
        return graph.compile()

    def build_graph(self):
        graph = StateGraph(KeywordResearchState)
        previous = START
        for node in (LoadNode(self), BusinessProfileNode(self), CompetitorDiscoveryNode(self), ResearchLoopNode(self),
                     MapNode(self), WriteNode(self)):
            graph.add_node(node.name, node)
            graph.add_edge(previous, node.name)
            previous = node.name
        graph.add_edge(previous, END)
        return graph.compile()

    def run(self, reports_dir, *, out_dir=None, site_url=None, only_pages=None, competitors=None, market=None,
            max_iterations=None):
        self._accepted = {}
        if self._generator is None and not os.getenv('OPENROUTER_API_KEY'):
            raise KeywordResearchConfigError('OPENROUTER_API_KEY is not set: the keyword stage needs a model to profile the '
                                             'business and propose keywords. Set it in .env (no keywords are invented '
                                             'without one).')
        begun = start('keyword research workflow')
        state = self.build_graph().invoke({
            'reports_dir': str(reports_dir), 'out_dir': out_dir, 'site_url': site_url, 'only_pages': only_pages or [],
            'cli_competitors': competitors or [], 'market': market or market_from(),
            'targets': targets_from(self.targets, max_iterations)})
        done('keyword research workflow', begun)
        return state


def build_graph(toolkit=None, generator=None, judge=None):
    return KeywordResearchWorkflow(toolkit, generator, judge).build_graph()


def run_keyword_research(reports_dir, **kwargs):
    """Function form of `KeywordResearchWorkflow().run(reports_dir, ...)` with the default components."""
    components = {k: kwargs.pop(k) for k in ('toolkit', 'generator', 'judge') if k in kwargs}
    return KeywordResearchWorkflow(**components).run(reports_dir, **kwargs)
