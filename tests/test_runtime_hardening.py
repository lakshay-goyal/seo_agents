"""Security, runtime and PR-hygiene fixes from the audit (§4, §6 Runtime, §9 Hygiene, §15)."""

import http.server
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from seo_agents.exploration.repo_analysis.runtime import build_serve as bs
from seo_agents.exploration.repo_analysis.runtime import lighthouse, link_check
from seo_agents.exploration.repo_analysis.runtime.dom import summarize_html
from seo_agents.exploration.repo_analysis.runtime.render import chromium_args, close_browsers
from seo_agents.exploration.repo_analysis.tools.agent_retrieval import repository_tools, safe_compile


class SafeEnvTests(unittest.TestCase):
    def test_secrets_never_reach_repository_code(self):
        base = {'PATH': '/usr/bin', 'HOME': '/root', 'GITHUB_TOKEN': 'ghp_x', 'OPENROUTER_API_KEY': 'sk',
                'GH_TOKEN': 'x', 'LANGSMITH_API_KEY': 'k', 'AWS_SECRET_ACCESS_KEY': 'a', 'LC_ALL': 'C',
                'npm_config_registry': 'https://r', 'npm_config__authToken': 'tok', 'HTTPS_PROXY': 'http://p'}
        env = bs.safe_env({'NODE_ENV': 'production', 'MY_API_KEY': 'leak'}, base=base)
        for secret in ('GITHUB_TOKEN', 'OPENROUTER_API_KEY', 'GH_TOKEN', 'LANGSMITH_API_KEY', 'AWS_SECRET_ACCESS_KEY',
                       'npm_config__authToken', 'MY_API_KEY'):
            self.assertNotIn(secret, env)
        for kept in ('PATH', 'HOME', 'LC_ALL', 'npm_config_registry', 'HTTPS_PROXY', 'NODE_ENV'):
            self.assertIn(kept, env)
        self.assertEqual(env['NEXT_TELEMETRY_DISABLED'], '1')

    def test_build_step_sees_no_token_and_timeout_kills_the_group(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'GITHUB_TOKEN': 'ghp_secret'}):
            log = []
            self.assertTrue(bs.run(['bash', '-c', 'test -z "$GITHUB_TOKEN"'], directory, 10, log))
            began = time.time()
            self.assertFalse(bs.run(['bash', '-c', 'sleep 20 & sleep 20; wait'], directory, 1, log))
            self.assertLess(time.time() - began, 15)
            self.assertIn('process group killed', log[-1])


class PackageManagerTests(unittest.TestCase):
    def test_install_commands_per_manager(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(bs.install_command('yarn', root), ['yarn', 'install'])
            (root / 'yarn.lock').write_text('')
            self.assertEqual(bs.install_command('yarn', root, 1), ['yarn', 'install', '--frozen-lockfile'])
            self.assertEqual(bs.install_command('yarn', root, 4), ['yarn', 'install', '--immutable'])
            (root / '.yarnrc.yml').write_text('nodeLinker: node-modules')
            self.assertEqual(bs.install_command('yarn', root), ['yarn', 'install', '--immutable'])
            (root / 'package-lock.json').write_text('{}')
            self.assertEqual(bs.install_command('npm', root)[:2], ['npm', 'ci'])

    def test_package_manager_field_and_workspace_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'package.json').write_text(json.dumps({'packageManager': 'pnpm@9.1.0', 'engines': {'node': '>=20'}}))
            (root / 'pnpm-workspace.yaml').write_text('packages: [apps/*]')
            app = root / 'apps' / 'web'
            app.mkdir(parents=True)
            (app / 'package.json').write_text('{}')
            (app / '.nvmrc').write_text('20.11\n')
            self.assertEqual(bs.workspace_root(app, root), root.resolve())
            self.assertEqual(bs.package_manager_spec(root, 'npm'), ('pnpm', 9))
            self.assertEqual(bs.node_requirements(app, root), {'nvmrc': '20.11', 'engines': '>=20'})

    def test_preview_recipes_use_each_tools_flags(self):
        command, env = bs.preview_command('npm', 'start', 'next start', 4000)
        self.assertEqual(command, ['npm', 'run', 'start', '--', '-p', '4000', '-H', '127.0.0.1'])
        self.assertNotIn('--host', command)
        command, _ = bs.preview_command('pnpm', 'preview', 'vite preview', 4001)
        self.assertEqual(command, ['pnpm', 'run', 'preview', '--port', '4001', '--strictPort', '--host', '127.0.0.1'])
        command, env = bs.preview_command('npm', 'start', 'remix-serve ./build/server/index.js', 4002)
        self.assertEqual(command, ['npm', 'run', 'start'])
        self.assertEqual(env['PORT'], '4002')


