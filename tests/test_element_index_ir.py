"""Single IR: value spans, contiguous text, content-anchored IDs, data flow, loops, i18n, shared flags."""

import unittest

from seo_agents.repo_analysis.tools.parsing.element_index import build_element_index


def index(files):
    return build_element_index({'files': files, 'assets': {}})


def by_tag(idx, file, tag):
    return [e for e in idx['elements'] if e['file'] == file and e['tag'] == tag]


def at(files, file, value_span):
    """Source characters covered by a span (single-line spans only)."""
    line = files[file].splitlines()[value_span['line'] - 1]
    return line[value_span['col']:value_span['end_col']]


PAGE_RAIL = '''const pages = [
  { label: "Home", href: "/" },
  { label: "About", href: "/about" },
  { label: "Bookshelf", href: "/bookshelf" },
];

export default function PageRail({ path }) {
  return (
    <nav aria-label="Pages">
      {pages.map((page) => (
        <a href={page.href} key={page.href}><span>{page.label}</span></a>
      ))}
    </nav>
  );
}
'''


class ValueSpanTests(unittest.TestCase):
    def test_attribute_spans_disambiguate_identical_values(self):
        files = {'index.html': '<html><body>\n<img src="/a.png" alt="Team photo" title="Team photo">\n</body></html>',
                 'src/Card.jsx': 'export default function Card() {\n  return <img alt="Logo" title={"Logo"} src="/l.png" />;\n}\n'}
        idx = index(files)
        img = by_tag(idx, 'index.html', 'img')[0]
        alt, title = img['attrs']['alt'], img['attrs']['title']
        self.assertEqual(at(files, 'index.html', alt), 'Team photo')
        self.assertEqual(at(files, 'index.html', title), 'Team photo')
        self.assertLess(alt['col'], title['col'])
        jsx = by_tag(idx, 'src/Card.jsx', 'img')[0]
        self.assertEqual(at(files, 'src/Card.jsx', jsx['attrs']['alt']), 'Logo')
        self.assertEqual(at(files, 'src/Card.jsx', jsx['attrs']['title']), 'Logo')
        self.assertNotEqual(jsx['attrs']['alt']['col'], jsx['attrs']['title']['col'])

    def test_text_split_by_children_is_not_a_contiguous_literal(self):
        files = {'a.html': '<p>Some <b>bold</b> text</p>\n<p>Plain words &amp; more</p>',
                 'B.jsx': 'export const B = () => <p>Some <b>bold</b> text</p>;\n'}
        idx = index(files)
        for file in files:
            split = [e for e in by_tag(idx, file, 'p') if 'bold' in e.get('text_literal', '')][0]
            self.assertEqual(split['text_literal'], 'Some bold text')
            self.assertFalse(split['contiguous'])
            self.assertEqual([s['text'] for s in split['segments']], ['Some', 'bold', 'text'])
            self.assertNotIn('Some text', [p.get('literal') for p in split['text']])  # no fake merged run
        plain = [e for e in by_tag(idx, 'a.html', 'p') if 'Plain' in e.get('text_literal', '')][0]
        self.assertTrue(plain['contiguous'])
        self.assertEqual(at(files, 'a.html', plain['text_span']), 'Plain words &amp; more')

    def test_markdown_spans(self):
        files = {'post.md': '---\ntitle: Hi\n---\n# Spring race\n\nSee [the map](/map.png) now.\n'}
        idx = index(files)
        h1 = by_tag(idx, 'post.md', 'h1')[0]
        self.assertEqual(at(files, 'post.md', h1['text_span']), 'Spring race')
        link = by_tag(idx, 'post.md', 'a')[0]
        self.assertEqual(at(files, 'post.md', link['attrs']['href']), '/map.png')


class StableIdTests(unittest.TestCase):
    def test_ids_survive_lines_inserted_above(self):
        before = {'index.html': '<html><body><main><h1>Hello</h1><p>World</p></main></body></html>',
                  'App.jsx': 'export default function App() {\n  return <main><h1>Hi</h1><p>There</p></main>;\n}\n'}
        after = {'index.html': '<!-- banner -->\n\n<html><body><main><h1>Hello</h1><p>World</p></main></body></html>',
                 'App.jsx': '// comment\nimport x from "./x";\n\nexport default function App() {\n  return <main><h1>Hi</h1><p>There</p></main>;\n}\n'}
        ids = lambda idx: {(e['file'], e['tag'], e.get('text_literal')): e['id'] for e in idx['elements']}
        first, second = ids(index(before)), ids(index(after))
        self.assertEqual(first, second)
        self.assertEqual(len(set(first.values())), len(first))

    def test_parent_links_use_new_ids(self):
        idx = index({'a.html': '<main><section><h2>A</h2></section><section><h2>B</h2></section></main>'})
        known = {e['id'] for e in idx['elements']}
        self.assertTrue(all(e['parent'] in known for e in idx['elements'] if e['parent']))


