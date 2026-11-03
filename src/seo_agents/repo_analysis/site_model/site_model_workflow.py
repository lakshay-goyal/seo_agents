"""Site-model LangGraph (doc §11): one class per stage; only the gap-worker stage calls a model.

Map-reduce fan-out with `Send`:

  acquire -> build_serve -> url_manifest -+-> measure_page (Send per URL) -> discover -+-> measure_page (next crawl wave)
                                          |                                           +-> attribute
                                          +-> attribute (source-only)
  attribute -+-> gap_worker (Send per task) -> assemble -> END
             +-> assemble (no gaps / no model)

    AcquireStage        RepositorySnapshotTool, FrameworkDetector, ElementIndexBuilder, RouteDiscoveryTool,
                        FrameworkRouteAdapters (+ GapWorkerAgent.learn_route_adapter for unknown stacks)
    BuildServeStage     SiteServer, UrlManifestBuilder, PageRenderer
    UrlManifestStage    UrlManifestBuilder
    MeasurePageStage    PageRenderer
    CrawlDiscoveryStage (pure Python)
    AttributionStage    UrlManifestBuilder, SourceLens, Attributor, Lighthouse / link checks / probes, GapWorkerAgent.build_tasks
    GapWorkerStage      GapWorkerAgent.solve
    AssembleStage       GapWorkerAgent.apply, site_model_builder helpers, ExplorationWriter
"""

from __future__ import annotations

import json
import operator
import os
import re
from pathlib import Path
from typing import Annotated, Any, TypedDict
from urllib.parse import urljoin, urlsplit

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from seo_agents.utils.state_reducers import merge_dicts, merge_min_depth
from seo_agents.utils.step_logger import log, progress


def ok_paths(measured):
    """Crawled URLs that answered successfully; failures stay findings, not pages."""
    good = []
    for path, result in measured.items():
        raw = (result.get('lenses') or {}).get('raw') or {}
        if raw.get('status') == 'success' or (raw.get('status') in {None, 'failed', 'timeout'} and
                                              any((result['lenses'].get(d) or {}).get('status') == 'success' for d in ('desktop', 'mobile'))):
            good.append(path)
    return good


class SiteState(TypedDict, total=False):
    config: dict
    snapshot: dict
    fingerprint: dict
    index: dict
    routes: Any
    learned: list
    limitations: list
    served: Any
    base_url: Any
    extra_urls: list
    pages: list
    manifest_notes: dict
    frontier: list
    measured: Annotated[dict, merge_dicts]
    depth: Annotated[dict, merge_min_depth]
    found: Annotated[list, operator.add]
    model_pages: list
    gaps: list
    ctx: Any
    performance: dict
    link_health: dict
    probes: dict
    tasks: list
    own_hosts: list
    wave: int
    resolutions: Annotated[list, operator.add]
    model: dict


