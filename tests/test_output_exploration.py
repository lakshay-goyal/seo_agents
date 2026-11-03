"""Sharded exploration output, served probes on the canonical host, edit verification and scope annotation."""

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from seo_agents.repo_analysis.site_model import site_model_builder as sm
from seo_agents.repo_analysis.output import load_exploration, write_exploration
from seo_agents.repo_analysis.output.schema_validation import SCHEMAS, validate

FIXTURES = Path(__file__).parent / 'fixtures' / 'sites'
ENV = {'SEO_GAP_WORKERS': '0', 'SEO_LIGHTHOUSE': '0', 'SEO_RENDER': '0', 'SEO_BUILD': '0', 'SEO_SITE_URL': '',
       'SEO_GAP_CACHE': '0', 'OPENROUTER_API_KEY': ''}


def fake_site(routes):
    """fetch(url) -> response in the http_probe shape; routes: {url: response}."""
    def fetch(url, timeout=10, **_):
        return routes.get(url, {'status': 'http-error', 'http_status': 404, 'final_url': url, 'redirect_chain': [], 'headers': {}})
    return fetch


SHELL = '<!doctype html><html><head><title>X</title></head><body><div id="root"></div></body></html>'


class ServedProbeTests(unittest.TestCase):
    """PR #8 shape: apex 307 -> www, catch-all rewrite, HSTS only on www, both slash forms 200."""

    def setUp(self):
        ok = {'status': 'success', 'http_status': 200, 'redirect_chain': [], 'initial_html': SHELL,
              'headers': {'strict-transport-security': 'max-age=63072000', 'server': 'Vercel'}}
        www = 'https://www.example.com'
        self.fetch = fake_site({
            'https://example.com/': {'status': 'redirect-out-of-scope', 'http_status': 307, 'final_url': www + '/',
                                     'redirect_chain': [{'url': 'https://example.com/', 'http_status': 307, 'target': www + '/'}]},
            www + '/': {**ok, 'final_url': www + '/'},
            www + '/seo-agents-probe-' + __import__('hashlib').sha1(b'https://example.com').hexdigest()[:8]: ok,
            www + '/about': ok, www + '/about/': ok,
            'http://www.example.com/': {'status': 'redirect-out-of-scope', 'http_status': 308, 'final_url': www + '/',
                                        'redirect_chain': [{'url': 'http://www.example.com/', 'http_status': 308, 'target': www + '/'}]},
        })
        self.text = lambda url, timeout=10, **_: {'status': 200, 'content_type': 'text/html; charset=utf-8', 'body': SHELL}

    def test_probes_run_on_final_host_and_detect_spa_fallbacks(self):
        probes = sm.probe_site('https://example.com', ['/blog/[slug]', '/about'], fetch=self.fetch, text_fetch=self.text)
        host = probes['canonical_host']
        self.assertEqual(host['value'], 'https://www.example.com')
        self.assertEqual(host['host_redirect'], {'from': 'example.com', 'to': 'www.example.com', 'status': 307, 'permanent': False})
        self.assertEqual(probes['robots_txt']['value'], 'spa-shell-fallback')
        self.assertEqual(probes['sitemap_xml']['value'], 'spa-shell-fallback')
        self.assertTrue(probes['soft_404']['soft_404'])
        self.assertEqual(probes['soft_404']['kind'], 'spa-catch-all')
        self.assertEqual(probes['trailing_slash']['sample'], '/about')
        self.assertEqual(probes['trailing_slash']['value'], 'duplicate (both 200)')
        self.assertEqual(probes['hsts']['value'], 'max-age=63072000')
        self.assertTrue(probes['http_to_https']['value'] and probes['http_to_https']['permanent'])
        self.assertEqual(probes['robots_txt']['method'], 'raw@www.example.com')

    def test_trailing_slash_not_run_without_a_real_route(self):
        probes = sm.probe_site('https://example.com', ['/', '/blog/:slug'], fetch=self.fetch, text_fetch=self.text)
        self.assertEqual(probes['trailing_slash']['status'], 'not-run')

    def test_live_sitemap_is_same_site_only(self):
        requested = []

        def text(url, timeout=10, **_):
            requested.append(url)
            if url.endswith('/robots.txt'):
                return {'status': 200, 'content_type': 'text/plain', 'body': 'Sitemap: https://evil.test/s.xml\nSitemap: https://www.example.com/a.xml'}
            if url.endswith('/a.xml'):
                return {'status': 200, 'content_type': 'application/xml', 'body':
                        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://www.example.com/x</loc></url>'
                        '<url><loc>https://evil.test/y</loc></url></urlset>'}
            return {'status': 404, 'body': ''}
        with patch.object(sm, 'resolve_canonical_host', lambda base, fetch=None: {'final_origin': 'https://www.example.com'}):
            urls = sm.live_sitemap_urls('https://example.com', fetch=text)
        self.assertEqual(urls, [('https://www.example.com/x', 'sitemap:live')])
        self.assertNotIn('https://evil.test/s.xml', requested)
        self.assertEqual(len(requested), len(set(requested)))


