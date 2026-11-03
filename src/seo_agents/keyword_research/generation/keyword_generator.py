"""LlmKeywordGenerator: the `KeywordGenerator` contract backed by the keyword-research skills.

profile_business(site_packet, market)   -> BusinessProfile           (skill: business-profile)
generate(page, business, needs=..., ...) -> list[KeywordCandidate]    (skill: keyword-ideation)

The model proposes keywords + hypotheses only; it never outputs metrics. Competitor-keyword metrics (volume, kd,
position) are shown to it as evidence of demand and language. Feedback from the judge (accepted keywords, rejected
ones with rejection codes and reasons) is sent every iteration and every accepted/rejected keyword is blocked by the
validator, so a rejected keyword can never come back.
"""

from __future__ import annotations

import threading

from collections import Counter

from seo_agents.keyword_research.contracts import (BusinessProfile, KeywordCandidate, KeywordEvidence, KeywordRecord,
                                                   Market, PageBrief, to_dict)
from seo_agents.keyword_research.generation.business_profiler import BusinessProfiler
from seo_agents.keyword_research.generation.candidate_validation import (clean_needs, normalize_keyword,
                                                                         validate_candidates)
from seo_agents.keyword_research.generation.llm_writer import KeywordWriter, KeywordWriterFailed
from seo_agents.utils.step_logger import log

SKILL = 'keyword-ideation'
MAX_HEADINGS = 30
MAX_COPY_ITEMS = 40
MAX_COPY_CHARS = 400
MAX_COMPETITOR_KEYWORDS = 60
MAX_SITE_ACCEPTED = 120
MAX_OTHER_PAGES = 60


def record_keyword(record):
    return normalize_keyword(record.candidate.keyword)


def record_page(record):
    return record.mapped_page_id or record.candidate.page_id


def site_pages_from_packet(site_packet):
    """[{'page_id', 'url', 'title'}] from a site packet's `pages` list (tolerant of missing keys)."""
    pages = []
    for item in (site_packet or {}).get('pages') or []:
        if isinstance(item, dict) and item.get('url'):
            pages.append({'page_id': item.get('page_id') or item.get('id'), 'url': item['url'],
                          'title': item.get('title')})
    return pages