# ---------------------------------------------------------------- nodes
class AcquireStage:
    """Stage A: snapshot, framework fingerprint, element index, routes (learned adapter for unknown stacks)."""

    name = 'acquire'

    def __call__(self, state):
        from seo_agents.repo_analysis.tools.parsing.element_index import ElementIndexBuilder
        from seo_agents.repo_analysis.tools.repository.framework_detection import FrameworkDetector
        from seo_agents.repo_analysis.tools.repository.repository_snapshot import RepositorySnapshotTool
        config = state['config']
        with progress('site graph: acquire (snapshot, fingerprint, element index)'):
            snapshot = state.get('snapshot') or RepositorySnapshotTool().capture(config['root'])
            fingerprint = FrameworkDetector().detect(snapshot)
            index = ElementIndexBuilder().build(snapshot)
        log('framework: ' + json.dumps({k: fingerprint[k] for k in ('primary', 'version', 'router', 'renderer')}))
        routes = (config.get('deterministic') or {}).get('routes')
        if routes is None:
            try:
                from seo_agents.repo_analysis.tools.routing.route_discovery import RouteDiscoveryTool
                from seo_agents.repo_analysis.tools.repository.source_index import SourceIndex
                routes = RouteDiscoveryTool().discover(SourceIndex(snapshot))
            except Exception as error:  # noqa: BLE001 - the generic routes skill is only a fallback
                log('routes skill unavailable: ' + type(error).__name__)
        learned, limitations = [], list(snapshot.get('limitations', []))
        from seo_agents.repo_analysis.tools.routing.framework_route_adapters import FrameworkRouteAdapters
        if config['gap_workers'] and not FrameworkRouteAdapters().routes(snapshot['files'], fingerprint)[0]:
            from seo_agents.repo_analysis.agents.gap_workers.gap_worker_agent import GapWorkerAgent
            with progress('site graph: learn route adapter for unrecognised framework'):
                rules, origin = GapWorkerAgent().learn_route_adapter(snapshot, fingerprint)
            learned = GapWorkerAgent().apply_learned_rules(snapshot['files'], rules)
            if learned:
                limitations.append(f'Routes for this unrecognised stack come from a learned adapter ({origin}); verify them.')
        return {'snapshot': snapshot, 'fingerprint': fingerprint, 'index': index, 'routes': routes, 'learned': learned,
                'limitations': limitations}


class BuildServeStage:
    """Stage B: a served site to measure (site_url | pre-built HTML | build+serve | source-only)."""

    name = 'build_serve'

    def __call__(self, state):
        from seo_agents.repo_analysis.site_model.url_manifest import UrlManifestBuilder
        config = state['config']
        if config['measure'] is False:
            return {'served': None, 'base_url': None, 'extra_urls': []}
        from seo_agents.repo_analysis.measurement.site_server import SiteServer
        with progress('site graph: build/serve'):
            served = SiteServer().serve(config['root'], state['fingerprint'], site_url=config['site_url'], build=config['build'])
        config.setdefault('_served', []).append(served)  # closed by run_site_graph even if a later node fails
        if config['measure'] is None and served.mode == 'source-only':
            return {'served': None, 'base_url': None, 'extra_urls': [],
                    'limitations': state['limitations'] + ['Source-only: no site_url, no pre-built HTML and no build requested; '
                                                           'pass --site-url or --build for served/rendered measurements.']}
        extra = UrlManifestBuilder().build_output_urls(served.output_dir)
        if served.base_url:
            from seo_agents.repo_analysis.site_model.site_model_builder import live_sitemap_urls
            extra += live_sitemap_urls(served.base_url,
                                       own_hosts=UrlManifestBuilder().site_hosts(state['snapshot'], config.get('site_url')))
        limitations = state['limitations'] + served.limitations
        if config['render'] and served.base_url:
            from seo_agents.repo_analysis.measurement.page_renderer import PageRenderer
            if not PageRenderer().available():
                limitations.append('Rendering requested but Playwright is not installed; raw HTML lens only.')
        return {'served': served, 'base_url': served.base_url, 'extra_urls': extra, 'limitations': limitations}


class UrlManifestStage:
    """Stage C: URL manifest and the first crawl frontier."""

    name = 'url_manifest'

    def __call__(self, state):
        from seo_agents.repo_analysis.site_model.url_manifest import UrlManifestBuilder
        manifest = UrlManifestBuilder()
        hosts = manifest.site_hosts(state['snapshot'], state['config'].get('site_url'))
        pages, notes = manifest.build(state['snapshot'], state['fingerprint'], routes=state.get('routes'),
                                      site_url=state.get('base_url'), extra_urls=state.get('extra_urls', []),
                                      learned_rows=state.get('learned', []), own_hosts=hosts)
        frontier = []
        base = state.get('base_url')
        if base:
            frontier = [('/', 0, fetch_url(base, '/', None))] + [
                (p['url'], None, fetch_url(base, p['url'], p.get('fetch_path')))
                for p in pages if p['instance_status'] in {'static', 'concrete'} and p['url'] != '/']
        log(f"url manifest: {len(pages)} URLs ({notes.get('adapter')}); measuring {len(frontier)}")
        return {'pages': pages, 'manifest_notes': notes, 'frontier': frontier[:state['config']['max_pages']],
                'own_hosts': sorted(hosts), 'wave': 0}


