"""CLI for a local checkout (no clone, no PR):

    python -m seo_agents.repo_analysis /path/to/site [--site-url URL] [--render] [--build] [--with-llm-agents]
"""

import argparse
from pathlib import Path

from seo_agents.repo_analysis.repo_analysis import DEFAULT_OUTPUT, DEFAULT_SITE_MODEL, repo_analysis


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
