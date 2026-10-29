import json
import struct
import tempfile
import unittest
from pathlib import Path

from seo_agents.exploration.checks.tools.inventory import inventory, routes
from seo_agents.exploration.checks.tools import crawl
from seo_agents.exploration.checks.json_analysis import analyze_directory, analyze_reports
from seo_agents.exploration.checks.tools.helpers import REPORT_FILES
from seo_agents.exploration.checks.graph import run_onpage_graph


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.write('app/layout.tsx', '''import Header from '../components/Header';
export default function Layout({children}) { return <html><body><Header />{children}</body></html> }''')
        self.write('components/Header.tsx', '<header><nav><a href="/about">About us</a></nav></header>')
        self.write('app/page.tsx', '''import Image from 'next/image';
import Hero from '../components/Hero';
export const metadata = { title: "Home title", description: "A full description for home." };
export default function Home() { return <main><h1>Welcome</h1><Hero /><h2>Details</h2>
<p>Written by Jane, a licensed expert.</p><a href="about#team">Our team</a>
<Image src="/hero.png" alt="A mountain" width={640} height={320} priority={false} />
<img src="/hero.png" alt="" loading="eager" /><Image src={dynamicSrc} alt={dynamicAlt} loading={dynamicLoading} />
<video src="/movie.mp4" preload="metadata" /><p>{runtimeContent}</p></main> }''')
        self.write('components/Hero.tsx', '<section><p>' + 'Long visible content. ' * 300 + '</p><button>Start now</button></section>')
        self.write('app/about/page.tsx', '<main><h1>About us</h1><p>Contact us for editorial policy details.</p></main>')
        self.write('app/blog/[slug]/page.tsx', '<main><h1>{post.title}</h1></main>')
        self.write('public/hero.png', b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR' + struct.pack('>II', 640, 320) + b'\x00' * 210000)

    def write(self, name, value):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else value.encode())

    def home(self, category):
        return next(p for p in inventory(self.repo, category)['pages'] if p['url'] == '/')

    def test_complete_literal_content_and_shared_components(self):
        page = self.home('content')
        text = ' '.join(b['text'] for b in page['blocks'])
        self.assertIn('Start now', text)
        self.assertGreater(len(text), 6000)
        self.assertEqual(text.count('Long visible content.'), 300)
        self.assertNotIn('export default', text)
        self.assertIn('About us', text)
        self.assertFalse(page['coverage']['complete'])
        self.assertTrue(any(g.get('expression') == 'runtimeContent' for g in page['coverage']['limitations']))

    def test_routes_root_groups_dynamic_and_api(self):
        self.write('src/app/(shop)/store/page.tsx', '<h1>Store</h1>')
        self.write('pages/api/test.ts', 'export default function api() {}')
        self.write('pages/_document.tsx', '<html />')
        urls = [r['url'] for r in routes(self.repo)]
        self.assertIn('/', urls)
        self.assertIn('/store', urls)
        self.assertIn('/blog/[slug]', urls)
        self.assertNotIn('/api/test', urls)
        self.assertNotIn('/_document', urls)

    def test_metadata_retains_description(self):
        declarations = self.home('metadata')['declarations']
        self.assertTrue(any(d.get('description') == 'A full description for home.' for d in declarations))

    def test_link_page_context_anchor_and_relative_resolution(self):
        links = self.home('internal_links')['links']
        team = next(l for l in links if l['anchor_text'] == 'Our team')
        self.assertEqual(team['target_url'], '/about#team')
        self.assertEqual(team['file'], 'app/page.tsx')
        self.assertTrue(any(l['href'] == '/about' for l in links))

    def test_media_measurements_loading_and_unknown(self):
        items = self.home('media')['items']
        hero = items[0]
        self.assertEqual(hero['size_bytes'], (self.repo / 'public/hero.png').stat().st_size)
        self.assertEqual((hero['intrinsic_width'], hero['intrinsic_height']), (640, 320))
        self.assertEqual(hero['declared_width'], '640')
        self.assertEqual(hero['effective_loading'], 'lazy')
        self.assertEqual(items[1]['effective_loading'], 'eager')
        self.assertIsNone(items[2]['src'])
        self.assertIsNone(items[2]['effective_loading'])
        self.assertIn('alt', items[2]['unresolved_attributes'])
        self.assertEqual(items[3]['preload'], 'metadata')
        analysis = analyze_reports({'media': inventory(self.repo, 'media')})
        self.assertFalse(any(f['code'] == 'alt_not_observed' for f in analysis['findings']))
        self.assertTrue(any(f['code'] == 'large_source_asset' for f in analysis['findings']))

    def test_eeat_requires_visible_evidence_not_variable_names(self):
        self.write('app/fake/page.tsx', 'const author = "contact us"; export default function Fake() { return <p>Hello</p> }')
        report = inventory(self.repo, 'eeat')
        fake = next(p for p in report['pages'] if p['url'] == '/fake')
        self.assertTrue(all(s['status'] != 'candidate_evidence' for s in fake['signals']))
        home = next(p for p in report['pages'] if p['url'] == '/')
        self.assertTrue(any(s['signal'] == 'authorship' and s['evidence'] for s in home['signals']))

    def test_graph_tools_and_offline_analysis(self):
        state = run_onpage_graph(str(self.repo), mode='static')
        reports = state['artifacts']
        self.assertEqual(set(reports), set(REPORT_FILES))
        folder = self.repo / 'saved'
        folder.mkdir()
        for category, filename in REPORT_FILES.items():
            report = reports[category]
            self.assertEqual(report['category'], category)
            self.assertNotIn('repo', report)
            self.assertNotIn('ai', report)
            (folder / filename).write_text(json.dumps(report))
        expected = analyze_reports(reports)
        self.assertEqual(analyze_directory(folder), expected)
        self.assertEqual(crawl.scrape_content_blocks.invoke({'repo_path': str(self.repo)})['schema_version'], '2.0')

    def test_heading_return_to_higher_level_is_valid(self):
        self.write('app/order/page.tsx', '<main><h1>A</h1><h2>B</h2><h3>C</h3><h2>D</h2></main>')
        report = analyze_reports({'page_structure': inventory(self.repo, 'page_structure')})
        self.assertFalse(any(f['url'] == '/order' and f['code'] == 'heading_level_skipped' for f in report['findings']))

    def test_imported_assets_literal_constants_and_literal_expression_text(self):
        self.write('app/assets/page.tsx', '''import photo from '../../public/hero.png';
const title = "Visible title";
export default function Page() { return <main><h1>{title}</h1><img src={photo} alt={'My photo'} /></main> }''')
        media = next(p for p in inventory(self.repo, 'media')['pages'] if p['url'] == '/assets')['items'][0]
        self.assertIsNotNone(media['size_bytes'])
        self.assertEqual(media['alt'], 'My photo')
        content = next(p for p in inventory(self.repo, 'content')['pages'] if p['url'] == '/assets')
        self.assertTrue(any(b['text'] == 'Visible title' for b in content['blocks']))

    def test_html_metadata_and_linked_image_accessible_name(self):
        self.write('pages/html.tsx', '<Head><title>Actual title</title><meta name="description" content="Actual description" /></Head><main><a href="/about"><img src="/hero.png" alt="About our team" /></a></main>')
        report = inventory(self.repo, 'metadata')
        page = next(p for p in report['pages'] if p['url'] == '/html')
        self.assertTrue(any(d.get('title') == 'Actual title' for d in page['declarations']))
        links = next(p for p in inventory(self.repo, 'internal_links')['pages'] if p['url'] == '/html')['links']
        self.assertEqual(links[0]['accessible_name'], 'About our team')

    def test_invalid_category_payload_rejected_before_pr(self):
        from seo_agents.sandbox.pr import check_reports
        reports = {key: inventory(self.repo, key) for key in REPORT_FILES}
        reports['content']['pages'][0]['items'] = []
        with self.assertRaisesRegex(ValueError, 'Unexpected page fields'):
            check_reports(reports)

    def test_single_page_react_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'src').mkdir()
            (root / 'src/App.jsx').write_text('<main><h1>React home</h1></main>')
            report = inventory(root, 'content')
            self.assertEqual(report['pages'][0]['url'], '/')
            self.assertEqual(report['pages'][0]['blocks'][0]['text'], 'React home')


if __name__ == '__main__':
    unittest.main()
