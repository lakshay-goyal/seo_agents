"""Read-only exploration/export/query interface; never publishes a PR."""

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys

from seo_agents.exploration.checks.evidence import build_evidence, query_evidence
from seo_agents.exploration.checks.graph import ExplorationIncomplete, run_onpage_graph


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path, help='Checkout path, or saved evidence JSON with --query')
    parser.add_argument('--mode', choices=['agent', 'static'], default=None)
    parser.add_argument('--site-url', help='Explicit authorized HTTP(S) runtime; omission makes no network requests')
    parser.add_argument('--max-runtime-pages', type=int, default=100)
    parser.add_argument('--runtime-timeout', type=float, default=12)
    parser.add_argument('--resume', type=Path, help='Previous evidence JSON from the same unchanged snapshot/extractor')
    parser.add_argument('--query', metavar='JSON_POINTER', help='Query a saved bundle without source, a model or network')
    parser.add_argument('--offset', type=int, default=0, help='Query record offset, or character offset for a scalar/object')
    args = parser.parse_args(argv)
    if args.query is not None:
        result = query_evidence(json.loads(args.path.read_text()), args.query, offset=args.offset)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    resume = json.loads(args.resume.read_text()) if args.resume else None
    status = 0
    with redirect_stdout(sys.stderr):
        try:
            state = run_onpage_graph(args.path, mode=args.mode, site_url=args.site_url,
                max_runtime_pages=args.max_runtime_pages, runtime_timeout=args.runtime_timeout, resume=resume)
            result = state['evidence']
        except ExplorationIncomplete as error:
            state = error.checkpoint
            result = build_evidence(state, site_url=args.site_url)
            result.update(checkpoint=state['checkpoint'], failure={'type': type(error.__cause__).__name__,
                                                                 'message': str(error.__cause__)[:2000]})
            result['coverage']['performance'] = {'status': 'pending', 'reason': 'Source exploration did not finish',
                                                'eligible_concrete_urls': len(result['concrete_urls'])}
            status = 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return status


if __name__ == '__main__':
    sys.exit(main())
