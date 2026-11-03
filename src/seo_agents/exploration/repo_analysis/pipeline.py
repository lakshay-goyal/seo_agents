"""Repository analysis: the one place where every codebase-exploration stage is called.

`repo_analysis()`:
1. snapshots the repository ONCE (`tools/repository_snapshot.py`, content-hash identity);
2. runs every deterministic skill runner in `explore_codebase()` (no model);
3. writes `seo_reports/repo_context.json` (`context_builder.py`; references, not file copies);
4. builds the page-centric SEO site model (`site_model.explore_site`, LangGraph orchestrator):
   framework fingerprint -> element index -> URL manifest -> raw/rendered/Lighthouse lenses
   (when a site_url, pre-built HTML or SEO_BUILD is available) -> attribution -> cheap-model gap
   workers -> `seo_reports/site_model/{index.json, site.json, pages/<id>.json}`;
5. optionally (SEO_RUN_LLM_AGENTS=1 / --with-llm-agents) runs the seven legacy LLM category
   extractors through `graph.py`, reusing the same snapshot, context and element index. DEPRECATED: the
   site model covers their data; a legacy failure never blocks the site model or repo_context.json.

CLI (no clone, no PR):
    python -m seo_agents.exploration.repo_analysis /path/to/site [--site-url URL] [--render] [--build]
"""

import argparse
import os
from pathlib import Path
import sys

from seo_agents.exploration.repo_analysis.context_builder import build_repo_context, write_repo_context
from seo_agents.exploration.repo_analysis.tools.repository_snapshot import snapshot_repository
from seo_agents.exploration.repo_analysis.tools.source_index import SourceIndex
from seo_agents.exploration.repo_analysis.tools.routes import discover_routes
from seo_agents.exploration.repo_analysis.tools.page_inventory import analyze_page_inventory
from seo_agents.exploration.repo_analysis.tools.keywords import extract_keywords
from seo_agents.exploration.repo_analysis.agents.code_exploration import explore_code
from seo_agents.exploration.repo_analysis.agents.sitemap_robots import check_sitemap_and_robots
from seo_agents.exploration.repo_analysis.agents.canonicals_redirects import analyze_canonicals_redirects
from seo_agents.exploration.repo_analysis.agents.rendering_strategy import analyze_rendering_strategy
from seo_agents.exploration.repo_analysis.agents.react_diagnostics import diagnose_react
from seo_agents.exploration.repo_analysis.agents.performance_assets import analyze_performance_assets
from seo_agents.exploration.repo_analysis.agents.https_security_headers import analyze_https_security_headers
from seo_agents.exploration.repo_analysis.agents.structured_data import analyze_structured_data
from seo_agents.exploration.repo_analysis.agents.mobile_parity import analyze_mobile_parity
from seo_agents.exploration.repo_analysis.agents.hreflang_i18n import analyze_hreflang_i18n
from seo_agents.exploration.repo_analysis.agents.shareability_link_assets import analyze_shareability_link_assets
from seo_agents.exploration.repo_analysis.agents.brand_entity_signals import analyze_brand_entity_signals
from seo_agents.exploration.repo_analysis.agents.local_business_presence import analyze_local_business_presence
from seo_agents.exploration.repo_analysis.agents.ai_search_readiness import analyze_ai_search_readiness
from seo_agents.log import done, log, progress, start

DEFAULT_OUTPUT = 'seo_reports/repo_context.json'
DEFAULT_SITE_MODEL = 'seo_reports/site_model'


def explore_codebase(index):
    """Run every skill-driven agent over one snapshot index. Returns {agent name: result}."""
    results = {}

    def run(name, agent, *args):
        with progress('agent ' + str(len(results) + 1) + ': ' + name):
            results[name] = agent(*args)
        return results[name]

    # Codebase structure: manifests/dependencies, routes, page inventory (tags, headings, text, links, media), keywords
    project = run('code_exploration', explore_code, index)
    routes = run('routes', discover_routes, index)
    inventory = run('page_inventory', analyze_page_inventory, index)
    run('keywords', extract_keywords, inventory, routes)

    # Crawl, rendering, Core Web Vitals, security, structured data, mobile and i18n
    sitemap = run('sitemap_robots', check_sitemap_and_robots, index)
    run('canonicals_redirects', analyze_canonicals_redirects, index)
    client = run('client_diagnostics', diagnose_react, index)
    run('rendering_strategy', analyze_rendering_strategy, index, routes, client)
    run('performance_web_vitals', analyze_performance_assets, index, inventory, project)
    run('https_security_headers', analyze_https_security_headers, index)
    structured = run('structured_data', analyze_structured_data, index)  # single @type source for the agents below
    run('mobile_parity', analyze_mobile_parity, index, project)
    run('hreflang_i18n', analyze_hreflang_i18n, index)

    # Shareability, brand/entity, local business presence and AI-search readiness
    run('shareability_link_assets', analyze_shareability_link_assets, index)
    run('brand_entity_signals', analyze_brand_entity_signals, index, structured)
    run('local_business_presence', analyze_local_business_presence, index, structured)
    run('ai_search_readiness', analyze_ai_search_readiness, index, structured, sitemap)
    return results


def run_llm_agents(repo_path=None, *, snapshot=None, element_index=None, routes=None):
    """Seven legacy LLM category extractors via graph.py, reusing the pipeline's single snapshot."""
    from seo_agents.exploration.repo_analysis.graph import run_onpage_graph
    from seo_agents.exploration.repo_analysis.tools.source_context import build_context

    context = build_context(snapshot) if snapshot is not None else None
    return run_onpage_graph(repo_path=repo_path, snapshot=snapshot, context=context,
                            element_index=element_index, routes=routes)