class _Handler(http.server.BaseHTTPRequestHandler):
    status = 200

    def do_GET(self):
        self.send_response(self.status)
        self.end_headers()

    def log_message(self, *args):
        return


class ReadinessTests(unittest.TestCase):
    def serve(self, status):
        handler = type('H', (_Handler,), {'status': status})
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_address[1]}'

    def test_500_is_not_ready_but_200_is(self):
        self.assertFalse(bs.wait_for(self.serve(500), timeout=1.5))
        self.assertTrue(bs.wait_for(self.serve(200), timeout=3))


class PrebuiltAndInstrumentationTests(unittest.TestCase):
    def spa(self, root):
        (root / 'package.json').write_text(json.dumps({'dependencies': {'react-dom': '18'}, 'devDependencies': {'vite': '5'}}))
        (root / 'dist').mkdir()
        (root / 'dist' / 'index.html').write_text('<html><body><div id="root"></div><script src="/assets/a.js"></script></body></html>')
        return {'primary': 'react-spa', 'renderer': 'csr', 'package_json': 'package.json', 'build': {'output_dirs': ['dist']}}

    def test_committed_dist_is_flagged_stale_or_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fingerprint = self.spa(root)
            served = bs.build_and_serve(root, fingerprint, build=False)
            try:
                self.assertEqual(served.mode, 'static-dir')
                self.assertTrue(any('stale' in note for note in served.limitations))
            finally:
                served.close()
            with patch.dict(os.environ, {'SEO_MEASURE_PREBUILT': '0'}):
                self.assertEqual(bs.build_and_serve(root, fingerprint, build=False).mode, 'source-only')

    def test_instrumentation_wrapper_only_for_plain_vite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.spa(root)
            (root / 'vite.config.js').write_text('export default {}')
            log, limitations = [], []
            command, cleanup = bs.instrumented_build(root, {}, log, limitations)
            wrapper = root / bs.WRAPPER_NAME
            self.assertTrue(wrapper.is_file())
            text = wrapper.read_text()
            self.assertIn('data-seo-src', text)
            self.assertIn('vite.config.js', text)
            self.assertEqual(command[-2:], ['--config', bs.WRAPPER_NAME])
            cleanup()
            self.assertFalse(wrapper.exists())
            (root / 'package.json').write_text(json.dumps({'dependencies': {'next': '15', 'vite': '5'}}))
            command, _ = bs.instrumented_build(root, {}, log, limitations)
            self.assertIsNone(command)
            self.assertTrue(limitations)


class LinkCheckTests(unittest.TestCase):
    class Opener:
        def __init__(self, answers):
            self.answers, self.calls = answers, []

        def open(self, request, timeout=None):
            self.calls.append((request.get_method(), request.full_url))
            status, headers = self.answers[(request.get_method(), request.full_url)]
            from urllib.error import HTTPError
            from email.message import Message
            message = Message()
            for key, value in (headers or {}).items():
                message[key] = value
            if status >= 300:
                raise HTTPError(request.full_url, status, 'x', message, None)

            class Response:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *exc):
                    return False
            response = Response()
            response.status, response.headers = status, message
            return response

    def test_head_404_retries_with_get(self):
        opener = self.Opener({('HEAD', 'https://e.com/a'): (404, None), ('GET', 'https://e.com/a'): (200, None)})
        result = link_check.check_url('https://e.com/a', opener=opener)
        self.assertTrue(result['ok'])
        self.assertEqual(result['state'], 'ok')

    def test_linkedin_999_is_blocked_not_broken_or_redirected(self):
        url = 'https://www.linkedin.com/in/someone'
        opener = self.Opener({('HEAD', url): (999, None), ('GET', url): (999, None)})
        result = link_check.check_url(url, opener=opener)
        self.assertEqual(result['state'], 'blocked')
        self.assertIsNone(result['ok'])
        self.assertEqual(result['redirects'], 0)

    def test_external_sample_is_most_linked_first_and_spread(self):
        urls = [f'https://a.com/{i}' for i in range(10)] + ['https://z.com/popular', 'https://m.com/x']
        picked = link_check.sample_external(urls, 4, {'https://z.com/popular': 9})
        self.assertEqual(picked[0], 'https://z.com/popular')
        self.assertIn('https://m.com/x', picked)
        self.assertLessEqual(sum(u.startswith('https://a.com') for u in picked), 3)


