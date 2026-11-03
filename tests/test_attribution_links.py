"""Attribution regressions from the PR #8 audit: link labels vs headings, array entries, repeated blocks,
no `line: 1` placeholders, data-src hints."""

import unittest

from seo_agents.repo_analysis.site_model.value_attribution import Attributor, parse_src_hint
from seo_agents.repo_analysis.site_model.site_model_builder import empty_page
from seo_agents.repo_analysis.site_model.source_lens import Context
from seo_agents.repo_analysis.tools.parsing.element_index import build_element_index

FILES = {
    'index.html': '<!doctype html>\n<html lang="en">\n<head><title>Lakshay Goyal — Software Engineer</title></head>\n<body><div id="root"></div></body>\n</html>',
    'src/components/PageRail.jsx': ('const pages = [\n  { label: "Home", href: "/" },\n  { label: "About", href: "/about" },\n'
                                    '  { label: "Blogs", href: "/blog" },\n];\n\nexport default function PageRail({ path }) {\n'
                                    '  return <nav>{pages.map((page) => <a href={page.href} key={page.href}><span>{page.label}</span></a>)}</nav>;\n}\n'),
    'src/components/ContactRail.jsx': ('const contacts = [\n  { label: "GitHub", href: "https://github.com/lakshay-goyal" },\n'
                                       '  { label: "Email", href: "mailto:me@example.com" },\n];\n'
                                       'export default function ContactRail() {\n  return <aside>{contacts.map((c) => <a href={c.href} aria-label={c.label}>{c.label}</a>)}</aside>;\n}\n'),
    'src/App.jsx': ('import PageRail from "./components/PageRail";\nimport ContactRail from "./components/ContactRail";\n'
                    'function Section({ id, title, children }) { return <section id={id}><h2>{title}</h2>{children}</section>; }\n'
                    'export default function App() {\n  return <main>\n    <PageRail path="/" />\n'
                    '    <Section id="about" title="About"><p>Read more</p></Section>\n'
                    '    <Section id="blog" title="Blogs"><p>Read more</p></Section>\n    <ContactRail />\n  </main>;\n}\n'),
}


def snapshot():
    return {'files': FILES, 'assets': {}, 'inventory': [{'path': p, 'kind': 'file'} for p in FILES], 'limitations': [],
            'sha256': 'test', 'coverage': {}}


class AttributionLinkTests(unittest.TestCase):
    def setUp(self):
        snap = snapshot()
        self.ctx = Context(snap, build_element_index(snap), {'primary': 'react-spa', 'family': 'react'})
        self.attributor = Attributor(self.ctx)
        self.page = {'id': 'p_home', 'url': '/', 'template': 'src/App.jsx'}
        self.source = empty_page()
        self.source['composition'] = ['src/App.jsx', 'src/components/PageRail.jsx', 'src/components/ContactRail.jsx', 'index.html']

    def attribute(self, summary):
        return self.attributor.attribute(self.page, self.source, summary, 'rendered')

    def test_nav_links_go_to_the_array_entry_not_the_section_heading(self):
        values = self.attribute({'links': [{'href': '/about', 'text': 'About', 'section': 'nav'},
                                           {'href': '/blog', 'text': 'Blogs', 'section': 'nav'}],
                                 'headings': [{'level': 2, 'text': 'About'}]})
        about, blogs = values['links']['out']
        self.assertEqual((about['edit']['file'], about['edit']['line']), ('src/components/PageRail.jsx', 3))
        self.assertEqual(about['anchor_edit']['line'], 3)
        self.assertTrue(about['edit']['in_loop'])
        self.assertEqual(about['edit']['verify'], 'grounded')
        self.assertEqual((blogs['edit']['file'], blogs['edit']['line']), ('src/components/PageRail.jsx', 4))

    def test_contact_links_go_to_contact_rail(self):
        values = self.attribute({'links': [{'href': 'https://github.com/lakshay-goyal', 'text': 'GitHub', 'section': 'aside'}]})
        edit = values['links']['out'][0]['edit']
        self.assertEqual((edit['file'], edit['line']), ('src/components/ContactRail.jsx', 2))

    def test_no_line_one_placeholders(self):
        values = self.attribute({'title': 'Something generated at runtime', 'description': 'Also generated',
                                 'images': [{'src': '/assets/unknown.png', 'alt': None}]})
        for item in (values['head']['title'], values['head']['description'], values['images'][0]):
            edit = item['edit']
            self.assertTrue(edit is None or edit.get('line') not in (1, None) or edit['kind'] == 'cms', edit)

    def test_repeated_blocks_keep_their_own_locations_in_dom_order(self):
        first = {'text': 'Read more', 'edit': {'kind': 'literal', 'file': 'src/App.jsx', 'line': 7}}
        second = {'text': 'Read more', 'edit': {'kind': 'literal', 'file': 'src/App.jsx', 'line': 8}}
        self.source['content']['blocks'] = [first, second]
        values = self.attribute({'blocks': [{'tag': 'p', 'text': 'Read more'}, {'tag': 'p', 'text': 'Read more'}]})
        self.assertEqual([b['edit']['line'] for b in values['content']['blocks']], [7, 8])

    def test_src_hint_wins_when_value_is_literal_there(self):
        self.assertEqual(parse_src_hint('./src/App.jsx:3:10'), ('src/App.jsx', 3, 10))
        self.assertIsNone(parse_src_hint('nonsense'))
        values = self.attribute({'title': 'Lakshay Goyal — Software Engineer', 'src_hints': {'title': 'index.html:3:6'}})
        edit = values['head']['title']['edit']
        self.assertEqual((edit['file'], edit['line'], edit['verify'], edit['via']), ('index.html', 3, 'grounded', 'data-src'))

    def test_invisible_hrefless_links_are_not_attributed(self):
        values = self.attribute({'links': [{'href': '', 'text': 'Unlovable', 'visible': False}]})
        link = values['links']['out'][0]
        self.assertIsNone(link['edit'])
        self.assertFalse(link['attributable'])


if __name__ == '__main__':
    unittest.main()