class VerifyEditTests(unittest.TestCase):
    files = {'index.html': '<!doctype html>\n<html>\n<head>\n<title>Home</title>\n</head>\n<body></body>\n</html>',
             'src/App.jsx': 'export default function App() {\n  return <h2>Projects</h2>;\n}',
             'locales/en.json': '{"hero": {"title": "Big sale"}}'}

    def test_placeholders_and_values(self):
        verify = sm.verify_edit
        self.assertEqual(verify(self.files, {'kind': 'literal', 'file': 'index.html', 'line': 4}, 'Home')[0], 'grounded')
        self.assertEqual(verify(self.files, {'kind': 'literal', 'file': 'index.html', 'line': 1}, 'Elsewhere')[0], 'failed')
        self.assertEqual(verify(self.files, {'kind': 'computed', 'file': 'src/App.jsx', 'line': 1,
                                             'via': 'no source location matched the served value'}, 'x')[0], 'failed')
        self.assertEqual(verify(self.files, {'kind': 'computed', 'file': 'src/App.jsx', 'line': 2, 'via': 'map()'}, 'x')[0], 'heuristic')
        self.assertEqual(verify(self.files, {'kind': 'i18n', 'file': 'src/App.jsx', 'line': 2, 'i18n_key': 'hero.title'}, 'Big sale')[0], 'heuristic')
        self.assertEqual(verify(self.files, {'kind': 'i18n', 'file': 'src/App.jsx', 'line': 2, 'i18n_key': 'hero.title'}, 'Nope')[0], 'failed')
        self.assertEqual(verify(self.files, {'kind': 'literal', 'file': 'index.html', 'line': 4, 'via': 'title-template'}, 'Home | Acme')[0], 'grounded')

    def test_add_edit_moves_from_line_one_to_head_close(self):
        edit = {'kind': 'add', 'file': 'index.html', 'line': 1, 'anchor': '<Helmet> in page component'}
        self.assertEqual(sm.verify_edit(self.files, edit, None)[0], 'grounded')
        self.assertEqual((edit['line'], edit['insert']), (5, 'before </head>'))


def page(pid, url, template, title_edit, headings=(), composition=()):
    return {'id': pid, 'url': url, 'template': template, 'instance_status': 'static', 'composition': list(composition),
            'head': {'title': {'value': 'T', 'edit': title_edit}}, 'headings': list(headings), 'content': {'blocks': []},
            'links': {'out': []}, 'images': []}


class ScopeTests(unittest.TestCase):
    def test_spa_shell_template_and_data_scopes(self):
        shell = {'kind': 'literal', 'file': 'index.html', 'line': 14}
        a = page('p1', '/', 'src/App.jsx', dict(shell), [
            {'level': 3, 'text': 'A', 'edit': {'kind': 'literal', 'file': 'src/App.jsx', 'line': 40}},
            {'level': 3, 'text': 'B', 'edit': {'kind': 'literal', 'file': 'src/App.jsx', 'line': 40}},
            {'level': 2, 'text': 'C', 'edit': {'kind': 'json', 'file': 'data.json', 'line': 3, 'pointer': '/0/title'}}],
            ['index.html', 'src/App.jsx', 'src/Rail.jsx'])
        b = page('p2', '/about', 'src/App.jsx', dict(shell), [{'level': 2, 'text': 'Nav', 'edit': {'kind': 'literal', 'file': 'src/Rail.jsx', 'line': 3}}],
                 ['index.html', 'src/App.jsx', 'src/Rail.jsx'])
        a['headings'].append({'level': 2, 'text': 'Nav', 'edit': {'kind': 'literal', 'file': 'src/Rail.jsx', 'line': 3}})
        sm.annotate_scope([a, b], {'primary': 'react-spa', 'renderer': 'csr'})
        self.assertEqual(a['head']['title']['edit']['scope'], 'shell')
        self.assertEqual(a['head']['title']['edit']['shared_by'], ['p1', 'p2'])
        self.assertEqual((a['headings'][0]['edit']['scope'], a['headings'][0]['edit']['template_n']), ('template', 2))
        self.assertEqual(a['headings'][2]['edit']['scope'], 'data')
        self.assertEqual(a['headings'][3]['edit']['scope'], 'shell')  # Rail.jsx renders on every route
        self.assertEqual(a['head_scope'], 'shared-shell')

    def test_static_site_index_pages_are_not_shell(self):
        a = page('p1', '/', 'index.html', {'kind': 'literal', 'file': 'index.html', 'line': 1}, composition=['index.html'])
        b = page('p2', '/about', 'about/index.html', {'kind': 'literal', 'file': 'about/index.html', 'line': 1}, composition=['about/index.html'])
        sm.annotate_scope([a, b], {'primary': 'static-html', 'renderer': 'static'})
        self.assertEqual(b['head']['title']['edit']['scope'], 'literal')
        self.assertEqual(a['head_scope'], 'per-route')