class LighthouseTests(unittest.TestCase):
    def test_failed_run_keeps_successful_ones(self):
        report = {'lighthouseVersion': '12', 'audits': {'largest-contentful-paint': {'numericValue': 1200}}}
        calls = {'n': 0}

        def fake_run(args, **kwargs):
            calls['n'] += 1
            if calls['n'] == 2:
                raise lighthouse.subprocess.TimeoutExpired(args, 1)
            path = next(a for a in args if a.startswith('--output-path=')).split('=', 1)[1]
            Path(path).write_text(json.dumps(report))
        with patch.object(lighthouse, 'lighthouse_command', return_value=['lighthouse']), \
                patch.object(lighthouse.subprocess, 'run', side_effect=fake_run):
            result = lighthouse.run_cli('http://127.0.0.1/', 'mobile', 3, 5)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['runs'], 2)
        self.assertEqual(result['metrics']['lcp_ms'], 1200)

    def test_all_runs_failed_is_failed_and_overall_status(self):
        with patch.object(lighthouse, 'lighthouse_command', return_value=['lighthouse']), \
                patch.object(lighthouse.subprocess, 'run', side_effect=OSError('boom')):
            result = lighthouse.run_cli('http://127.0.0.1/', 'mobile', 2, 5)
        self.assertEqual((result['status'], result['runs']), ('failed', 0))
        self.assertEqual(lighthouse.overall_status({'mobile': result, 'desktop': result}), 'failed')
        with patch.object(lighthouse, 'measure_performance', side_effect=lambda url, **k: {'status': 'unavailable'}):
            self.assertEqual(lighthouse.measure_many(['a', 'b', 'a'])['a']['status'], 'unavailable')


class DomTests(unittest.TestCase):
    def test_source_hints_literal_main_and_landmarks(self):
        html = ('<main><nav><a href="/about" data-seo-src="src/components/PageRail.jsx:15:13">About</a></nav>'
                '<img src="x.png" data-src="https://cdn.e.com/x.png"><h2>Hi</h2></main><footer><a href="/f">F</a></footer>')
        out = summarize_html(html, 'https://e.com/')
        about, footer = out['links']
        self.assertEqual(about['src_hint'], 'src/components/PageRail.jsx:15:13')
        self.assertTrue(about['in_main'])
        self.assertTrue(about['in_chrome'])
        self.assertFalse(footer['in_main'])
        self.assertIsNone(out['images'][0]['src_hint'])  # a lazy-loader data-src URL is not a source location
        self.assertIn({'issue': 'nav-inside-main', 'count': 1}, out['landmarks']['issues'])

    def test_without_main_in_main_means_outside_chrome(self):
        out = summarize_html('<nav><a href="/a">A</a></nav><p><a href="/b">B</a></p>', 'https://e.com/')
        self.assertEqual([link['in_main'] for link in out['links']], [False, True])


class RenderTests(unittest.TestCase):
    def test_no_sandbox_flag_only_for_root(self):
        with patch('os.geteuid', return_value=1000):
            self.assertNotIn('--no-sandbox', chromium_args())
        with patch('os.geteuid', return_value=0):
            self.assertIn('--no-sandbox', chromium_args())
        close_browsers()  # safe when no pool was started


class RetrievalToolTests(unittest.TestCase):
    def tools(self, files):
        return {t.name: t for t in repository_tools({'files': files, 'assets': {}}, routes=[{'url': '/', 'template': 'a.html'}])}

    def test_dangerous_regex_is_refused(self):
        for pattern in ('(a+)+$', '(\\w*)*x', '(a)\\1', 'x' * 400):
            compiled, error = safe_compile(pattern)
            self.assertIsNone(compiled, pattern)
            self.assertTrue(error)
        self.assertIn('error', self.tools({'a.txt': 'aaa'})['grep'].invoke({'pattern': '(a+)+$', 'regex': True}))

    def test_grep_paginates_and_read_reports_long_lines(self):
        files = {'a.txt': '\n'.join('needle %d' % i for i in range(150)), 'b.txt': 'x' * 3000}
        tools = self.tools(files)
        first = tools['grep'].invoke({'pattern': 'needle'})
        self.assertEqual(len(first['matches']), 60)
        second = tools['grep'].invoke({'pattern': 'needle', 'cursor': first['next_cursor']})
        self.assertEqual(second['matches'][0]['line'], 61)
        read = tools['read'].invoke({'path': 'b.txt'})
        self.assertEqual(read['long_lines'], [{'line': 1, 'length': 3000}])
        self.assertTrue(read['truncated'])

    def test_query_index_explains_empty_results(self):
        tools = self.tools({'a.html': '<h1>Hi</h1>'})
        self.assertIn('Unknown page', tools['query_index'].invoke({'kind': 'element', 'page': '/missing'})['error'])
        self.assertIn('error', tools['query_index'].invoke({'kind': 'element', 'tag': 'video'}))
        schema = tools['query_index'].tool_call_schema.model_json_schema()
        self.assertEqual(schema['properties']['kind']['enum'], ['element', 'route', 'ref', 'data', 'i18n'])
        self.assertTrue(schema['properties']['page'].get('description'))


if __name__ == '__main__':
    unittest.main()