def fetch_url(base, path, fetch_path=None):
    """URL to request for a page: the original file path when known (e.g. /about.html), never the
    normalised key; a base URL with a path prefix (GitHub Pages) is not duplicated."""
    target = fetch_path or path
    if target.startswith(('http://', 'https://')):
        return target
    return urljoin(base.rstrip('/') + '/', target.lstrip('/'))


def fan_out_measure(state):
    frontier = state.get('frontier') or []
    if not frontier:
        return 'attribute'
    config = state['config']
    return [Send('measure_page', {'path': path, 'level': level, 'url': url, 'base_url': state['base_url'],
                                  'own_hosts': state.get('own_hosts', []), 'render': config['render'],
                                  'timeout': config['timeout']}) for path, level, url in frontier]


class MeasurePageStage:
    """Stage D (one per URL, via Send): raw + rendered lenses and the internal links found on the page."""

    name = 'measure_page'

    def __call__(self, item):
        from seo_agents.repo_analysis.measurement.page_renderer import PageRenderer
        from seo_agents.repo_analysis.site_model.site_model_builder import best_summary, same_site
        from seo_agents.repo_analysis.site_model.url_manifest import normalize_path
        base = item['base_url']
        result = PageRenderer().measure(item['url'], render=item['render'], timeout=item['timeout'])
        found = []
        raw = (result.get('lenses') or {}).get('raw') or {}
        if raw.get('status') not in {'success', None}:
            return {'measured': {item['path']: result}, 'depth': {item['path']: item['level']} if item['level'] is not None else {}, 'found': []}
        own = set(item.get('own_hosts') or [])
        for link in (best_summary(result) or {}).get('links', []):
            resolved = link.get('resolved')
            if not resolved:
                continue
            local = same_site(resolved, base)
            host = (urlsplit(resolved).hostname or '').lower()
            if not local and host not in own:
                continue
            target = normalize_path(resolved, base, own_hosts=own)
            if target and not re.search(r'\.(?:pdf|png|jpe?g|gif|svg|webp|avif|ico|zip|xml|txt|json|css|js|mjs|md|mdx|csv|mp4|mp3|woff2?)$', target, re.I):
                # Fetch exactly what the page linked to (keeps .html / trailing slash / base path) on the measured host.
                fetch = resolved if local else fetch_url(base, urlsplit(resolved).path or '/')
                found.append((target, item['path'], fetch.split('#', 1)[0]))
        depth = {item['path']: item['level']} if item['level'] is not None else {}
        return {'measured': {item['path']: result}, 'depth': depth, 'found': found}


class CrawlDiscoveryStage:
    """Stage D crawl expansion: links seen on measured pages that are not measured yet (BFS waves)."""

    name = 'discover'

    def __call__(self, state):
        config = state['config']
        measured = state.get('measured', {})
        depth = dict(state.get('depth', {}))
        changed = True
        while changed:  # propagate click depth along observed edges
            changed = False
            for target, origin, _ in state.get('found', []):
                if origin in depth and (target not in depth or depth[origin] + 1 < depth[target]):
                    depth[target] = depth[origin] + 1
                    changed = True
        wave = state.get('wave', 0) + 1
        budget = config['max_pages'] - len(measured)
        max_depth = int(os.getenv('SEO_MAX_CRAWL_DEPTH', '10'))
        queued = {path for path, _, _ in state.get('frontier', [])}
        new, seen_new = [], set()
        for target, _, url in state.get('found', []):
            if target in measured or target in queued or target in seen_new:
                continue
            if depth.get(target) is not None and depth[target] > max_depth:
                continue
            seen_new.add(target)
            new.append((target, depth.get(target), url))
        frontier = new[:max(0, budget)] if wave <= MAX_WAVES else []
        update = {'frontier': frontier, 'depth': depth, 'wave': wave}
        if frontier:  # the manifest is rebuilt once, in attribute, with every crawled path
            log(f'crawl: {len(frontier)} new internal URLs discovered (measured {len(measured)})')
        return update


