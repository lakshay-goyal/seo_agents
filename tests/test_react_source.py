import tempfile
import unittest
from pathlib import Path

from seo_agents.exploration.checks.tools.inventory import inventory


class ReactSourceTests(unittest.TestCase):
    def fixture(self, app, extra=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        repo = Path(temporary.name).resolve()
        (repo / 'src').mkdir()
        (repo / 'index.html').write_text('<div id="root"></div><script type="module" src="/src/main.jsx"></script>')
        (repo / 'src/main.jsx').write_text("import App from './App.jsx'; createRoot(root).render(<App />)")
        (repo / 'src/App.jsx').write_text(app)
        for name, content in (extra or {}).items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        return repo

    def test_unreachable_local_functions_do_not_leak(self):
        repo = self.fixture('function Unused() {return <p>Secret unused content</p>} export default function App(){return <main><h1>Actual content</h1></main>}')
        report = inventory(repo, 'content')
        copy = ' '.join(b['text'] for b in report['pages'][0]['blocks'])
        self.assertEqual(copy, 'Actual content')

    def test_literal_react_router_pages_remain_separate(self):
        repo = self.fixture("import {BrowserRouter, Routes, Route} from 'react-router-dom'; function Home(){return <h1>Home</h1>} function About(){return <h1>About</h1>} export default function App(){return <BrowserRouter><Routes><Route path='/' element={<Home/>}/><Route path='/about' element={<About/>}/></Routes></BrowserRouter>}")
        report = inventory(repo, 'content')
        self.assertEqual({p['url']: ' '.join(b['text'] for b in p['blocks']) for p in report['pages']}, {'/': 'Home', '/about': 'About'})

    def test_remote_fetch_is_not_executed(self):
        repo = self.fixture("import {useEffect, useState} from 'react'; export default function App(){const [data,setData]=useState(null); useEffect(()=>{fetch('https://example.invalid/private').then(r=>r.text()).then(setData)},[]); return <p>{data}</p>}")
        report = inventory(repo, 'content')
        self.assertTrue(any(g['reason'] == 'remote_or_missing_fetch_not_executed' for g in report['pages'][0]['coverage']['limitations']))

    def test_unknown_expressions_have_specific_coverage(self):
        repo = self.fixture('export default function App(){return <p>{unknownRuntime}</p>}')
        report = inventory(repo, 'content')
        self.assertEqual(report['pages'][0]['blocks'], [])
        self.assertTrue(any(g['reason'] == 'unresolved_rendered_expression' for g in report['pages'][0]['coverage']['limitations']))

    def test_unresolved_mount_cannot_publish_an_empty_shell(self):
        repo = self.fixture('export default unknownFactory()')
        with self.assertRaisesRegex(ValueError, 'refusing an HTML-shell-only report'):
            inventory(repo, 'content')

    def test_local_fetch_cannot_escape_public(self):
        repo = self.fixture("import {useEffect,useState} from 'react'; export default function App(){const [data,setData]=useState(null); useEffect(()=>{fetch('/../secret.txt').then(r=>r.text()).then(setData)},[]);return <p>{data}</p>}", {'secret.txt': 'Never read this'})
        report = inventory(repo, 'content')
        self.assertEqual(report['pages'][0]['blocks'], [])


if __name__ == '__main__':
    unittest.main()
