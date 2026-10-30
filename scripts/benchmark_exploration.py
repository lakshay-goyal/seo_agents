"""Synthetic, offline comparison. No model calls, client repositories or retained fixtures."""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import statistics
import tempfile
import time
from unittest.mock import patch

from seo_agents.exploration.checks.agents.repository import snapshot_repository
from seo_agents.exploration.checks.agents.retrieval import encoded_size, repository_summary, repository_tools
from seo_agents.exploration.checks.graph import run_onpage_graph
from seo_agents.exploration.checks.tools import inventory
from seo_agents.exploration.checks.tools.helpers import REPORT_FILES


def main():
    with tempfile.TemporaryDirectory(prefix='seo-exploration-benchmark-') as directory:
        repo = Path(directory).resolve()
        for index in range(20):
            name = 'index.html' if index == 0 else f'page-{index}.html'
            (repo / name).write_text(f'<title>Page {index}</title><nav>Shared navigation</nav>'
                f'<main><h1>Topic {index}</h1><p>Unique page content {index}.</p><a href="/page-1">Related topic</a></main>')
        before, after, parse_counts = [], [], []
        with redirect_stdout(io.StringIO()):
            for _ in range(5):
                with patch.object(inventory, 'pages', wraps=inventory.pages) as pages:
                    begun = time.perf_counter()
                    old_reports = {category: inventory.inventory(repo, category) for category in REPORT_FILES}
                    before.append((time.perf_counter() - begun) * 1000)
                    old_count = pages.call_count
                with patch.object(inventory, 'pages', wraps=inventory.pages) as pages:
                    begun = time.perf_counter()
                    state = run_onpage_graph(repo, mode='static')
                    after.append((time.perf_counter() - begun) * 1000)
                    parse_counts.append((old_count, pages.call_count))
                assert old_reports == state['artifacts'], 'Seven report payloads differ'
        snapshot = snapshot_repository(repo)
        paths = list(snapshot['files'])[:8]
        old_metrics, new_metrics = {}, {}
        tools = {t.name: t for t in repository_tools(snapshot, metrics=old_metrics)}
        old_text = [tools['read_source'].invoke({'path': path})['lines'] for path in paths]
        tools = {t.name: t for t in repository_tools(snapshot, metrics=new_metrics)}
        new_text = [r['lines'] for r in tools['read_sources'].invoke({'paths': paths})['results']]
        assert old_text == new_text, 'Batched retrieval lost source text'
        large = dict(snapshot, files={f'websites/storefront/src/components/catalog/category_{i:04d}/descriptive_component.tsx': '' for i in range(2000)})
        large.pop('inventory')
        old_manifest = {'source_files': list(large['files']), 'assets': {}, 'limitations': []}
        compact = repository_summary(large, {'records': [], 'packages': [], 'parse_errors': []})
        print(json.dumps({
            'fixture': {'html_pages': 20, 'repeats': 5, 'network_requests': 0, 'model_calls': 0},
            'quality': {'seven_reports_identical': True, 'batched_source_text_identical': True},
            'page_composition_passes': {'before': parse_counts[0][0], 'after': parse_counts[0][1]},
            'known_eight_file_tool_round_trips': {'before': old_metrics['tool_calls'], 'after': new_metrics['tool_calls']},
            'initial_manifest_bytes_2000_paths': {'before': encoded_size(old_manifest), 'after': encoded_size(compact)},
            'wall_time_ms': {'before_median': round(statistics.median(before), 3), 'before_range': [round(min(before), 3), round(max(before), 3)],
                             'after_median': round(statistics.median(after), 3), 'after_range': [round(min(after), 3), round(max(after), 3)]},
            'limits': ['Synthetic offline baseline, not a paid-model/token or production performance benchmark.',
                       'After timing also includes new inventory/context/evidence/checkpoint work; before contains only seven legacy reports.',
                       'Agent mode still verifies accepted scripts with two worker executions per category.']
        }, indent=2))


if __name__ == '__main__':
    main()
