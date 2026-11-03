"""Pathname-switch SPA (PR #8 shape): each route keeps only its own component's elements."""

import os
import unittest
from pathlib import Path
from unittest import mock

from seo_agents.exploration.repo_analysis.site_model import explore_site

FIXTURE = Path(__file__).parent / 'fixtures' / 'sites' / 'pathname-spa'
ENV = {'SEO_GAP_WORKERS': '0', 'SEO_GAP_CACHE': '0', 'OPENROUTER_API_KEY': ''}


class PathnameScopeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch.dict(os.environ, ENV):
            model = explore_site(FIXTURE, run_gap_workers=False)
        cls.pages = {page['url']: page for page in model['pages'].values()}

    def headings(self, url):
        return [h['text'] for h in self.pages[url]['headings']]

    def test_routes_do_not_inherit_other_branches(self):
        self.assertEqual(self.headings('/'), ['Selected work'])
        self.assertEqual(self.headings('/about'), ['About'])
        self.assertNotIn('Selected work', self.headings('/blog/deploying-with-vercel'))

    def test_not_found_fallback_is_not_page_content(self):
        for url in self.pages:
            self.assertNotIn('Page not found', self.headings(url))

    def test_nav_links_attribute_to_nav_array(self):
        files = {(link.get('href'), (link.get('href_edit') or link.get('edit') or {}).get('file'))
                 for link in self.pages['/about']['links']['out']}
        self.assertIn(('/about', 'src/components/PageRail.jsx'), files)

    def test_head_insertion_point_is_before_head_close_not_line_1(self):
        target = self.pages['/']['head']['add_target']
        self.assertEqual((target['file'], target['line'], target['anchor']), ('index.html', 8, '</head>'))
        self.assertEqual(target['verify'], 'grounded')
        self.assertEqual(target['scope'], 'shell')


if __name__ == '__main__':
    unittest.main()
