"""Repository analysis: the one place where every codebase-exploration agent is called.

`repo_analysis()`:
1. snapshots the repository once (`tools/repository_snapshot.py`);
2. runs every skill-driven agent in `explore_codebase()` (no model needed);
3. writes `seo_reports/repo_context.json` (`context_builder.py`): every record holds
   the file reference *and* the exact source code; referenced files are embedded in full;
4. optionally runs the 7 LLM agents through `graph.py` (verified per-page reports).

CLI (no clone, no PR):
    python -m seo_agents.exploration.repo_analysis /path/to/site -o /tmp/repo_context.json
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
from seo_agents.exploration.repo_analysis.agents.state_management import analyze_state_management
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
    run('sitemap_robots', check_sitemap_and_robots, index)
    run('canonicals_redirects', analyze_canonicals_redirects, index)
    run('rendering_strategy', analyze_rendering_strategy, index, routes)
    run('client_diagnostics', diagnose_react, index)
    run('state_management', analyze_state_management, index)
    run('performance_web_vitals', analyze_performance_assets, index, inventory, project)
    run('https_security_headers', analyze_https_security_headers, index)
    run('structured_data', analyze_structured_data, index)
    run('mobile_parity', analyze_mobile_parity, index)
    run('hreflang_i18n', analyze_hreflang_i18n, index)

    # Shareability, brand/entity, local business presence and AI-search readiness
    run('shareability_link_assets', analyze_shareability_link_assets, index)
    run('brand_entity_signals', analyze_brand_entity_signals, index)
    run('local_business_presence', analyze_local_business_presence, index)
    run('ai_search_readiness', analyze_ai_search_readiness, index)
    return results


def run_llm_agents(repo_path=None):
    """7 LLM agents via graph.py (urls, page_structure, internal_links, media, content, metadata, eeat)."""
    from seo_agents.exploration.repo_analysis.graph import run_onpage_graph

    return run_onpage_graph(repo_path=repo_path)


def repo_analysis(github_url=None, repo_path=None, *, output_path=None, run_agents=None,
                  include_content='referenced'):
    """Explore the whole codebase and write the SEO context JSON.

    output_path: where to write the JSON (default `<repo>/seo_reports/repo_context.json`;
        pass False to skip writing).
    run_agents: also run the 7 LLM agents (default: env SEO_RUN_LLM_AGENTS, else true;
        set to 0 to run without a model).
    include_content: 'referenced' (full content of every file with a record), 'all', or 'none'.

    Returns the LLM graph state (or a minimal state) with `repo_context` and
    `repo_context_path` added. The context file is written before the LLM agents
    start, so it survives an agent failure.
    """
    label = "repo analysis: " + (github_url or str(repo_path or Path.cwd()))
    begun = start(label)
    root = Path(repo_path or Path.cwd())
    with progress('repository snapshot'):
        index = SourceIndex(snapshot_repository(root))
    results = explore_codebase(index)
    context = build_repo_context(index, results, repo_path=root, github_url=github_url,
                                 include_content=include_content)
    path = None
    if output_path is not False:
        path = write_repo_context(context, output_path or root / DEFAULT_OUTPUT)
    if run_agents is None:
        run_agents = os.getenv('SEO_RUN_LLM_AGENTS', '1') != '0'
    state = {}
    if run_agents:
        state = run_llm_agents(repo_path=repo_path)
        artifacts = state.get("artifacts") or {}
        log("LLM agent reports in memory: " + str(sorted(artifacts)))
        context['llm_agent_reports'] = {'categories': sorted(artifacts),
                                        'note': 'Verified LLM reports are published separately under seo_reports/onpage/.'}
        if path:
            write_repo_context(context, path)
    state = dict(state)
    state['repo_context'] = context
    state['repo_context_path'] = str(path) if path else None
    done(label, begun)
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description='Write the SEO repository context JSON for a local checkout.')
    parser.add_argument('path', type=Path, help='Repository checkout to analyze')
    parser.add_argument('-o', '--output', type=Path, help='Output JSON path (default: <path>/' + DEFAULT_OUTPUT + ')')
    parser.add_argument('--with-llm-agents', action='store_true', help='Also run the 7 LLM agents')
    parser.add_argument('--content', choices=['referenced', 'all', 'none'], default='referenced',
                        help='Which file contents to embed in the JSON')
    args = parser.parse_args(argv)
    state = repo_analysis(repo_path=args.path, output_path=args.output, run_agents=args.with_llm_agents,
                          include_content=args.content)
    print(state['repo_context_path'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