class DataFlowTests(unittest.TestCase):
    def test_const_array_loop_maps_to_array_literal_lines(self):
        files = {'src/components/PageRail.jsx': PAGE_RAIL}
        idx = index(files)
        link = by_tag(idx, 'src/components/PageRail.jsx', 'a')[0]
        self.assertTrue(link['in_loop'])
        self.assertEqual(link['template_n'], 3)
        self.assertEqual(link['data_source']['pointer_prefix'], '/pages')
        hrefs = link['attrs']['href']['instances']
        self.assertEqual([(h['value'], h['line'], h['pointer']) for h in hrefs],
                         [('/', 2, '/pages/0/href'), ('/about', 3, '/pages/1/href'), ('/bookshelf', 4, '/pages/2/href')])
        self.assertEqual(at(files, 'src/components/PageRail.jsx', hrefs[1]), '/about')
        label = by_tag(idx, 'src/components/PageRail.jsx', 'span')[0]['text'][0]['instances']
        self.assertEqual([l['value'] for l in label], ['Home', 'About', 'Bookshelf'])
        consts = {(d['file'], d['pointer']): d for d in idx['data'] if d['kind'] == 'const'}
        self.assertIn(('src/components/PageRail.jsx', '/pages/1/label'), consts)
        self.assertEqual(consts[('src/components/PageRail.jsx', '/pages/1/label')]['edit_kind'], 'literal')

    def test_imported_json_loop_gets_pointers(self):
        files = {'src/App.jsx': 'import projects from "./data/p.json";\nexport default () => <ul>{projects.map((p) => <li key={p.title}><h3>{p.title}</h3></li>)}</ul>;\n',
                 'src/data/p.json': '[\n  {"title": "FOZO app"},\n  {"title": "Testsigma"}\n]\n'}
        idx = index(files)
        h3 = by_tag(idx, 'src/App.jsx', 'h3')[0]
        self.assertEqual(h3['template_n'], 2)
        self.assertEqual(h3['data_source'], {'kind': 'json', 'file': 'src/data/p.json', 'pointer_prefix': '', 'name': 'projects'})
        instances = h3['text'][0]['instances']
        self.assertEqual([(i['pointer'], i['line'], i['kind']) for i in instances], [('/0/title', 2, 'json'), ('/1/title', 3, 'json')])
        self.assertEqual(at(files, 'src/data/p.json', instances[0]), 'FOZO app')

    def test_same_file_constants_resolve_with_spans(self):
        files = {'src/Hero.jsx': 'const SITE = { name: "Acme", tagline: `Fast tools` };\nconst TITLE = "Welcome";\n'
                                 'export default function Hero() {\n  return <header><h1>{TITLE}</h1><img alt={SITE.name} src="/a.png" /><p>{SITE.tagline}</p></header>;\n}\n'}
        idx = index(files)
        h1 = by_tag(idx, 'src/Hero.jsx', 'h1')[0]['text'][0]
        self.assertEqual((h1['literal'], h1['line'], h1['via']), ('Welcome', 2, 'const'))
        self.assertEqual(at(files, 'src/Hero.jsx', h1), 'Welcome')
        alt = by_tag(idx, 'src/Hero.jsx', 'img')[0]['attrs']['alt']
        self.assertEqual((alt['literal'], alt['line'], alt['const']['pointer']), ('Acme', 1, '/SITE/name'))
        self.assertEqual(by_tag(idx, 'src/Hero.jsx', 'p')[0]['text'][0]['literal'], 'Fast tools')

    def test_vue_and_astro_script_constants_and_loops(self):
        files = {'pages/index.vue': '<template>\n  <h1 :title="heading">{{ heading }}</h1>\n  <li v-for="item in items" :key="item.id">{{ item.name }}</li>\n</template>\n'
                                    '<script setup>\nconst heading = "Courses"\nconst items = [{ id: "a", name: "Intro" }, { id: "b", name: "Advanced" }]\n</script>\n',
                 'src/pages/index.astro': '---\nconst title = "Astro home";\n---\n<h1>{title}</h1>\n'}
        idx = index(files)
        h1 = by_tag(idx, 'pages/index.vue', 'h1')[0]
        self.assertEqual(h1['attrs']['title']['literal'], 'Courses')
        self.assertEqual(h1['text'][0]['literal'], 'Courses')
        li = by_tag(idx, 'pages/index.vue', 'li')[0]
        self.assertEqual(li['template_n'], 2)
        self.assertEqual([i['value'] for i in li['text'][0]['instances']], ['Intro', 'Advanced'])
        astro = by_tag(idx, 'src/pages/index.astro', 'h1')[0]['text'][0]
        self.assertEqual((astro['literal'], astro['line']), ('Astro home', 2))

    def test_template_language_loops_are_flagged(self):
        files = {'sections/list.liquid': '<ul>{% for p in collection.products %}<li>{{ p.title }}</li>{% endfor %}</ul><p>after</p>',
                 'layouts/_default/list.html': '<ul>{{ range .Pages }}<li>{{ .Title }}</li>{{ end }}</ul>{{ if .Draft }}<b>d</b>{{ end }}<p>x</p>',
                 'resources/views/a.blade.php': '<ul>@foreach($posts as $post)<li>{{ $post->title }}</li>@endforeach</ul><p>y</p>',
                 'src/routes/+page.svelte': '<ul>{#each items as item}<li>{item.name}</li>{/each}</ul><p>z</p>'}
        idx = index(files)
        for file in files:
            self.assertTrue(by_tag(idx, file, 'li')[0].get('in_loop'), file)
            self.assertFalse(by_tag(idx, file, 'p')[0].get('in_loop'), file)
        self.assertFalse(by_tag(idx, 'layouts/_default/list.html', 'b')[0].get('in_loop'))
        self.assertEqual(by_tag(idx, 'sections/list.liquid', 'li')[0]['text'][0]['loop_ref'], 'title')

    def test_conditional_branches_record_their_condition(self):
        files = {'src/App.jsx': 'export default function App() {\n  const path = window.location.pathname;\n'
                                '  return path === "/" ? <h2>Home</h2> : path === "/about" ? <h2>About</h2> : <p>404</p>;\n}\n'}
        idx = index(files)
        about = [e for e in by_tag(idx, 'src/App.jsx', 'h2') if e['text_literal'] == 'About'][0]
        self.assertEqual((about['conditional'], about['condition'], about['branch']), (True, 'path === "/about"', 'then'))