class AttributionStage:
    """Stage E: source lens + attribution per page, Lighthouse / link checks / site probes, gap tasks."""

    name = 'attribute'

    def __call__(self, state):
        from seo_agents.repo_analysis.site_model.value_attribution import Attributor
        from seo_agents.repo_analysis.site_model import site_model_builder as sm
        from seo_agents.repo_analysis.site_model.source_lens import SourceLens
        from seo_agents.repo_analysis.site_model.url_manifest import UrlManifestBuilder
        config = state['config']
        pages = state['pages']
        measured = state.get('measured', {})
        if measured:
            crawl = [(path, 'crawl') for path in sorted(ok_paths(measured))]
            pages, notes = UrlManifestBuilder().build(state['snapshot'], state['fingerprint'], routes=state.get('routes'),
                                          site_url=state.get('base_url'), extra_urls=state.get('extra_urls', []) + crawl,
                                          learned_rows=state.get('learned', []), own_hosts=set(state.get('own_hosts', [])))
            state = {**state, 'manifest_notes': notes}
        with progress('site graph: source lens + attribution'):
            source_pages, ctx = SourceLens().extract(state['snapshot'], state['index'], state['fingerprint'], [p for p in pages if p['template']])
            attributor = Attributor(ctx)
            model_pages = []
            for page in pages:
                source = source_pages.get(page['id']) or sm.empty_page()
                result = measured.get(page['url'])
                values, lens = (sm.merge_measured(page, source, result, attributor.attribute) if result else (source, None))
                model_pages.append(sm.assemble_page(page, values, result, lens, state.get('depth', {}).get(page['url'])))
        base = state.get('base_url')
        performance, link_health, probes = {}, {}, {}
        try:
            if base and config['lighthouse']:
                performance = sm.run_lighthouse(base, model_pages)
            if base and config['check_links']:
                link_health = sm.run_link_checks(base, model_pages, measured)
            if base:
                samples = [p['url'] for p in sorted(pages, key=lambda p: (len(p['url']), p['url']))
                           if p['instance_status'] in {'static', 'concrete'} and p['url'] != '/']
                probes = sm.probe_site(base, samples)
        finally:
            if state.get('served') is not None:
                state['served'].close()
        sm.link_graph(model_pages)
        gaps = attributor.gaps + sm.collect_gaps(model_pages, state['snapshot']['files'], attributor.gaps)
        tasks = []
        if config['gap_workers']:
            from seo_agents.repo_analysis.agents.gap_workers.gap_worker_agent import GapWorkerAgent
            tasks = GapWorkerAgent().build_tasks(gaps, model_pages, ctx, state['fingerprint'])
        return {'pages': pages, 'manifest_notes': state['manifest_notes'], 'model_pages': model_pages, 'gaps': gaps, 'ctx': ctx,
                'performance': performance, 'link_health': link_health, 'probes': probes, 'tasks': tasks}


def fan_out_gaps(state):
    tasks = state.get('tasks') or []
    if not tasks:
        return 'assemble'  # edge case from the doc: no gaps -> straight to assemble
    family = state['fingerprint'].get('family')
    return [Send('gap_worker', {'task': task, 'family': family, 'index': position}) for position, task in enumerate(tasks)]


class GapWorkerStage:
    """Stage F (one per gap task, via Send): GapWorkerAgent.solve."""

    name = 'gap_worker'

    def __call__(self, item):
        from seo_agents.repo_analysis.agents.gap_workers.gap_worker_agent import GapWorkerAgent
        return {'resolutions': [(item['index'], GapWorkerAgent().solve(item['task'], item['family']))]}


