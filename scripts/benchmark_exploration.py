"""Offline benchmark of the site model (not the legacy LLM extractors) over the golden fixtures.

Per fixture: wall time per stage (snapshot, fingerprint, element index, URL manifest, full source-only
site model), URL count, output size in bytes and an approximate token count (bytes / 4). Hermetic: no model
calls, no network, temporary caches (same guard as scripts/score_fixtures.py).

    PYTHONPATH=src python scripts/benchmark_exploration.py [--fixture NAME ...] [--repeats 3] [--json out.json]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import statistics
import sys
import tempfile
import time
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
spec = importlib.util.spec_from_file_location('score_fixtures', ROOT / 'scripts' / 'score_fixtures.py')
scorer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scorer)


def timed(fn, *args, **kwargs):
    begun = time.perf_counter()
    value = fn(*args, **kwargs)
    return value, (time.perf_counter() - begun) * 1000


def bench_fixture(name, repeats):
    from seo_agents.exploration.repo_analysis.site_model import explore_site
    from seo_agents.exploration.repo_analysis.tools.element_index import build_element_index
    from seo_agents.exploration.repo_analysis.tools.framework import detect_frameworks
    from seo_agents.exploration.repo_analysis.tools.repository_snapshot import snapshot_repository
    from seo_agents.exploration.repo_analysis.url_manifest import build_manifest
    stages = {key: [] for key in ('snapshot', 'fingerprint', 'element_index', 'url_manifest', 'site_model')}
    result = {'fixture': name}
    with tempfile.TemporaryDirectory() as temp:
        repo = Path(temp) / name
        shutil.copytree(scorer.FIXTURES / name, repo, ignore=shutil.ignore_patterns('expected.json'))
        for _ in range(repeats):
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                snapshot, ms = timed(snapshot_repository, repo)
                stages['snapshot'].append(ms)
                fingerprint, ms = timed(detect_frameworks, snapshot)
                stages['fingerprint'].append(ms)
                _, ms = timed(build_element_index, snapshot)
                stages['element_index'].append(ms)
                (pages, _), ms = timed(build_manifest, snapshot, fingerprint)
                stages['url_manifest'].append(ms)
                out = Path(temp) / 'out'
                model, ms = timed(explore_site, repo, render=False, output_dir=out)
                stages['site_model'].append(ms)
        size = sum(p.stat().st_size for p in out.rglob('*.json')) if out.exists() else len(json.dumps(model))
        result.update(framework=fingerprint['primary'], urls=len(pages), output_bytes=size, approx_tokens=size // 4,
                      ms={key: round(statistics.median(values), 2) for key, values in stages.items()})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--fixture', action='append')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--json', type=Path)
    args = parser.parse_args(argv)
    rows = []
    with scorer.hermetic(True):
        for name in args.fixture or scorer.fixture_names():
            try:
                rows.append(bench_fixture(name, max(1, args.repeats)))
            except Exception as error:  # noqa: BLE001 - one broken fixture must not hide the others
                rows.append({'fixture': name, 'error': type(error).__name__ + ': ' + str(error)[:200]})
    for row in rows:
        if 'error' in row:
            print(f"{row['fixture']:<20} ERROR {row['error']}")
            continue
        ms = row['ms']
        print(f"{row['fixture']:<20} {row['framework']:<16} urls={row['urls']:<3} tokens~{row['approx_tokens']:<7} "
              f"snapshot={ms['snapshot']}ms manifest={ms['url_manifest']}ms model={ms['site_model']}ms")
    payload = {'model_calls': 0, 'network_requests': 0, 'repeats': args.repeats, 'fixtures': rows}
    if args.json:
        args.json.write_text(json.dumps(payload, indent=2) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