class I18nTests(unittest.TestCase):
    def test_namespaced_keys(self):
        files = {'package.json': '{"dependencies": {"next-intl": "^3.0.0"}}',
                 'messages/en.json': '{"Hero": {"title": "Build faster"}}',
                 'app/page.tsx': "import {useTranslations} from 'next-intl';\nexport default function Page() {\n"
                                 "  const t = useTranslations('Hero');\n  return <h1>{t('title')}</h1>;\n}\n"}
        idx = index(files)
        keys = by_tag(idx, 'app/page.tsx', 'h1')[0]['text'][0]['i18n_keys']
        self.assertEqual(keys[0], 'Hero.title')
        self.assertIn('Hero.title', idx['i18n'])

    def test_t_call_without_i18n_library_is_not_i18n(self):
        files = {'package.json': '{"dependencies": {"react": "18"}}',
                 'src/A.jsx': 'const t = (x) => x.toUpperCase();\nexport default () => <h1>{t("hello")}</h1>;\n'}
        h1 = by_tag(index(files), 'src/A.jsx', 'h1')[0]
        self.assertNotIn('i18n_keys', h1['text'][0])


class SharedTests(unittest.TestCase):
    def test_shared_components_and_spa_shell(self):
        files = {'index.html': '<html><head><title>X</title></head><body><div id="root"></div></body></html>',
                 'src/Nav.jsx': 'export default function Nav() { return <nav><a href="/">Home</a></nav>; }\n',
                 'src/Home.jsx': 'import Nav from "./Nav";\nexport default function Home() { return <main><Nav /><h1>Home</h1></main>; }\n',
                 'src/About.jsx': 'import Nav from "./Nav";\nexport default function About() { return <main><Nav /><h1>About</h1></main>; }\n'}
        idx = index(files)
        nav = by_tag(idx, 'src/Nav.jsx', 'a')[0]
        self.assertTrue(nav['shared'])
        self.assertEqual(nav['used_by'], ['src/About.jsx', 'src/Home.jsx'])
        self.assertFalse(by_tag(idx, 'src/Home.jsx', 'h1')[0].get('shared'))
        self.assertTrue(by_tag(idx, 'index.html', 'title')[0]['shared'])
        self.assertIn('index.html', idx['shells'])


if __name__ == '__main__':
    unittest.main()
