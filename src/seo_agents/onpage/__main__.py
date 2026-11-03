"""CLI: python -m seo_agents.onpage <seo_reports dir> [--site-url URL] [--pages ID|URL ...] [--publish]

--publish commits seo_reports/onpage/ in the git checkout that contains the reports dir and pushes the current
branch (use a checkout of the exploration PR branch so the proposals land on the same PR).
"""

import argparse
import os
from pathlib import Path

from seo_agents.log import log


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m seo_agents.onpage')
    parser.add_argument('reports_dir', help='seo_reports/ folder (or its site_model/ folder)')
    parser.add_argument('--out', help='output folder (default: <seo_reports>/onpage)')
    parser.add_argument('--site-url', help='canonical origin, e.g. https://www.example.com (default: final URL from the JSON)')
    parser.add_argument('--pages', nargs='*', help='limit to these page ids or URLs')
    parser.add_argument('--run-date', help='ISO date used for freshness/dateModified (default: today)')
    parser.add_argument('--publish', action='store_true', help='commit seo_reports/onpage and push the current branch')
    args = parser.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[3] / '.env')
        load_dotenv()
    except ImportError:
        pass
    for key in ('OPENROUTER_SMALL_MODEL', 'OPENROUTER_MID_MODEL', 'OPENROUTER_MODEL', 'SEO_ONPAGE_TIER'):
        if os.environ.get(key) == '':
            del os.environ[key]  # empty .env entries mean "use the default", not "model ''"
    if not os.getenv('OPENROUTER_API_KEY'):
        raise SystemExit('OPENROUTER_API_KEY is not set (.env); the content writer needs a model.')
    from seo_agents.onpage.graph import run_onpage
    state = run_onpage(args.reports_dir, out_dir=args.out, site_url=args.site_url, run_date=args.run_date, only_pages=args.pages)
    written = state.get('written') or {}
    log('onpage output: ' + str(written.get('out_dir')))
    if args.publish:
        from seo_agents.onpage.publish import publish
        log('published: ' + publish(written['out_dir']))


if __name__ == '__main__':
    main()