class LlmKeywordGenerator:
    """KeywordGenerator implementation. `model_factory(tier, label[, temperature])` is injectable for tests."""

    @property
    def last_meta(self) -> dict:
        """Metadata of this thread's most recent call (thread-local, so parallel page loops never read each other's)."""
        return getattr(self._local, 'meta', {})

    @last_meta.setter
    def last_meta(self, value: dict):
        self._local.meta = value

    def __init__(self, writer: KeywordWriter | None = None, *, model_factory=None, attempts=None,
                 site_pages: list[dict] | None = None):
        self.writer = writer or KeywordWriter(model_factory=model_factory, attempts=attempts)
        self.profiler = BusinessProfiler(self.writer)
        self.site_pages = list(site_pages or [])
        self._local = threading.local()  # last_meta per thread: pages run in parallel on one generator

    # ------------------------------------------------------------ business profile
    def profile_business(self, site_packet: dict, market: Market) -> BusinessProfile:
        if not self.site_pages:
            self.site_pages = site_pages_from_packet(site_packet)
        profile = self.profiler.profile(site_packet, market)
        self.last_meta = self.profiler.last_meta
        return profile

    # ------------------------------------------------------------ candidates
    def build_packet(self, page, business, *, needs, competitor_keywords, accepted, rejected, iteration, limit,
                     competitor_evidence=None, site_pages=None):
        competitor_evidence = competitor_evidence or {}
        url_of = {p.get('page_id'): p.get('url') for p in (site_pages or self.site_pages)}
        on_page = [r for r in accepted if record_page(r) == page.page_id]
        elsewhere = [r for r in accepted if record_page(r) != page.page_id]
        rejected_rows = [{'keyword': r.candidate.keyword, 'kind': r.candidate.kind.value,
                          'codes': [c.value for c in (r.verdict.rejection_codes if r.verdict else [])],
                          'reasons': list(r.verdict.reasons if r.verdict else [])} for r in rejected]
        competitor_rows = []
        for candidate in list(competitor_keywords or [])[:MAX_COMPETITOR_KEYWORDS]:
            evidence: KeywordEvidence | None = competitor_evidence.get(candidate.keyword)
            row = {'keyword': candidate.keyword, 'competitor_url': candidate.competitor_url}
            if evidence is not None:
                for name, alias in (('search_volume', 'volume'), ('keyword_difficulty', 'kd'),
                                    ('competitor_position', 'position'), ('provider_intent', 'intent')):
                    value = evidence.value(name)
                    if value is not None:
                        row[alias] = value
            competitor_rows.append(row)
        business_view = to_dict(business)
        market = business_view.pop('market', {})
        return {
            'iteration': iteration,
            'limit': limit,
            'needs': needs,
            'market': {'country': market.get('country'), 'language': market.get('language_name')},
            'page': {'page_id': page.page_id, 'url': page.url, 'page_type': page.page_type, 'lang': page.lang,
                     'title': page.title, 'description': page.description, 'headings': page.headings[:MAX_HEADINGS],
                     'copy': [str(c)[:MAX_COPY_CHARS] for c in page.copy[:MAX_COPY_ITEMS]],
                     'image_alts': [i.get('alt') for i in page.images if isinstance(i, dict) and i.get('alt')][:20],
                     'existing_queries': page.existing_queries[:30]},
            'business': business_view,
            'competitor_keywords': competitor_rows,
            'accepted_on_page': [{'keyword': r.candidate.keyword, 'kind': r.final_status} for r in on_page],
            'rejected_on_page': rejected_rows,
            'rejection_summary': dict(Counter(code for row in rejected_rows for code in row['codes'])),
            'accepted_elsewhere': [{'keyword': r.candidate.keyword, 'page_url': url_of.get(record_page(r), record_page(r))}
                                   for r in elsewhere][:MAX_SITE_ACCEPTED],
            'other_pages': [{'url': p.get('url'), 'title': p.get('title')} for p in (site_pages or self.site_pages)
                            if p.get('page_id') != page.page_id and p.get('url') != page.url][:MAX_OTHER_PAGES],
        }

    def generate(self, page: PageBrief, business: BusinessProfile, *, needs: dict[str, int],
                 competitor_keywords: list[KeywordCandidate], accepted: list[KeywordRecord],
                 rejected: list[KeywordRecord], iteration: int, limit: int,
                 competitor_evidence: dict[str, KeywordEvidence] | None = None,
                 site_pages: list[dict] | None = None) -> list[KeywordCandidate]:
        """Validated candidates for one page and iteration ([] when nothing is needed or every attempt failed;
        the failure details are in `last_meta['errors']`)."""
        needs = clean_needs(needs)
        if not any(needs.values()) or limit <= 0:
            self.last_meta = {'skill': SKILL, 'skipped': 'nothing needed'}
            return []
        blocked = {record_keyword(r) for r in list(accepted) + list(rejected)}
        packet = self.build_packet(page, business, needs=needs, competitor_keywords=competitor_keywords,
                                   accepted=accepted, rejected=rejected, iteration=iteration, limit=limit,
                                   competitor_evidence=competitor_evidence, site_pages=site_pages)
        validate = lambda answer: validate_candidates(  # noqa: E731
            answer, page=page, business=business, needs=needs, competitor_keywords=competitor_keywords,
            blocked=blocked, limit=limit, iteration=iteration)
        try:
            batch, meta = self.writer.write(SKILL, packet, validate)
        except KeywordWriterFailed as error:
            log(f'keyword_research: no candidates for {page.url} (iteration {iteration}): {error}')
            self.last_meta = {'skill': SKILL, 'failed': True, 'errors': error.errors}
            return []
        self.last_meta = {**meta, 'page_intent': batch.page_intent, 'dropped': batch.dropped}
        return batch.candidates
