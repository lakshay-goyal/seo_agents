"""KeywordOutputWriter: seo_reports/keywords/

    keyword_research.csv   every candidate ever generated or expanded (accepted, rejected, review, unevaluated), one row
                           each; unknown metrics are empty cells (never 0); rejection codes + reasons say why not used
    keyword_map.json       {schema, market, business, generated_at, filters, pages: {page_id: PageKeywordPlan}}  (on-page input)
    pages/<page_id>.json   brief, plan, competitor data, every record with full evidence + verdict (audit)
    summary.json/.md       counts per decision / code / stage, complete vs partial pages, providers, Jev mode, cost
"""

from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path

from seo_agents.keyword_research.contracts import SCHEMA_VERSION, Decision, RejectionCode, to_dict
from seo_agents.keyword_research.mapping.keyword_mapper import (INTENT_KEYS, JOURNEY_KEYS, SERP_INTENT_KEYS, judgment,
                                                                metric)

BASE_COLUMNS = (
    'page_id', 'page_url', 'keyword', 'kind', 'tail', 'source', 'competitor_url', 'iteration', 'hypothesized_intent',
    'judged_user_intent', 'serp_intent', 'journey_stage', 'search_volume', 'search_volume_ads', 'keyword_difficulty', 'cpc',
    'bid_low', 'bid_high', 'ads_competition', 'ads_competition_level', 'provider_intent', 'trend_direction', 'seasonal_peak_months', 'top_regions', 'serp_features',
    'people_also_ask', 'click_opportunity', 'serp_authority_share', 'gsc_impressions', 'gsc_position', 'content_alignment',
)
TAIL_COLUMNS = (
    'composite_score', 'decision', 'final_status', 'mapped_page_url', 'rejection_codes', 'rejection_reasons', 'filter_stage',
    'providers', 'retrieved_at', 'market', 'rationale',
)
CSV_COLUMNS = BASE_COLUMNS + ('factor_<name> ...',) + TAIL_COLUMNS   # documentation: one factor_* column per factor
PLAIN_METRICS = ('search_volume', 'search_volume_ads', 'keyword_difficulty', 'cpc', 'bid_low', 'bid_high', 'ads_competition',
                 'ads_competition_level', 'provider_intent',
                 'trend_direction', 'click_opportunity', 'serp_authority_share', 'gsc_impressions', 'gsc_position',
                 'content_alignment')


def cell(value):
    """CSV cell: None -> '' (unknown), enums -> value, lists -> ';'-joined, floats rounded."""
    if value is None:
        return ''
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, float):
        return f'{value:.4f}'.rstrip('0').rstrip('.') if value == value else ''
    if isinstance(value, (list, tuple, set)):
        return ';'.join(cell(v) for v in value if v is not None)
    return str(value)


def filter_stage(record):
    """Where the keyword was filtered out: gate | jev | quota | mapping | not_evaluated ('' when used)."""
    verdict = record.verdict
    if record.final_status == 'unevaluated':
        return 'not_evaluated'
    if verdict is None:
        return 'jev'
    codes = set(verdict.rejection_codes)
    if RejectionCode.CANNIBALIZATION in codes:
        return 'mapping'
    if verdict.decision == Decision.ACCEPT:
        return ''
    if codes and codes <= {RejectionCode.QUOTA_FULL, RejectionCode.DUPLICATE}:
        return 'quota'
    if verdict.decision == Decision.REJECT and verdict.composite_score is None:
        return 'gate'
    return 'jev'


def providers_of(evidence):
    if evidence is None:
        return []
    names = [m.provenance.provider for m in evidence.metrics.values() if m.provenance is not None and m.value is not None]
    if evidence.serp is not None and evidence.serp.provenance is not None:
        names.append(evidence.serp.provenance.provider)
    return sorted(set(names))


def retrieved_at(evidence):
    if evidence is None:
        return None
    stamps = [m.provenance.retrieved_at for m in evidence.metrics.values() if m.provenance is not None and m.value is not None]
    return max(stamps) if stamps else None


def top_regions(evidence, limit=3):
    rows = metric_value(evidence, 'region_interest') or []
    rows = [r for r in rows if isinstance(r, dict) and r.get('region')]
    rows.sort(key=lambda r: -(r.get('value') or 0))
    return [r['region'] for r in rows[:limit]]


def metric_value(evidence, name):
    return evidence.value(name) if evidence is not None else None


def market_label(market):
    if market is None:
        return None
    return f'{market.country}/{market.language_code}/{market.location_code}'


def json_default(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (set, tuple)):
        return list(value)
    return str(value)


def dump(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=json_default) + '\n', encoding='utf-8')


