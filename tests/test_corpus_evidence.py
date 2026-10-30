"""Ground-truth coverage, retrieval, reuse and runtime boundary checks."""

from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from seo_agents.exploration.checks.agents.context import build_context
from seo_agents.exploration.checks.agents.http_probe import probe
from seo_agents.exploration.checks.agents.repository import snapshot_repository
from seo_agents.exploration.checks.agents.retrieval import encoded_size, repository_summary, repository_tools
from seo_agents.exploration.checks.agents.verification import execute_script
from seo_agents.exploration.checks.evidence import build_evidence, query_evidence, resolve_destination
from seo_agents.exploration.checks.graph import ExplorationIncomplete, run_onpage_graph
from seo_agents.exploration.checks.technical.runtime import observe_runtime
from seo_agents.exploration.inspect import main


class CorpusEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.write('index.html', '<html lang="en"><head><title>Tea shop</title><meta name="keywords" content="jasmine, tea"></head>\n'
                   '<body><nav>Shared navigation</nav><main><h1>Jasmine tea</h1><p>Organic jasmine tea.</p>'
                   '<a href="/about?lang=en#story">Our story</a><a href="/about?lang=en#other">Story again</a>'
                   '<a href="https://elsewhere.test/page">External</a></main></body></html>')
        self.write('about.html', '<title>Our story</title><nav>Shared navigation</nav><main><h1>Growers</h1><p>Our growing methods.</p></main>')

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def state(self):
        return run_onpage_graph(self.repo, mode='static')

    def test_inventory_continues_after_capture_cap_and_records_real_exclusions(self):
        self.write('.vitepress/config.ts', 'export default {title: "Docs"}')
        self.write('.next/routes-manifest.json', '{"staticRoutes":[]}')
        self.write('.env', 'SECRET=do-not-read')
        self.write('node_modules/vendor/index.js', 'not in scope')
        for index in range(8):
            self.write(f'content/{index}.md', 'Source content ' * 30)
        snapshot = snapshot_repository(self.repo, max_files=2)
        rows = {r['path']: r for r in snapshot['inventory']}
        self.assertIn('.next/routes-manifest.json', rows)
        self.assertIn('.vitepress/config.ts', rows)
        self.assertEqual(rows['.env']['reason'], 'credential-or-secret')
        self.assertEqual(rows['node_modules']['kind'], 'subtree')
        self.assertIn('content/7.md', rows)
        self.assertTrue(snapshot['coverage']['inventory_complete_within_scope'])
        self.assertFalse(snapshot['coverage']['capture_complete_within_scope'])
        self.assertGreater(snapshot['coverage']['file_status_counts']['not_loaded'], 0)

    def test_unicode_capture_boundary_is_truncated_instead_of_lost(self):
        self.write('content.txt', '茶' * 100)
        snapshot = snapshot_repository(self.repo, max_file_bytes=7)
        self.assertEqual(snapshot['files']['content.txt'], '茶茶')
        row = next(r for r in snapshot['inventory'] if r['path'] == 'content.txt')
        self.assertEqual(row['status'], 'truncated')

    def test_non_json_numeric_constants_remain_errors_and_do_not_poison_export(self):
        self.write('invalid.json', '{"value":NaN}')
        self.write('schema.html', '<script type="application/ld+json">{"value":Infinity}</script>')
        context = build_context(snapshot_repository(self.repo))
        self.assertTrue(any(r['file'] == 'invalid.json' for r in context['parse_errors']))
        self.assertEqual(next(r for r in context['records'] if r['kind'] == 'structured-data')['parse_status'], 'invalid-json')
        json.dumps(context, allow_nan=False)

    def test_context_preserves_json_pointer_import_data_call_and_malformed_jsonld(self):
        self.write('src/menu.json', '{"items":[{"slug":"green","a/b~c":"Organic tea"}]}')
        self.write('src/layout.ts', 'import menu from "./menu.json"; export const data = fetch("https://cms.test/items");')
        self.write('structured.html', '<script type="application/ld+json">{"@type":</script>')
        self.write('sitemap.xml', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://example.test/orphan</loc></url></urlset>')
        context = build_context(snapshot_repository(self.repo))
        pointer = next(r for r in context['records'] if r.get('pointer') == '/items/0/a~1b~0c')
        self.assertEqual(pointer['value'], 'Organic tea')
        dependency = next(r for r in context['records'] if r['kind'] == 'module-dependency')
        self.assertEqual(dependency['resolved_file'], 'src/menu.json')
        self.assertEqual(next(r for r in context['records'] if r['kind'] == 'data-source-call')['specifier'], 'https://cms.test/items')
        structured = next(r for r in context['records'] if r['kind'] == 'structured-data')
        self.assertEqual(structured['parse_status'], 'invalid-json')
        self.assertEqual(structured['raw'], '{"@type":')
        self.assertEqual(next(r for r in context['records'] if r['kind'] == 'sitemap-url')['value'], 'https://example.test/orphan')

    def test_long_line_and_bulk_json_are_recoverable_with_byte_bounded_queries(self):
        original = ('茶"\\' * 9000) + '\nlast line'
        self.write('long.txt', original)
        self.write('data.json', json.dumps({'body': 'Complete value ' * 3000}))
        snapshot = snapshot_repository(self.repo)
        context = build_context(snapshot)
        tools = {t.name: t for t in repository_tools(snapshot, context=context)}
        parts, line, column = [], 1, 0
        for _ in range(100):
            result = tools['read_source'].invoke({'path': 'long.txt', 'start_line': line, 'start_column': column})
            self.assertLessEqual(encoded_size(result), 16_000)
            parts.append(result['lines'])
            line, column = result['next_line'], result['next_column']
            if line is None:
                break
        self.assertEqual(''.join(parts), original)
        preview = tools['query_context'].invoke({'kind': 'json-value', 'prefix': 'data.json'})
        record_id = preview['records'][0]['record_id']
        offset, chunks = 0, []
        while offset is not None:
            result = tools['read_context_record'].invoke({'record_id': record_id, 'offset': offset})
            self.assertLessEqual(encoded_size(result), 16_000)
            chunks.append(result['json_text'])
            offset = result['next_offset']
        self.assertEqual(json.loads(''.join(chunks))['value'], 'Complete value ' * 3000)

    def test_batch_reads_reduce_round_trips_at_identical_source_coverage(self):
        paths = [str(self.write(f'components/{i}.ts', f'export const value = {i};').relative_to(self.repo)) for i in range(8)]
        snapshot = snapshot_repository(self.repo)
        baseline_metrics, batch_metrics = {}, {}
        baseline = {t.name: t for t in repository_tools(snapshot, metrics=baseline_metrics)}
        batched = {t.name: t for t in repository_tools(snapshot, metrics=batch_metrics)}
        expected = [baseline['read_source'].invoke({'path': path})['lines'] for path in paths]
        result = batched['read_sources'].invoke({'paths': paths})
        self.assertEqual([row['lines'] for row in result['results']], expected)
        self.assertEqual((baseline_metrics['tool_calls'], batch_metrics['tool_calls']), (8, 1))
        self.assertLessEqual(encoded_size(result), 16_000)

    def test_manifest_is_compact_at_equal_inventory_coverage(self):
        files = {f'websites/storefront/src/components/catalog/category_{i:04d}/descriptive_component.tsx': 'export const x = 1;' for i in range(2000)}
        snapshot = {'files': files, 'assets': {}, 'limitations': [], 'sha256': 'fixture'}
        summary = repository_summary(snapshot, {'records': [], 'packages': [], 'parse_errors': []})
        old = {'source_files': list(files), 'assets': {}, 'limitations': []}
        self.assertEqual(summary['source_files'], len(files))
        self.assertLess(encoded_size(summary), encoded_size(old) // 10)

    def test_static_pipeline_parses_pages_once_and_preserves_ground_truth_content(self):
        from seo_agents.exploration.checks.tools import inventory
        with patch.object(inventory, 'pages', wraps=inventory.pages) as pages:
            state = self.state()
        self.assertEqual(pages.call_count, 1)
        self.assertEqual({p['url'] for p in state['artifacts']['urls']['pages']}, {'/', '/about'})
        content = next(p for p in state['artifacts']['content']['pages'] if p['url'] == '/')
        self.assertIn('Organic jasmine tea.', [b['text'] for b in content['blocks']])
        evidence = state['evidence']
        self.assertIs(evidence['reports'], state['artifacts'])
        home = next(p for p in evidence['pages'] if p['url'] == '/')
        self.assertEqual(home['keywords']['observed_terms']['jasmine'], 2)
        self.assertNotIn('navigation', home['keywords']['observed_terms'])
        self.assertEqual(home['keywords']['declared_meta_keywords'][0]['value'], 'jasmine, tea')
        self.assertGreater(evidence['coverage']['performance']['pending'], 0)

    def test_url_resolution_preserves_queries_path_case_and_domain_scope(self):
        value = resolve_destination('../About?x=1#team', '/docs/page', 'https://example.test', '/docs/base/')
        self.assertEqual(value['resolved_url'], 'https://example.test/docs/About?x=1#team')
        self.assertEqual(value['destination'], 'https://example.test/docs/About?x=1')
        self.assertEqual(resolve_destination('https://example.test.evil.test/a', '/', 'https://example.test')['kind'], 'external')
        self.assertEqual(resolve_destination('//example.test/a', '/', 'https://example.test')['kind'], 'internal')

    def test_dynamic_families_are_not_measured_and_known_instances_are(self):
        state = self.state()
        for report in state['artifacts'].values():
            template = copy.deepcopy(report['pages'][0])
            template.update(url='/products/[slug]', is_dynamic=True, instance_status='template')
            instance = copy.deepcopy(template)
            instance.update(url='/products/green', instance_status='concrete', route_family='/products/[slug]')
            report['pages'].extend([template, instance])
        evidence = build_evidence(state, site_url='https://example.test')
        urls = {row['url'] for row in evidence['concrete_urls']}
        self.assertIn('https://example.test/products/green', urls)
        self.assertNotIn('https://example.test/products/[slug]', urls)
        self.assertTrue(next(p for p in evidence['pages'] if p['url'] == '/products/green')['no_incoming_link_candidate'])
        self.assertEqual(len(evidence['unresolved_route_families']), 1)
        self.assertIsNone(evidence['coverage']['url_discovery']['possible_url_denominator'])

    def test_runtime_attempts_every_eligible_url_or_explicitly_leaves_it_pending(self):
        evidence = build_evidence(self.state(), site_url='https://example.test')
        fetcher = Mock(side_effect=[{'status': 'success', 'http_status': 200, 'initial_html': '<title>Deployed title</title>',
                                    'request_to_headers_ms': 20}, TimeoutError('slow')])
        observe_runtime(evidence, site_url='https://example.test', max_pages=2, fetcher=fetcher)
        self.assertEqual(fetcher.call_count, 2)
        coverage = evidence['coverage']['performance']
        self.assertEqual(coverage['states']['success'], 1)
        self.assertEqual(coverage['states']['timeout'], 1)
        self.assertEqual(coverage['pending'], coverage['eligible_concrete_urls'] - 2)
        self.assertFalse(coverage['browser_performance_complete'])
        self.assertEqual(evidence['runtime']['ledger'][1]['metrics']['request_to_headers_ms'], None)
        self.assertEqual(evidence['runtime']['responses'][0]['initial_html'], '<title>Deployed title</title>')

    def test_http_probe_does_not_follow_cross_origin_redirect(self):
        response = Mock(code=302, headers={'Location': 'https://outside.test/target'})
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response
        with patch('seo_agents.exploration.checks.agents.http_probe.build_opener', return_value=opener):
            result = probe({'url': 'https://example.test/', 'timeout': 1, 'max_body_bytes': 100})
        self.assertEqual(result['status'], 'redirect-out-of-scope')
        self.assertEqual(opener.open.call_count, 1)

    def test_http_baseline_measures_initial_response_without_inventing_browser_metrics(self):
        response = Mock(code=200, headers={'Content-Type': 'text/html; charset=utf-8', 'X-Robots-Tag': 'noindex'})
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.side_effect = [b'<', b'title>Runtime</title>']
        opener = Mock()
        opener.open.return_value = response
        with patch('seo_agents.exploration.checks.agents.http_probe.build_opener', return_value=opener), patch(
                'seo_agents.exploration.checks.agents.http_probe.time.perf_counter', side_effect=[0, 0.02, 0.035, 0.05]):
            result = probe({'url': 'https://example.test/', 'timeout': 1, 'max_body_bytes': 100})
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['request_to_headers_ms'], 20)
        self.assertEqual(result['request_to_first_body_byte_ms'], 35)
        self.assertEqual(result['initial_html'], '<title>Runtime</title>')
        self.assertEqual(result['headers']['x-robots-tag'], 'noindex')
        self.assertNotIn('lcp', result)

    def test_runtime_excludes_out_of_scope_input_even_for_a_caller_constructed_bundle(self):
        bundle = {'concrete_urls': [{'url': 'https://outside.test/', 'fetch': 'pending'}],
                  'coverage': {}, 'unresolved_route_families': []}
        fetcher = Mock()
        observe_runtime(bundle, site_url='https://example.test', fetcher=fetcher)
        fetcher.assert_not_called()
        self.assertEqual(bundle['runtime']['ledger'][0]['status'], 'excluded')

    def test_script_accesses_and_html_cache_are_observed_not_guessed(self):
        snapshot = snapshot_repository(self.repo)
        source = 'def extract(files, assets):\n    value = files["index.html"]\n    parse_html(value)\n    parse_html(value)\n    return {"extraction": {}}'
        result = execute_script(source, snapshot)
        self.assertEqual(result['extraction']['script_access']['files'], ['index.html'])
        self.assertEqual(result['extraction']['script_access']['html_parsing'], {'calls': 1, 'cache_hits': 1})

    def test_interrupted_scan_resumes_completed_categories_and_rejects_changed_shared_source(self):
        from seo_agents.exploration.checks.tools import inventory
        original = inventory.inventory
        def interrupt(repo, category, **kwargs):
            if category == 'metadata':
                raise RuntimeError('synthetic interruption')
            return original(repo, category, **kwargs)
        with patch.object(inventory, 'inventory', side_effect=interrupt), self.assertRaises(ExplorationIncomplete) as caught:
            self.state()
        checkpoint = caught.exception.checkpoint
        self.assertEqual(checkpoint['checkpoint']['completed'], ['urls', 'content', 'page_structure', 'internal_links'])
        with patch.object(inventory, 'inventory', wraps=original) as extract:
            resumed = run_onpage_graph(self.repo, mode='static', resume=checkpoint)
        self.assertEqual([call.args[1] for call in extract.call_args_list], ['metadata', 'media', 'eeat'])
        self.assertEqual(len(resumed['artifacts']), 7)
        self.write('shared/layout.ts', 'export const changed = true;')
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            run_onpage_graph(self.repo, mode='static', resume=resumed)

    def test_read_only_cli_exports_json_and_queries_without_model_or_source(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main([str(self.repo), '--mode', 'static']), 0)
        bundle = json.loads(output.getvalue())
        self.assertEqual(bundle['kind'], 'seo-exploration-evidence')
        result = query_evidence(bundle, '/reports/content/pages/0')
        self.assertIn('blocks', json.loads(result['json_text']))
        self.assertLessEqual(encoded_size(query_evidence(bundle, '/context/records')), 16_000)

    def test_oversized_route_record_advances_and_has_lossless_recovery(self):
        snapshot = snapshot_repository(self.repo)
        routes = [{'url': '/' + 'x' * 30000, 'template': 'index.html', 'is_dynamic': False}]
        tools = {t.name: t for t in repository_tools(snapshot, routes=routes)}
        result = tools['list_routes'].invoke({})
        self.assertTrue(result['records'][0]['oversized_record'])
        self.assertIsNone(result['next_offset'])
        self.assertLessEqual(encoded_size(result), 16_000)
        chunks, offset = [], 0
        while offset is not None:
            chunk = tools['read_index_record'].invoke({'collection': 'routes', 'record_id': 0, 'offset': offset})
            chunks.append(chunk['json_text'])
            offset = chunk['next_offset']
        self.assertEqual(json.loads(''.join(chunks)), routes[0])

    def test_invalid_runtime_scope_is_rejected_before_model_work(self):
        with patch('seo_agents.exploration.checks.agents.extractor.get_chat_model') as model:
            with self.assertRaisesRegex(ValueError, 'HTTP'):
                run_onpage_graph(self.repo, mode='agent', site_url='file:///private/site')
            model.assert_not_called()

    def test_resume_does_not_claim_reuse_of_a_partial_capture(self):
        with patch('seo_agents.exploration.checks.agents.repository.MAX_FILE_BYTES', 5):
            state = self.state()
            with self.assertRaisesRegex(ValueError, 'partial source capture'):
                run_onpage_graph(self.repo, mode='static', resume=state)

    def test_asset_only_repository_retains_inventory_and_pending_phases_without_model_work(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'handbook.pdf').write_bytes(b'not-a-text-page')
            snapshot = snapshot_repository(directory)
            self.assertEqual(snapshot['files'], {})
            output = io.StringIO()
            with redirect_stdout(output), patch('seo_agents.exploration.checks.agents.extractor.get_chat_model') as model:
                self.assertEqual(main([directory, '--mode', 'agent']), 2)
                model.assert_not_called()
            bundle = json.loads(output.getvalue())
            self.assertEqual(bundle['repository']['inventory'][0]['path'], 'handbook.pdf')
            self.assertEqual(bundle['coverage']['extraction']['content']['status'], 'pending')


if __name__ == '__main__':
    unittest.main()