class AssembleStage:
    """Stage G: apply gap answers, dedup, link health, validation, scope, site facts; write the model."""

    name = 'assemble'

    def __call__(self, state):
        from collections import Counter
        from seo_agents.repo_analysis.site_model import site_model_builder as sm
        from seo_agents.repo_analysis.agents.gap_workers.gap_worker_agent import GapWorkerAgent
        gap_worker = GapWorkerAgent()
        from seo_agents.repo_analysis.site_model.source_lens import CHROME
        config = state['config']
        model_pages = state['model_pages']
        by_id = {page['id']: page for page in model_pages}
        tasks = state.get('tasks') or []
        counts = Counter()
        unresolved = []
        for position, result in sorted(state.get('resolutions', []), key=lambda r: r[0]):
            task = tasks[position]
            counts[task['task'] + ':' + result['status']] += 1
            if result['status'] == 'resolved' and task.get('page_id') in by_id and gap_worker.apply(task, result, by_id[task['page_id']], state['ctx']):
                counts['applied'] += 1
            elif result['status'] != 'resolved':
                unresolved.append({'task': task['task'], 'page_id': task.get('page_id'), 'field': task.get('field'), 'errors': result['errors'][-2:]})
        resolutions = state.get('resolutions', [])
        suggested = expand_suggested_instances(model_pages, state.get('pages') or [], config)
        if suggested:
            model_pages = model_pages + suggested
            counts['instances_added'] = len(suggested)
        gap_report = {'total': len(tasks), 'attribution_gaps': len(state.get('gaps', [])),
                      'resolved': sum(r['status'] == 'resolved' for _, r in resolutions), 'applied': counts['applied'],
                      'by_outcome': dict(counts), 'escalated_to_mid': sum(r['tier'] == 'mid' for _, r in resolutions),
                      'status': 'completed' if tasks else ('not-run' if not config['gap_workers'] else 'no-tasks'),
                      'unresolved': unresolved[:50]}
        for page in model_pages:
            page['_simhash'] = sm.simhash(' '.join(b['text'] for b in page['content']['blocks'] if b.get('section') not in CHROME))
        duplicates = sm.near_duplicates(model_pages)
        link_health = state.get('link_health') or {}
        for page in model_pages:
            page.pop('_simhash', None)
            if page['id'] in (state.get('performance') or {}):
                page['performance'] = state['performance'][page['id']]
            for link in page['links']['out']:
                health = link_health.get('results', {}).get(link.get('resolved') or '')
                if health:
                    link['status'] = health.get('status')
                    link['state'] = sm.link_state(health)
                    if link['state'] == 'ok' and health.get('redirects'):
                        link['redirect_chain'] = health['redirect_chain']
        validation = sm.validate_model(model_pages, state['snapshot']['files'])
        sm.annotate_scope(model_pages, state['fingerprint'])
        site = sm.site_facts(state['snapshot'], state['fingerprint'], state['index'], state['manifest_notes'], state.get('served'),
                             state.get('probes') or {}, config.get('deterministic'), model_pages, duplicates, link_health,
                             gap_report, validation, state.get('limitations', []), config['begun'])
        measured = state.get('measured', {})
        good = set(ok_paths(measured))
        site['crawl_errors'] = [{'url': path, 'status': ((result.get('lenses') or {}).get('raw') or {}).get('status'),
                                 'http_status': ((result.get('lenses') or {}).get('raw') or {}).get('http_status'),
                                 'linked_from': sorted({origin for target, origin, _ in state.get('found', []) if target == path})[:10]}
                                for path, result in sorted(measured.items()) if path not in good][:300]
        index_view = sm.planning_index(state['snapshot'], state['fingerprint'], model_pages, site)
        site['timings'] = dict(config.get('_timings') or {})
        model = {'index': index_view, 'site': site, 'pages': {page['id']: page for page in model_pages}}
        if config.get('output_dir'):
            sm.write_model(model, config['output_dir'])
            from seo_agents.repo_analysis.output.exploration_writer import ExplorationWriter
            exploration_dir = os.getenv('SEO_EXPLORATION_DIR') or Path(config['output_dir']).parent / 'exploration'
            ExplorationWriter().write(model, exploration_dir, root=config.get('root'), files=state['snapshot']['files'],
                                      deterministic=config.get('deterministic'))
        model['_element_index'] = state['index']  # in memory only: lets repo_analysis.py reuse it instead of rebuilding
        return {'model': model}


