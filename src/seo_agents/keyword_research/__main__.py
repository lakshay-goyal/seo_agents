"""CLI: python -m seo_agents.keyword_research <seo_reports dir> [--country US --location-code 2840 --language en]
        [--competitor domain ...] [--pages ID|URL ...] [--max-iterations N] [--out DIR] [--site-url URL]

Writes <seo_reports>/keywords/ (keyword_research.csv, keyword_map.json, pages/, summary.json, summary.md).
Needs OPENROUTER_API_KEY (generator). Provider credentials (DataForSEO, Google Ads, GSC) and TYPESAFE_API_KEY (Jev)
are optional: without them the run is degraded and data-dependent keywords end up as `review`.
"""

import argparse
import os
from pathlib import Path

from seo_agents.utils.step_logger import log


def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog='python -m seo_agents.keyword_research')
    parser.add_argument('reports_dir', help='seo_reports/ folder (or its site_model/ folder)')
    parser.add_argument('--out', help='output folder (default: <seo_reports>/keywords)')
    parser.add_argument('--site-url', help='canonical origin, e.g. https://www.example.com (default: from the JSON)')
    parser.add_argument('--country', help='ISO-3166 alpha-2 market (default SEO_KEYWORD_COUNTRY or US)')
    parser.add_argument('--location-code', type=int, help='DataForSEO / Google Ads geo id (default 2840 = United States)')
    parser.add_argument('--language', help='language code (default SEO_KEYWORD_LANGUAGE or en)')
    parser.add_argument('--competitor', nargs='*', default=[], help='known competitor domains (merged into the profile)')
    parser.add_argument('--pages', nargs='*', help='limit to these page ids or URLs')
    parser.add_argument('--max-iterations', type=int, help='generate -> judge iterations per page (default 4)')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[3] / '.env')
        load_dotenv()
    except ImportError:
        pass
    for key in list(os.environ):
        if (key.startswith(('OPENROUTER_', 'SEO_KEYWORD_')) or key == 'SEO_ONPAGE_TIER') and os.environ.get(key) == '':
            del os.environ[key]  # empty .env entries mean "use the default"
    from seo_agents.keyword_research.keyword_research import (KeywordResearchConfigError, KeywordResearchWorkflow,
                                                              market_from)
    try:
        state = KeywordResearchWorkflow().run(
            args.reports_dir, out_dir=args.out, site_url=args.site_url, only_pages=args.pages, competitors=args.competitor,
            market=market_from(args.country, args.location_code, args.language), max_iterations=args.max_iterations)
    except KeywordResearchConfigError as error:
        raise SystemExit(str(error))
    written = state.get('written') or {}
    summary = written.get('summary') or {}
    log('keyword research output: ' + str(written.get('out_dir')) + f" ({summary.get('pages_complete', 0)} complete, "
        f"{summary.get('pages_partial', 0)} partial pages; providers: {', '.join(summary.get('providers_available') or []) or 'none'})")


if __name__ == '__main__':
    main()