@patch.dict(os.environ, ENV)
class ShardedOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def explore(self, fixture):
        repo = Path(self.temp.name) / fixture
        shutil.copytree(FIXTURES / fixture, repo)
        with redirect_stdout(io.StringIO()):
            model = sm.explore_site(repo, measure=False, output_dir=repo / 'seo_reports' / 'site_model')
        return repo, model

    def test_layout_schemas_and_referential_integrity(self):
        for fixture in ('next-app-router', 'vite-react-spa', 'hugo'):
            with self.subTest(fixture=fixture):
                repo, model = self.explore(fixture)
                out = repo / 'seo_reports' / 'exploration'
                manifest = json.loads((out / 'manifest.json').read_text())
                self.assertEqual(manifest['validation'].get('schema_errors', []), [])
                self.assertEqual(manifest['validation'].get('ref_errors', []), [])
                self.assertTrue(manifest['lens']['degraded'])
                for name in ('summary.json', 'routes.json', 'site.json', 'entity.json', 'images.json', 'links.json', 'metadata.json',
                             'structured_data.json', 'performance.json', 'keywords.json', 'schemas/page.schema.json'):
                    self.assertTrue((out / name).exists(), name)
                routes = json.loads((out / 'routes.json').read_text())['routes']
                self.assertEqual(len(routes), len(model['pages']))
                for row in routes:
                    shard = json.loads((out / row['file']).read_text())
                    self.assertEqual(validate(shard, SCHEMAS['page']), [])
                summary = (out / 'summary.json').read_text()
                self.assertLess(len(summary) // 4, 4200)
                self.assertNotIn(str(repo), (out / 'site.json').read_text() + summary + (out / 'manifest.json').read_text())
                self.assertIn('_element_index', model)
                self.assertTrue((repo / 'seo_reports' / 'site_model' / 'index.json').exists())  # back-compat layout kept

    def test_edits_carry_scope_verify_and_evidence(self):
        repo, _ = self.explore('next-app-router')
        out = repo / 'seo_reports' / 'exploration'
        meta = json.loads((out / 'metadata.json').read_text())
        title = next(r['title'] for r in meta['routes'].values() if r.get('title', {}).get('value') == 'Home | Acme')
        self.assertIn(title['edit']['verify'], {'grounded', 'heuristic'})
        self.assertIn('scope', title['edit'])
        evidence = json.loads((out / 'evidence' / (title['edit']['evidence'] + '.json')).read_text())
        self.assertEqual(evidence['file'], title['edit']['file'])
        loaded = load_exploration(out, include=['links.json'])
        self.assertEqual(set(loaded), {'manifest.json', 'summary.json', 'routes.json', 'links.json'})

    def test_summary_ranks_shared_spa_head_issue(self):
        model = {'site': {'framework': {'primary': 'react-spa', 'renderer': 'csr'}, 'served': {
                     'soft_404': {'soft_404': True, 'kind': 'spa-catch-all'}, 'robots_txt': {'value': 'spa-shell-fallback'},
                     'canonical_host': {'value': 'https://www.x.test', 'host': 'www.x.test',
                                        'host_redirect': {'status': 307, 'permanent': False}}},
                          'coverage': {'measured': 2, 'ledger': {}}, 'snapshot_sha256': 's', 'hosting': {'value': 'vercel',
                          'config_files': [{'host': 'vercel', 'file': 'vercel.json'}]}},
                 'pages': {}}
        for pid, url in (('p1', '/'), ('p2', '/about')):
            model['pages'][pid] = {'id': pid, 'url': url, 'template': 'src/App.jsx', 'instance_status': 'static', 'lens': 'raw-html',
                                   'head_scope': 'shared-shell', 'rendering': {'js_dependent': True},
                                   'head': {'title': {'value': 'Same', 'method': 'raw-html', 'edit': {'kind': 'literal', 'file': 'index.html', 'line': 14}},
                                            'description': {'value': 'd'}, 'og': {}, 'json_ld': []},
                                   'headings': [], 'content': {'blocks': []}, 'links': {'out': []}, 'images': []}
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(io.StringIO()):
            write_exploration(model, temp)
            issues = json.loads((Path(temp) / 'summary.json').read_text())['issues']
        ids = [i['id'] for i in issues]
        self.assertEqual(ids[:1], ['iss_shared_head'])
        self.assertIn('iss_soft_404', ids)
        self.assertIn('iss_robots_txt_spa_shell_fallback', ids)
        self.assertIn('iss_host_redirect_temporary', ids)
        self.assertIn('iss_missing_h1', ids)
        soft = next(i for i in issues if i['id'] == 'iss_soft_404')
        self.assertEqual(soft['fix'], {'kind': 'platform', 'where': 'vercel.json', 'recipe': soft['fix']['recipe']})


if __name__ == '__main__':
    unittest.main()