def expand_suggested_instances(model_pages, manifest_pages, config):
    """Gap feedback loop (one pass, no recursion): instances a gap worker found for a dynamic route
    (e.g. slugs in a data file) become source-only pages instead of being dropped."""
    import copy
    from seo_agents.repo_analysis.site_model.url_manifest import normalize_path, page_id
    sources = [page for page in model_pages if page.get('instances_suggested')]
    if not sources:
        return []
    known = {page['url'] for page in model_pages}
    suggestions = [{'route': p['route'], 'template': p['template'], 'urls': p['instances_suggested'].get('urls') or [],
                    'source': p['instances_suggested'].get('source'), 'page_id': p['id']} for p in sources]
    try:
        from seo_agents.repo_analysis.site_model.url_manifest import merge_suggested_instances
        rows = merge_suggested_instances(manifest_pages, suggestions) or []
        urls = {(row.get('url'), row.get('route')) for row in rows if isinstance(row, dict) and row.get('url')}
    except (ImportError, TypeError, ValueError, KeyError):
        urls = {(normalize_path(u) or u, s['route']) for s in suggestions for u in s['urls']}
    by_route = {s['route']: next(p for p in sources if p['id'] == s['page_id']) for s in suggestions}
    budget = max(0, config.get('max_pages', 200) - len(model_pages))
    added = []
    for url, route in sorted(urls, key=lambda r: str(r[0])):
        if not url or url in known or route not in by_route or len(added) >= budget:
            continue
        template = by_route[route]
        record = copy.deepcopy({k: v for k, v in template.items() if k != 'instances_suggested'})
        record.update(id=page_id(url), url=url, instance_status='concrete', discovered_by=['gap-suggested'], lens='source',
                      http=None, performance=None)
        record['rendering'] = {'raw_html_words': None, 'rendered_words': None, 'js_dependent': None, 'method': 'source-only'}
        record['limitations'] = [l for l in record.get('limitations', []) if not l.startswith('Route pattern')] + [
            'Instance suggested by a gap worker from ' + json.dumps(template['instances_suggested'].get('source')) +
            '; values describe the template until measured.']
        known.add(url)
        added.append(record)
    if added:
        log(f'gap feedback: {len(added)} suggested instances added to the model')
    return added


def timed(name, node):
    import time

    def run(state):
        begun = time.perf_counter()
        try:
            return node(state)
        finally:
            config = state.get('config') if isinstance(state, dict) and 'config' in state else None
            if config is not None:
                timings = config.setdefault('_timings', {})
                timings[name] = round(timings.get(name, 0) + time.perf_counter() - begun, 3)
    run.__name__ = name
    return run


STAGES = (AcquireStage(), BuildServeStage(), UrlManifestStage(), MeasurePageStage(), CrawlDiscoveryStage(),
          AttributionStage(), GapWorkerStage(), AssembleStage())


