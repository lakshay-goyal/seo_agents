"""Source lens: exact edit spans, loop instances, prop call sites and real insertion targets per stack."""

import unittest

from seo_agents.repo_analysis.site_model.source_lens import source_lens
from seo_agents.repo_analysis.tools.parsing.element_index import build_element_index

try:
    from test_element_index_ir import PAGE_RAIL
except ImportError:  # run as tests.<module>
    from tests.test_element_index_ir import PAGE_RAIL


def lens(files, primary, pages):
    snapshot = {'files': files, 'assets': {}}
    index = build_element_index(snapshot)
    out, _ = source_lens(snapshot, index, {'primary': primary}, pages)
    return out


SPA_SHELL = ('<!doctype html>\n<html lang="en">\n  <head>\n    <meta charset="UTF-8" />\n    <title>Jane — Engineer</title>\n'
             '  </head>\n  <body>\n    <div id="root"></div>\n    <script type="module" src="/src/main.jsx"></script>\n  </body>\n</html>\n')
APP = '''import PageRail from "./components/PageRail";

function Section({ id, title, children }) {
  return <section id={id}><h2>{title}</h2>{children}</section>;
}

function HomePage() {
  return (
    <div className="home">
      <Section id="work" title="Selected Work"><p>Things I built.</p></Section>
    </div>
  );
}

function AboutPage() {
  return <div className="about"><Section id="me" title="About me"><p>Bio.</p></Section></div>;
}

export default function App() {
  const path = window.location.pathname;
  const page = path === "/" ? <HomePage /> : path === "/about" ? <AboutPage /> : null;
  return <main><PageRail path={path} />{page}</main>;
}
'''


class SpaTests(unittest.TestCase):
    def setUp(self):
        self.files = {'index.html': SPA_SHELL, 'src/main.jsx': 'import App from "./App";\n',
                      'src/App.jsx': APP, 'src/components/PageRail.jsx': PAGE_RAIL}
        self.pages = [{'id': 'p_home', 'url': '/', 'template': 'src/App.jsx'},
                      {'id': 'p_about', 'url': '/about', 'template': 'src/App.jsx'}]
        self.out = lens(self.files, 'react-spa', self.pages)

    def test_head_add_target_is_before_head_close_in_shared_shell(self):
        target = self.out['p_home']['head']['add_target']
        self.assertEqual({k: target[k] for k in ('file', 'line', 'position', 'anchor', 'scope')},
                         {'file': 'index.html', 'line': 6, 'position': 'before', 'anchor': '</head>', 'scope': 'shell'})
        self.assertIn('prerender', target['recipe'])

    def test_body_add_target_is_inside_the_routed_page_component(self):
        home = self.out['p_home']['content']['add_target']
        about = self.out['p_about']['content']['add_target']
        self.assertEqual((home['file'], home['line'], home['position'], home['scope']), ('src/App.jsx', 9, 'after', 'route'))
        self.assertEqual(self.files['src/App.jsx'].splitlines()[8][:home['col']].strip(), '<div className="home">')
        self.assertEqual(about['line'], 16)

    def test_nav_links_point_at_array_literal_rows(self):
        links = [l for l in self.out['p_home']['links']['out'] if l['edit'].get('file') == 'src/components/PageRail.jsx']
        self.assertEqual([(l['href'], l['anchor'], l['edit']['line'], l['edit']['pointer']) for l in links],
                         [('/', 'Home', 2, '/pages/0/href'), ('/about', 'About', 3, '/pages/1/href'), ('/bookshelf', 'Bookshelf', 4, '/pages/2/href')])
        self.assertEqual(links[1]['anchor_edit']['pointer'], '/pages/1/label')
        self.assertTrue(all(l['edit']['in_loop'] and l['edit']['template_n'] == 3 for l in links))
        self.assertTrue(all(l['inside_main_landmark'] for l in links))  # nav renders inside <main> (landmark nesting issue)

    def test_same_file_component_props_resolve_to_call_site_span(self):
        headings = {h['text']: h for h in self.out['p_home']['headings']}
        work = headings['Selected Work']['edit']
        self.assertEqual((work['kind'], work['file'], work['line'], work['attr']), ('prop', 'src/App.jsx', 10, 'title'))
        line = self.files['src/App.jsx'].splitlines()[work['line'] - 1]
        self.assertEqual(line[work['col']:work['end_col']], 'Selected Work')
        self.assertEqual(work['prop_flow']['component'], 'Section')

    def test_shared_files_are_flagged_on_edits(self):
        links = self.out['p_home']['links']['out']
        self.assertTrue(all(l['edit'].get('shared') for l in links if l['edit'].get('file') == 'src/components/PageRail.jsx'))