class KeywordOutputWriter:
    """Tool: write seo_reports/keywords/ from the workflow state."""

    def write(self, out_dir, state):
        out = Path(out_dir)
        (out / 'pages').mkdir(parents=True, exist_ok=True)
        mapping = state['mapping']
        pages = state.get('pages') or {}
        generated_at = state.get('generated_at') or datetime.now(timezone.utc).isoformat(timespec='seconds')
        files = []
        files.append(self.write_csv(out / 'keyword_research.csv', mapping.records, pages, state.get('market')))
        keyword_map = {
            'schema': SCHEMA_VERSION, 'market': to_dict(state['market']) if state.get('market') else None,
            'business': to_dict(state['business']) if state.get('business') else None, 'generated_at': generated_at,
            'filters': {'targets': to_dict(state['targets']) if state.get('targets') else None, 'mapping': mapping.filters,
                        'judge': state.get('judge_filters') or {}},
            'pages': {pid: to_dict(plan) for pid, plan in mapping.plans.items()},
        }
        dump(out / 'keyword_map.json', keyword_map)
        files.append(str(out / 'keyword_map.json'))
        results = state.get('page_results') or {}
        competitors = state.get('competitors') or {}
        for pid, page in pages.items():
            path = out / 'pages' / (pid + '.json')
            rows = [r for r in mapping.records if r.candidate.page_id == pid]
            dump(path, {'schema': SCHEMA_VERSION, 'page': to_dict(page),
                        'plan': to_dict(mapping.plans[pid]) if pid in mapping.plans else None,
                        'loop': {k: (results.get(pid) or {}).get(k) for k in ('iterations', 'stop_reason', 'status', 'error')},
                        'competitors': {'urls': (competitors.get(pid) or {}).get('urls') or [],
                                        'errors': (competitors.get(pid) or {}).get('errors') or []},
                        'cannibalization': [c for c in mapping.cannibalization if pid in (c['page_id'], c['winner_page_id'])],
                        'records': [to_dict(r) for r in rows]})
            files.append(str(path))
        summary = self.summary(state, mapping, generated_at)
        dump(out / 'summary.json', summary)
        (out / 'summary.md').write_text(self.summary_markdown(summary), encoding='utf-8')
        files += [str(out / 'summary.json'), str(out / 'summary.md')]
        return {'out_dir': str(out), 'files': files, 'summary': summary}

    # ------------------------------------------------------------ CSV
    def write_csv(self, path, records, pages, market):
        factors = sorted({name for r in records if r.verdict is not None for name in (r.verdict.factor_scores or {})})
        columns = list(BASE_COLUMNS) + ['factor_' + f for f in factors] + list(TAIL_COLUMNS)
        with open(path, 'w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=columns, quoting=csv.QUOTE_MINIMAL, extrasaction='ignore')
            writer.writeheader()
            for record in records:
                writer.writerow({k: cell(v) for k, v in self.row(record, pages, market, factors).items()})
        return str(path)

    def row(self, record, pages, market, factors):
        c, e, v = record.candidate, record.evidence, record.verdict
        page = pages.get(c.page_id)
        mapped = pages.get(record.mapped_page_id) if record.mapped_page_id else None
        serp = e.serp if e is not None else None
        row = {
            'page_id': c.page_id, 'page_url': page.url if page else None, 'keyword': c.keyword, 'kind': c.kind,
            'tail': c.tail, 'source': c.source, 'competitor_url': c.competitor_url, 'iteration': c.iteration,
            'hypothesized_intent': c.hypothesized_intent, 'judged_user_intent': judgment(v, INTENT_KEYS),
            'serp_intent': judgment(v, SERP_INTENT_KEYS), 'journey_stage': judgment(v, JOURNEY_KEYS),
            'seasonal_peak_months': metric_value(e, 'seasonal_peak_months'), 'top_regions': top_regions(e) or None,
            'serp_features': serp.features if serp is not None else None,
            'people_also_ask': serp.people_also_ask if serp is not None else None,
            'composite_score': v.composite_score if v is not None else None,
            'decision': v.decision if v is not None else None, 'final_status': record.final_status,
            'mapped_page_url': mapped.url if mapped else None,
            'rejection_codes': list(v.rejection_codes) if v is not None and v.decision != Decision.ACCEPT else None,
            'rejection_reasons': self.reasons(record), 'filter_stage': filter_stage(record) or None,
            'providers': providers_of(e) or None, 'retrieved_at': retrieved_at(e), 'market': market_label(market),
            'rationale': c.rationale or None,
        }
        for name in PLAIN_METRICS:
            row[name] = metric(record, name)
        for name in factors:
            row['factor_' + name] = (v.factor_scores or {}).get(name) if v is not None else None
        return row

    @staticmethod
    def reasons(record):
        verdict = record.verdict
        if record.final_status == 'unevaluated':
            return ['not evaluated: the page loop stopped (quota met or iteration budget spent) before this candidate '
                    'was judged']
        if verdict is None or verdict.decision == Decision.ACCEPT:
            return None
        return list(verdict.reasons) or None

    # ------------------------------------------------------------ summary
    @staticmethod
    def summary(state, mapping, generated_at):
        decisions, finals, codes, stages, sources = Counter(), Counter(), Counter(), Counter(), Counter()
        for record in mapping.records:
            verdict = record.verdict
            decisions[verdict.decision.value if verdict is not None else 'none'] += 1
            finals[record.final_status] += 1
            sources[record.candidate.source.value] += 1
            stages[filter_stage(record) or 'used'] += 1
            if verdict is not None and verdict.decision != Decision.ACCEPT:
                for code in verdict.rejection_codes:
                    codes[code.value] += 1
        statuses = Counter(plan.status for plan in mapping.plans.values())
        page_errors = {pid: list(r.get('errors') or []) + ([r['error']] if r.get('error') else [])
                       for pid, r in (state.get('page_results') or {}).items()}
        page_errors = {pid: errors for pid, errors in page_errors.items() if errors}
        generator_failures = sum(1 for errors in page_errors.values() for e in errors if e.startswith('generate#'))
        providers = list(state.get('providers') or [])
        banners = list(state.get('warnings') or [])
        return {
            'schema': SCHEMA_VERSION, 'generated_at': generated_at, 'market': market_label(state.get('market')),
            'pages': len(mapping.plans), 'pages_complete': statuses.get('complete', 0),
            'pages_partial': statuses.get('partial', 0), 'pages_failed': statuses.get('failed', 0),
            'pages_by_status': {pid: plan.status for pid, plan in mapping.plans.items()},
            'candidates': len(mapping.records), 'decisions': dict(decisions), 'final_status': dict(finals),
            'rejection_codes': dict(codes.most_common()), 'filter_stages': dict(stages), 'sources': dict(sources),
            'cannibalization': len(mapping.cannibalization), 'providers_available': providers,
            'degraded': not providers, 'jev_mode': state.get('jev_mode'), 'costs': state.get('costs') or {},
            'warnings': banners, 'generator_failures': generator_failures, 'page_errors': page_errors,
            'stop_reasons': dict(Counter((r or {}).get('stop_reason') or 'unknown'
                                         for r in (state.get('page_results') or {}).values())),
        }

    @staticmethod
    def summary_markdown(summary):
        lines = ['# Keyword research summary', '']
        if summary['degraded']:
            lines += ['> **DEGRADED RUN: no keyword data provider is configured.** Volumes, difficulty, SERP and trends are '
                      'unknown, so keywords that need data are marked `review`, not accepted. Configure DataForSEO / Google Ads '
                      '/ Search Console credentials (.env) and rerun.', '']
        for warning in summary['warnings']:
            lines.append('> ' + warning)
        if summary['warnings']:
            lines.append('')
        lines += [f"- Market: `{summary['market']}`  |  generated {summary['generated_at']}",
                  f"- Providers: {', '.join(summary['providers_available']) or 'none'}  |  Jev mode: {summary['jev_mode'] or 'unknown'}",
                  f"- Pages: {summary['pages']} ({summary['pages_complete']} complete, {summary['pages_partial']} partial, "
                  f"{summary['pages_failed']} failed)",
                  f"- Candidates: {summary['candidates']}  |  cannibalization moves: {summary['cannibalization']}  |  "
                  f"generator failures: {summary['generator_failures']}  |  pages with provider/model errors: "
                  f"{len(summary['page_errors'])}",
                  f"- Loop stops: {', '.join(f'{k}={v}' for k, v in sorted(summary['stop_reasons'].items())) or 'none'}", '',
                  '| final status | count |', '| --- | --- |']
        lines += [f'| {k} | {v} |' for k, v in sorted(summary['final_status'].items())]
        lines += ['', '| rejection code | count |', '| --- | --- |']
        lines += [f'| {k} | {v} |' for k, v in summary['rejection_codes'].items()] or ['| (none) | 0 |']
        lines += ['', '| filter stage | count |', '| --- | --- |']
        lines += [f'| {k} | {v} |' for k, v in sorted(summary['filter_stages'].items())]
        if summary['costs']:
            lines += ['', '## Cost estimates', '', '```json', json.dumps(summary['costs'], indent=2, default=str), '```']
        lines += ['', 'Full rows: `keyword_research.csv`; page plans (on-page input): `keyword_map.json`; audit: `pages/`.']
        return '\n'.join(lines) + '\n'