def build_site_graph(checkpointer=None):
    """acquire -> build_serve -> url_manifest -(Send)-> measure_page <-> discover -> attribute -(Send)-> gap_worker -> assemble."""
    graph = StateGraph(SiteState)
    for stage in STAGES:
        graph.add_node(stage.name, timed(stage.name, stage))
    graph.add_edge(START, 'acquire')
    graph.add_edge('acquire', 'build_serve')
    graph.add_edge('build_serve', 'url_manifest')
    graph.add_conditional_edges('url_manifest', fan_out_measure, ['measure_page', 'attribute'])
    graph.add_edge('measure_page', 'discover')
    graph.add_conditional_edges('discover', fan_out_measure, ['measure_page', 'attribute'])
    graph.add_conditional_edges('attribute', fan_out_gaps, ['gap_worker', 'assemble'])
    graph.add_edge('gap_worker', 'assemble')
    graph.add_edge('assemble', END)
    return graph.compile(checkpointer=checkpointer) if checkpointer is not None else graph.compile()


MAX_WAVES = 40  # crawl waves; each costs two supersteps (measure_page, discover)


def graph_concurrency(config):
    """SEO_MEASURE_WORKERS (or an explicit `workers`) caps concurrency, up or down; otherwise the larger of
    the measure default and SEO_GAP_CONCURRENCY. Each concurrent URL launches its own browser."""
    if config.get('workers_explicit') or os.getenv('SEO_MEASURE_WORKERS'):
        return max(1, int(config.get('workers') or os.getenv('SEO_MEASURE_WORKERS') or 4))
    return max(config.get('workers') or 4, int(os.getenv('SEO_GAP_CONCURRENCY', '6')))


def checkpointer_from_env():
    """Opt-in resume: SEO_CHECKPOINT=/path/to/checkpoints.sqlite (needs langgraph-checkpoint-sqlite)."""
    path = os.getenv('SEO_CHECKPOINT')
    if not path:
        return None, None
    try:
        import sqlite3
        from langgraph.checkpoint.sqlite import SqliteSaver
        try:
            from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
            serde = JsonPlusSerializer(pickle_fallback=True)
        except (ImportError, TypeError):
            serde = None
        connection = sqlite3.connect(path, check_same_thread=False)
        saver = SqliteSaver(connection, serde=serde) if serde else SqliteSaver(connection)
        return saver, connection
    except Exception as error:  # noqa: BLE001 - checkpointing is optional; never fail the run over it
        log('checkpointing unavailable (' + type(error).__name__ + '); running without resume')
        return None, None


def run_site_graph(config, snapshot=None):
    concurrency = graph_concurrency(config)
    state = {'config': config}
    if snapshot is not None:
        state['snapshot'] = snapshot
    checkpointer, connection = checkpointer_from_env()
    run_config = {'max_concurrency': concurrency, 'recursion_limit': 2 * MAX_WAVES + 30}
    if checkpointer is not None:
        run_config['configurable'] = {'thread_id': os.getenv('SEO_CHECKPOINT_THREAD') or str(config.get('root'))}
    try:
        try:
            result = build_site_graph(checkpointer).invoke(state, config=run_config)
        except (TypeError, ValueError) as error:
            if checkpointer is None or 'serializ' not in str(error).lower():
                raise
            log('checkpoint serialisation failed; re-running without resume')
            run_config.pop('configurable', None)
            result = build_site_graph().invoke(state, config=run_config)
    finally:
        for served in config.pop('_served', []):
            served.close()
        if connection is not None:
            connection.close()
    return result['model']


class SiteModelWorkflow:
    """The site-model LangGraph: `SiteModelWorkflow(config).run(snapshot)` -> {'index', 'site', 'pages'}.

    Stages (one class each, in STAGES): AcquireStage, BuildServeStage, UrlManifestStage, MeasurePageStage,
    CrawlDiscoveryStage, AttributionStage, GapWorkerStage, AssembleStage. Tools per stage: lib/tool_mapping.py.
    """

    stages = STAGES

    def __init__(self, config):
        self.config = config

    def build_graph(self, checkpointer=None):
        return build_site_graph(checkpointer)

    def run(self, snapshot=None):
        return run_site_graph(self.config, snapshot=snapshot)
