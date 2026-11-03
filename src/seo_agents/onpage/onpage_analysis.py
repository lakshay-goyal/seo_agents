"""On-page analysis: the LangGraph workflow that runs every on-page check, one at a time (pages in parallel inside a check).

    START
      -> load              ExplorationReportsLoader + PageContextBuilder (reads only seo_reports/ JSON, incl. the optional
                           keywords/keyword_map.json from keyword research -> ctx['keyword_plan'])
      -> intent            SearchIntentCheck          (model, small)
      -> keyword_plan      KeywordPlanCheck
      -> page_plan         PagePlanCheck              (model, mid)
      -> media_links       MediaLinksCheck            (model, small)
      -> advice            EeatAdviceCheck            (model, small, optional)
      -> media_technical   MediaTechnicalCheck
      -> accessibility     LinkAccessibilityCheck
      -> technical         TechnicalOnpageCheck
      -> structured_data   StructuredDataCheck
      -> social_meta       SocialMetaCheck
      -> fact_check        FactCheckFreshnessCheck
      -> head_hygiene      HeadHygieneCheck
      -> heading_structure HeadingStructureCheck
      -> content_quality   ContentQualityCheck
      -> link_hygiene      LinkHygieneCheck
      -> indexability      IndexabilityCheck
      -> hreflang_i18n     HreflangI18nCheck
      -> security          SecurityCheck
      -> performance       PerformanceCheck
      -> crawlability      CrawlabilityCheck
      -> qa                QaConsolidationCheck       (scope gate, dedup, the before/after checklist)
      -> site              SiteLevelBuilder
      -> write             OnpageOutputWriter -> seo_reports/onpage/
    END

The check order is checks/checklist.py (CHECKS). A failed check is recorded with its validator errors; later checks
that depend on it are skipped for that page, others still run. Model tiers: lib/models.py; tools: lib/tool_mapping.py.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
import os
import traceback
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from seo_agents.utils.state_reducers import merge_page_results
from seo_agents.utils.step_logger import done, log, start
from seo_agents.onpage.checks.check_helpers import result
from seo_agents.onpage.checks.checklist import CHECKS, DEPENDS
from seo_agents.onpage.output.onpage_output_writer import OnpageOutputWriter
from seo_agents.onpage.output.site_level_builder import SiteLevelBuilder
from seo_agents.onpage.page_context.page_context_builder import PageContextBuilder
from seo_agents.onpage.reports.exploration_reports import ExplorationReportsLoader
from seo_agents.onpage.writer.content_writer import ContentWriter, WriterFailed


class OnpageState(TypedDict, total=False):
    reports_dir: str
    out_dir: str
    site_url: str
    run_date: str
    only_pages: list
    reports_meta: dict
    contexts: dict
    results: Annotated[dict, merge_page_results]
    site: dict
    written: dict


def selectable(page):
    """Real pages only: no route templates, no non-page rows, no 4xx/5xx responses."""
    status = (page.get('http') or {}).get('http_status')
    return page.get('instance_status') != 'template' and page.get('kind', 'page') == 'page' and not (isinstance(status, int) and status >= 400)


# ---------------------------------------------------------------- graph nodes
class LoadReportsNode:
    """Node: load the exploration JSON and build one page context per selected page (SEO_ONPAGE_MAX_PAGES)."""

    name = 'load'

    def __call__(self, state):
        begun = start('onpage: load exploration JSON')
        loader = ExplorationReportsLoader()
        reports = loader.load(state['reports_dir'])
        origin = loader.site_origin(reports, state.get('site_url'))
        run_date = state.get('run_date') or date.today().isoformat()
        limit = int(os.getenv('SEO_ONPAGE_MAX_PAGES', '50'))
        only = set(state.get('only_pages') or [])
        ids = [r['id'] for r in reports.page_rows() if r['id'] in reports.pages and selectable(reports.pages[r['id']])
               and (not only or r['id'] in only or r.get('url') in only)][:limit]
        builder = PageContextBuilder()
        contexts = {pid: builder.build(reports, pid, origin=origin, run_date=run_date, site_url=state.get('site_url')) for pid in ids}
        # exactly one selected page emits site-wide advice: the home page when selected, else the first selected page
        owner = next((pid for pid in ids if contexts[pid].get('is_home')), ids[0] if ids else None)
        for pid, ctx in contexts.items():
            ctx['site_items_owner'] = pid == owner
        log(f'onpage: {len(contexts)} pages selected (origin={origin})')
        done('onpage: load exploration JSON', begun)
        meta = {'site_model_dir': str(reports.site_model_dir), 'schema': reports.index.get('schema'),
                'snapshot_sha256': (reports.index.get('repo') or {}).get('snapshot_sha256'),
                'framework': (reports.index.get('repo') or {}).get('framework'), 'origin': origin,
                'keyword_map': {k: reports.keyword_map.get(k) for k in ('path', 'schema', 'generated_at', 'market')}
                if reports.keyword_map else None,
                'keyword_plans_used': sum(1 for c in contexts.values() if c.get('keyword_plan'))}
        return {'contexts': contexts, 'run_date': run_date, 'reports_meta': meta,
                'out_dir': state.get('out_dir') or str(reports.root / 'onpage')}


class CheckNode:
    """Node: run one OnpageCheck for every selected page (SEO_ONPAGE_CONCURRENCY pages at a time)."""

    def __init__(self, check, writer):
        self.check, self.writer = check, writer
        self.name = check.name

    def run_page(self, page_id, contexts, results):
        name = self.name
        prior = results.get(page_id, {})
        blocked = [d for d in DEPENDS.get(name, []) if (prior.get(d) or {}).get('status') != 'done']
        if blocked:
            return page_id, result('skipped', data={'reason': 'depends on failed/skipped: ' + ', '.join(blocked)})
        try:
            return page_id, self.check.run(contexts[page_id], prior, self.writer)
        except WriterFailed as error:
            return page_id, result('failed', errors=error.errors)
        except Exception as error:  # noqa: BLE001 - one page/check must not stop the workflow
            log(f'onpage {name} {page_id}: {type(error).__name__}: {error}')
            return page_id, result('failed', errors=[type(error).__name__ + ': ' + str(error)[:300],
                                                     traceback.format_exc(limit=3)[-600:]])

    def __call__(self, state):
        name = self.name
        begun = start('onpage check: ' + name)
        contexts, results = state['contexts'], state.get('results') or {}
        workers = max(1, int(os.getenv('SEO_ONPAGE_CONCURRENCY', '4')))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            outcomes = dict(pool.map(lambda page_id: self.run_page(page_id, contexts, results), list(contexts)))
        counts = {}
        for res in outcomes.values():
            counts[res['status']] = counts.get(res['status'], 0) + 1
        log(f'onpage check {name}: ' + ', '.join(f'{k}={v}' for k, v in sorted(counts.items())))
        done('onpage check: ' + name, begun)
        return {'results': {pid: {name: res} for pid, res in outcomes.items()}}


class SiteLevelNode:
    """Node: site-level changes across all pages."""

    name = 'site'

    def __call__(self, state):
        meta = state.get('reports_meta') or {}
        return {'site': SiteLevelBuilder().build(state['contexts'], state.get('results') or {}, origin=meta.get('origin'),
                                                 run_date=state.get('run_date'))}


class WriteOutputsNode:
    """Node: write seo_reports/onpage/."""

    name = 'write'

    def __call__(self, state):
        written = OnpageOutputWriter().write(state['out_dir'], state)
        log('onpage: wrote ' + str(len(written.get('files') or [])) + ' files to ' + state['out_dir'])
        return {'written': written}


# ---------------------------------------------------------------- workflow
class OnpageAnalysisWorkflow:
    """load -> every check in CHECKS order -> site -> write. `writer` is the ContentWriter used by model checks."""

    def __init__(self, writer=None, checks=CHECKS):
        self.writer = writer or ContentWriter()
        self.checks = tuple(checks)

    def build_graph(self):
        graph = StateGraph(OnpageState)
        steps = [LoadReportsNode()] + [CheckNode(check, self.writer) for check in self.checks] + [SiteLevelNode(), WriteOutputsNode()]
        previous = START
        for node in steps:
            graph.add_node(node.name, node)
            graph.add_edge(previous, node.name)
            previous = node.name
        graph.add_edge(previous, END)
        return graph.compile()

    def run(self, reports_dir, *, out_dir=None, site_url=None, run_date=None, only_pages=None):
        begun = start('onpage SEO workflow')
        state = self.build_graph().invoke({'reports_dir': str(reports_dir), 'out_dir': out_dir, 'site_url': site_url,
                                           'run_date': run_date, 'only_pages': only_pages or []})
        done('onpage SEO workflow', begun)
        return state


def build_graph(writer=None):
    return OnpageAnalysisWorkflow(writer).build_graph()


def run_onpage(reports_dir, *, out_dir=None, site_url=None, run_date=None, only_pages=None, writer=None):
    """Function form of `OnpageAnalysisWorkflow(writer).run(reports_dir, ...)`."""
    return OnpageAnalysisWorkflow(writer).run(reports_dir, out_dir=out_dir, site_url=site_url, run_date=run_date,
                                              only_pages=only_pages)