class AddTargetTests(unittest.TestCase):
    def test_next_app_router_after_imports_or_inside_metadata(self):
        files = {'app/layout.tsx': 'export default function L({children}) { return <html><body>{children}</body></html>; }\n',
                 'app/page.tsx': "import Hero from '../components/Hero';\nimport {x} from './x';\n\nexport default function Page() { return <main><Hero /></main>; }\n",
                 'app/about/page.tsx': "import type { Metadata } from 'next';\n\nexport const metadata: Metadata = { title: 'About' };\nexport default function P() { return <main>Hi</main>; }\n"}
        out = lens(files, 'next', [{'id': 'a', 'url': '/', 'template': 'app/page.tsx'}, {'id': 'b', 'url': '/about', 'template': 'app/about/page.tsx'}])
        home = out['a']['head']['add_target']
        self.assertEqual((home['file'], home['line'], home['position'], home['scope']), ('app/page.tsx', 2, 'after', 'route'))
        about = out['b']['head']['add_target']
        self.assertEqual((about['line'], about['position'], about['anchor']), (3, 'inside-start', 'export const metadata'))

    def test_markdown_front_matter_and_body(self):
        files = {'content/about.md': '---\ntitle: About\n---\nHello there.\n', 'layouts/_default/baseof.html': '<html><head></head><body></body></html>'}
        out = lens(files, 'hugo', [{'id': 'a', 'url': '/about/', 'template': 'content/about.md'}])
        head, body = out['a']['head']['add_target'], out['a']['content']['add_target']
        self.assertEqual((head['file'], head['line'], head['anchor'], head['kind']), ('content/about.md', 3, '---', 'markdown'))
        self.assertEqual((body['line'], body['position']), (4, 'before'))

    def test_nuxt_and_sveltekit(self):
        files = {'pages/index.vue': '<template><main><h1>Hi</h1></main></template>\n<script setup>\nconst a = 1\n</script>\n',
                 'src/routes/+page.svelte': '<script>\n  let x = 1;\n</script>\n<main><h1>Hi</h1></main>\n'}
        nuxt = lens(files, 'nuxt', [{'id': 'a', 'url': '/', 'template': 'pages/index.vue'}])['a']['head']['add_target']
        self.assertEqual((nuxt['line'], nuxt['position']), (2, 'after'))
        svelte = lens(files, 'sveltekit', [{'id': 'a', 'url': '/', 'template': 'src/routes/+page.svelte'}])['a']['head']['add_target']
        self.assertEqual((svelte['line'], svelte['position'], svelte['anchor']), (3, 'after', '</script>'))

    def test_server_template_with_head_block(self):
        files = {'templates/base.html': '<html><head>{% block head %}{% endblock %}</head><body>{% block content %}{% endblock %}</body></html>',
                 'templates/home.html': '{% extends "base.html" %}\n{% block head %}<title>Home</title>{% endblock %}\n{% block content %}<main><h1>Hi</h1></main>{% endblock %}'}
        out = lens(files, 'django', [{'id': 'a', 'url': '/', 'template': 'templates/home.html'}])
        target = out['a']['head']['add_target']
        self.assertEqual((target['file'], target['line'], target['position'], target['scope']), ('templates/home.html', 2, 'after', 'route'))

    def test_never_a_line_one_placeholder(self):
        files = {'pages/x.unknown': 'nothing'}
        target = lens(files, 'mystery', [{'id': 'a', 'url': '/', 'template': 'pages/x.unknown'}])['a']['head']['add_target']
        self.assertIsNone(target['line'])
        self.assertEqual(target['verify'], 'unresolved')


class ResolutionTests(unittest.TestCase):
    def test_ternary_text_keeps_both_branches(self):
        files = {'src/A.jsx': 'export default function A({ open }) {\n  return <main><button>{open ? "Close menu" : "Open menu"}</button></main>;\n}\n'}
        out = lens(files, 'react-spa', [{'id': 'a', 'url': '/', 'template': 'src/A.jsx'}])
        block = [b for b in out['a']['content']['blocks'] if b['tag'] == 'button']
        self.assertEqual(len(block), 0)  # unresolved (two candidates), not two fake blocks
        from seo_agents.repo_analysis.site_model.source_lens import Context, PageResolver
        snapshot = {'files': files, 'assets': {}}
        index = build_element_index(snapshot)
        ctx = Context(snapshot, index, {'primary': 'react-spa'})
        button = next(e for e in index['elements'] if e['tag'] == 'button')
        result = PageResolver(ctx, {'template': 'src/A.jsx'}, ['src/A.jsx'], {'src/A.jsx'}, {}).text_field(button)
        self.assertTrue(result['conditional'])
        self.assertEqual([c['value'] for c in result['candidates']], ['Close menu', 'Open menu'])

    def test_split_text_edit_reports_segments(self):
        files = {'index.html': '<html><head><title>T</title></head><body><main><p>Some <b>bold</b> text</p></main></body></html>'}
        out = lens(files, 'static-html', [{'id': 'a', 'url': '/', 'template': 'index.html'}])
        block = next(b for b in out['a']['content']['blocks'] if b['tag'] == 'p')
        self.assertFalse(block['edit']['contiguous'])
        self.assertEqual([s['text'] for s in block['edit']['segments']], ['Some', 'bold', 'text'])


if __name__ == '__main__':
    unittest.main()
