"""LangGraph linear workflow: load -> 12 checklist nodes (one at a time, pages in parallel inside a node) -> site -> write.

    START -> load -> intent -> keyword_plan -> page_plan -> media_links -> advice -> media_technical -> accessibility
          -> technical -> structured_data -> social_meta -> fact_check -> qa -> site -> write -> END

Four nodes call a model (intent, page_plan, media_links, advice); the rest are deterministic. `qa` runs the scope gate
(head changes on a shared shell are blocked behind prerendering), dedup/consolidation and the before/after checklist.
A failed item is recorded with its validator errors; later items that depend on it are skipped, others still run.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
import os
import traceback
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from seo_agents.log import done, log, start
from seo_agents.onpage.checks import CHECKS, DEPENDS, result
from seo_agents.onpage.context import build_context
from seo_agents.onpage.model import load_reports, site_origin
from seo_agents.onpage.output import write_outputs
from seo_agents.onpage.site_level import build_site
from seo_agents.onpage.writer import Writer, WriterFailed


def merge_results(left, right):
    merged = {page: dict(checks) for page, checks in (left or {}).items()}
    for page, checks in (right or {}).items():
        merged.setdefault(page, {}).update(checks)
    return merged


class OnpageState(TypedDict, total=False):
    reports_dir: str
    out_dir: str
    site_url: str
    run_date: str
    only_pages: list
    reports_meta: dict
    contexts: dict
    results: Annotated[dict, merge_results]
    site: dict
    written: dict


def selectable(page):
    status = (page.get('http') or {}).get('http_status')
    return page.get('instance_status') != 'template' and page.get('kind', 'page') == 'page' and not (isinstance(status, int) and status >= 400)


def load_node(state):
    begun = start('onpage: load exploration JSON')
    reports = load_reports(state['reports_dir'])
    origin = site_origin(reports, state.get('site_url'))
    run_date = state.get('run_date') or date.today().isoformat()
    limit = int(os.getenv('SEO_ONPAGE_MAX_PAGES', '50'))
    only = set(state.get('only_pages') or [])
    ids = [r['id'] for r in reports.page_rows() if r['id'] in reports.pages and selectable(reports.pages[r['id']])
           and (not only or r['id'] in only or r.get('url') in only)][:limit]
    contexts = {pid: build_context(reports, pid, origin=origin, run_date=run_date) for pid in ids}
    log(f'onpage: {len(contexts)} pages selected (origin={origin})')
    done('onpage: load exploration JSON', begun)
    meta = {'site_model_dir': str(reports.site_model_dir), 'schema': reports.index.get('schema'),
            'snapshot_sha256': (reports.index.get('repo') or {}).get('snapshot_sha256'),
            'framework': (reports.index.get('repo') or {}).get('framework'), 'origin': origin}
    return {'contexts': contexts, 'run_date': run_date, 'reports_meta': meta,
            'out_dir': state.get('out_dir') or str(reports.root / 'onpage')}


def check_node(name, fn, writer):
    def node(state):
        begun = start('onpage check: ' + name)
        contexts, results = state['contexts'], state.get('results') or {}

        def run(page_id):
            prior = results.get(page_id, {})
            blocked = [d for d in DEPENDS.get(name, []) if (prior.get(d) or {}).get('status') != 'done']
            if blocked:
                return page_id, result('skipped', data={'reason': 'depends on failed/skipped: ' + ', '.join(blocked)})
            try:
                return page_id, fn(contexts[page_id], prior, writer)
            except WriterFailed as error:
                return page_id, result('failed', errors=error.errors)
            except Exception as error:  # noqa: BLE001 - one page/check must not stop the workflow
                log(f'onpage {name} {page_id}: {type(error).__name__}: {error}')
                return page_id, result('failed', errors=[type(error).__name__ + ': ' + str(error)[:300],
                                                         traceback.format_exc(limit=3)[-600:]])

        workers = max(1, int(os.getenv('SEO_ONPAGE_CONCURRENCY', '4')))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            outcomes = dict(pool.map(run, list(contexts)))
        counts = {}
        for res in outcomes.values():
            counts[res['status']] = counts.get(res['status'], 0) + 1
        log(f'onpage check {name}: ' + ', '.join(f'{k}={v}' for k, v in sorted(counts.items())))
        done('onpage check: ' + name, begun)
        return {'results': {pid: {name: res} for pid, res in outcomes.items()}}
    return node


def site_node(state):
    meta = state.get('reports_meta') or {}
    return {'site': build_site(state['contexts'], state.get('results') or {}, origin=meta.get('origin'), run_date=state.get('run_date'))}


def write_node(state):
    written = write_outputs(state['out_dir'], state)
    log('onpage: wrote ' + str(len(written.get('files') or [])) + ' files to ' + state['out_dir'])
    return {'written': written}


def build_graph(writer=None):
    writer = writer or Writer()
    graph = StateGraph(OnpageState)
    graph.add_node('load', load_node)
    previous = 'load'
    graph.add_edge(START, 'load')
    for name, _skill, fn, _llm in CHECKS:
        graph.add_node(name, check_node(name, fn, writer))
        graph.add_edge(previous, name)
        previous = name
    graph.add_node('site', site_node)
    graph.add_node('write', write_node)
    graph.add_edge(previous, 'site')
    graph.add_edge('site', 'write')
    graph.add_edge('write', END)
    return graph.compile()


def run_onpage(reports_dir, *, out_dir=None, site_url=None, run_date=None, only_pages=None, writer=None):
    begun = start('onpage SEO workflow')
    state = build_graph(writer).invoke({'reports_dir': str(reports_dir), 'out_dir': out_dir, 'site_url': site_url,
                                        'run_date': run_date, 'only_pages': only_pages or []})
    done('onpage SEO workflow', begun)
    return state