def repo_analysis(github_url=None, repo_path=None, *, output_path=None, run_agents=None,
                  include_content='none', site_url=None, render=None, build=None, site_model=True,
                  site_model_dir=None):
    """Explore the whole codebase and write the SEO context JSON plus the page-centric site model.

    output_path: repo_context.json path (default `<repo>/seo_reports/repo_context.json`; False skips writing).
    site_model_dir: site model directory (default `<repo>/seo_reports/site_model`).
    site_url/render/build: runtime lenses (see `site_model.explore_site`); env SEO_SITE_URL/SEO_RENDER/SEO_BUILD.
    run_agents: also run the 7 legacy LLM category extractors (default: env SEO_RUN_LLM_AGENTS, else off;
        the site model already covers their data, with gap workers resolving only what code cannot).
    include_content: 'none' (references + hashes), 'referenced' or 'all' for repo_context.json.

    Returns the state with `repo_context`, `repo_context_path`, `site_model` and `site_model_dir`.
    Each artifact is written as soon as it exists, so a later failure keeps earlier outputs.
    """
    label = "repo analysis: " + (github_url or str(repo_path or Path.cwd()))
    begun = start(label)
    root = Path(repo_path or Path.cwd())
    with progress('repository snapshot (single capture shared by every stage)'):
        snapshot = snapshot_repository(root)
        index = SourceIndex(snapshot)
    results = explore_codebase(index)
    context = build_repo_context(index, results, repo_path=root, github_url=github_url,
                                 include_content=include_content)
    path = None
    if output_path is not False:
        path = write_repo_context(context, output_path or root / DEFAULT_OUTPUT)
    state = {}
    model, model_dir = None, None
    if site_model:
        from seo_agents.exploration.repo_analysis.site_model import explore_site
        model_dir = Path(site_model_dir or root / DEFAULT_SITE_MODEL)
        model = explore_site(root, site_url=site_url, render=render, build=build, snapshot=snapshot,
                             deterministic=results, output_dir=model_dir if output_path is not False or site_model_dir else None)
        context['site_model'] = {'directory': str(model_dir), 'pages': len(model['pages']),
                                 'framework': model['site']['framework']['primary'],
                                 'note': 'Page-centric model: index.json, site.json, pages/<id>.json.'}
    if run_agents is None:
        run_agents = os.getenv('SEO_RUN_LLM_AGENTS', '0') == '1'
    if run_agents:
        try:
            element_index = (model or {}).get('_element_index')
            if element_index is None and model is not None:
                from seo_agents.exploration.repo_analysis.tools.element_index import build_element_index
                element_index = build_element_index(snapshot)
            state = run_llm_agents(repo_path=repo_path, snapshot=snapshot, element_index=element_index,
                                   routes=results.get('routes'))
            artifacts = state.get("artifacts") or {}
            log("LLM agent reports in memory: " + str(sorted(artifacts)))
            context['llm_agent_reports'] = {'categories': sorted(artifacts), 'status': 'completed',
                                            'note': 'Deprecated legacy reports, published separately under seo_reports/onpage/.'}
        except Exception as error:  # noqa: BLE001 - legacy extractors must never block the site model / repo context
            log('legacy LLM agents failed (site model and repo_context.json are kept): ' + type(error).__name__ + ': ' + str(error)[:300])
            failed = getattr(error, 'checkpoint', None) or {}
            state = {}
            context['llm_agent_reports'] = {'status': 'failed', 'error': type(error).__name__ + ': ' + str(error)[:500],
                                            'completed_categories': sorted((failed.get('artifacts') or {}))}
    if path:
        write_repo_context(context, path)
    state = dict(state)
    state['repo_context'] = context
    state['repo_context_path'] = str(path) if path else None
    if isinstance(model, dict):
        model.pop('_element_index', None)
    state['site_model'] = model
    state['site_model_dir'] = str(model_dir) if model is not None and (output_path is not False or site_model_dir) else None
    done(label, begun)
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description='Write the SEO repository context JSON and site model for a local checkout.')
    parser.add_argument('path', type=Path, help='Repository checkout to analyze')
    parser.add_argument('-o', '--output', type=Path, help='repo_context.json path (default: <path>/' + DEFAULT_OUTPUT + ')')
    parser.add_argument('--site-model-dir', type=Path, help='Site model directory (default: <path>/' + DEFAULT_SITE_MODEL + ')')
    parser.add_argument('--site-url', help='Deployed or preview origin to measure (raw HTML, rendered DOM, Lighthouse, links)')
    parser.add_argument('--render', action='store_true', help='Render pages with Playwright (desktop + mobile)')
    parser.add_argument('--build', action='store_true', help='Install, build and serve the repository first (runs its code)')
    parser.add_argument('--no-site-model', action='store_true', help='Only write repo_context.json')
    parser.add_argument('--with-llm-agents', action='store_true', help='Also run the 7 legacy LLM category extractors')
    parser.add_argument('--content', choices=['none', 'referenced', 'all'], default='none',
                        help='Which file contents to embed in repo_context.json')
    args = parser.parse_args(argv)
    state = repo_analysis(repo_path=args.path, output_path=args.output, run_agents=args.with_llm_agents,
                          include_content=args.content, site_url=args.site_url, render=args.render or None,
                          build=args.build or None, site_model=not args.no_site_model, site_model_dir=args.site_model_dir)
    print(state['repo_context_path'])
    if state.get('site_model_dir'):
        print(state['site_model_dir'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
