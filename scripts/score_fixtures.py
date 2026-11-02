"""Score the site model against the golden fixtures in tests/fixtures/sites/.

Each fixture is a tiny repository for one framework plus `expected.json`
(statically determinable truth). The scorer runs `explore_site(render=False)` on
a copy of the fixture (without expected.json) and measures:

- framework fingerprint match;
- URL recall / precision (trailing slash, `.html`, `/index` and placeholder syntax
  `[x]`, `[...x]`, `:x`, `{x}`, `<type:x>` are normalised);
- head fields (title, description, canonical) exact match after text normalisation;
- headings (level + text) and links (href + anchor) precision / recall;
- images (src + alt) accuracy;
- edit attribution: file matches and line within +/-1.

Only keys present in expected.json are scored. Actual items with a null value
(runtime/CMS-only) are ignored for precision.

    PYTHONPATH=src python scripts/score_fixtures.py [--fixture NAME ...] [--json out.json]
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests' / 'fixtures' / 'sites'
HEAD_FIELDS = ('title', 'description', 'canonical')
TARGETS = {'url_recall': 0.98, 'head_accuracy': 0.97, 'attribution_accuracy': 0.90}


# ------------------------------------------------------------------ normalisation
def norm_text(value):
    if value is None:
        return None
    return ' '.join(html.unescape(str(value)).split()).casefold()


PLACEHOLDER = re.compile(r'\[\[?\.{0,3}[^\]/]+\]?\]|:[A-Za-z_][\w]*\??|\{[^}/]+\}|<(?:[^:>/]+:)?[^>/]+>|\*+')


def norm_url(value):
    """Comparable route/URL path: placeholders unified, no trailing slash/.html/index."""
    if value is None:
        return None
    value = str(value).strip()
    value = re.sub(r'^[a-z][a-z0-9+.-]*://[^/]+', '', value, flags=re.I)
    value = value.split('#', 1)[0].split('?', 1)[0]
    if not value.startswith('/'):
        value = '/' + value
    value = PLACEHOLDER.sub(':param', value)
    value = re.sub(r'/index(?:\.html?)?$', '/', value)
    value = re.sub(r'\.html?$', '', value)
    value = re.sub(r'/+', '/', value)
    if len(value) > 1:
        value = value.rstrip('/')
    return value or '/'


def norm_href(value):
    if value is None:
        return None
    value = str(value).strip()
    if re.match(r'^(?:mailto|tel|javascript):', value, re.I) or value.startswith('#'):
        return value
    if re.match(r'^[a-z][a-z0-9+.-]*://', value, re.I):
        return value.rstrip('/')
    return norm_url(value)


def norm_file(value):
    return str(value or '').lstrip('./').replace('\\', '/')


# ------------------------------------------------------------------ model access
def model_pages(model):
    pages = (model or {}).get('pages') or {}
    return list(pages.values()) if isinstance(pages, dict) else list(pages)


def model_framework(model):
    for container in ((model or {}).get('site') or {}, ((model or {}).get('index') or {}).get('repo') or {}, (model or {}).get('index') or {}):
        framework = container.get('framework')
        if isinstance(framework, str):
            return framework
        if isinstance(framework, dict):
            return framework.get('primary') or framework.get('name')
    return None


def field(page, name):
    value = ((page.get('head') or {}).get(name) or {})
    return value if isinstance(value, dict) else {'value': value}


def headings(page):
    return [h for h in page.get('headings') or [] if isinstance(h, dict)]


def link_rows(page):
    links = page.get('links') or {}
    rows = links.get('out') if isinstance(links, dict) else links
    return [row for row in rows or [] if isinstance(row, dict)]


def page_urls(page):
    return {norm_url(page.get(key)) for key in ('url', 'route') if page.get(key)}


# ------------------------------------------------------------------ scoring
class Tally:
    def __init__(self):
        self.hits = self.expected = self.actual = 0

    def add(self, hits, expected, actual=None):
        self.hits += hits
        self.expected += expected
        self.actual += hits if actual is None else actual

    @property
    def recall(self):
        return self.hits / self.expected if self.expected else None

    @property
    def precision(self):
        return self.hits / self.actual if self.actual else None

    def as_dict(self):
        return {'hits': self.hits, 'expected': self.expected, 'actual': self.actual,
                'recall': rounded(self.recall), 'precision': rounded(self.precision)}


def rounded(value):
    return None if value is None else round(value, 4)


def set_score(tally, expected, actual):
    expected, actual = set(expected), {item for item in actual if None not in item}
    tally.add(len(expected & actual), len(expected), len(actual))
    return sorted(map(list, expected - actual)), sorted(map(list, actual - expected))


def score_fixture(name, explore, *, keep_misses=True):
    fixture = FIXTURES / name
    expected = json.loads((fixture / 'expected.json').read_text())
    tallies = {key: Tally() for key in ('urls', 'head', 'headings', 'links', 'images', 'attribution')}
    result = {'fixture': name, 'expected_framework': expected['framework'], 'error': None, 'misses': []}
    model = None
    with tempfile.TemporaryDirectory() as temp:
        repo = Path(temp) / name
        shutil.copytree(fixture, repo, ignore=shutil.ignore_patterns('expected.json'))
        try:
            model = explore(repo, render=False)
        except Exception as error:  # a failing fixture scores zero; the run continues
            result['error'] = type(error).__name__ + ': ' + str(error)[:500]
            result['traceback'] = traceback.format_exc()[-2000:]
    actual_framework = model_framework(model)
    result['framework'] = actual_framework
    result['framework_match'] = actual_framework == expected['framework']
    pages = model_pages(model)
    by_url = {}
    for page in pages:
        for url in page_urls(page):
            by_url.setdefault(url, page)
    expected_urls = set()
    for row in expected['pages']:
        candidates = [norm_url(row['url'])] + [norm_url(u) for u in row.get('alt_urls', [])]
        expected_urls.add(candidates[0])
        page = next((by_url[c] for c in candidates if c in by_url), None)
        tallies['urls'].add(int(page is not None), 1, 0)
        if page is None:
            result['misses'].append({'url': row['url'], 'kind': 'missing-page'})
        fields = row.get('fields', {})
        for key in HEAD_FIELDS:
            if key not in fields:
                continue
            got = norm_text(field(page, key).get('value')) if page else None
            want = norm_text(fields[key])
            if key == 'canonical':
                got, want = norm_href(got), norm_href(want)
            ok = got == want
            tallies['head'].add(int(ok), 1, 1)
            if not ok and keep_misses:
                result['misses'].append({'url': row['url'], 'field': key, 'expected': fields[key], 'actual': got})
        if 'h1' in fields or 'headings' in fields:
            want = {(1, norm_text(t)) for t in fields.get('h1', [])}
            want |= {(int(level), norm_text(t)) for level, t in fields.get('headings', [])}
            got = {(h.get('level'), norm_text(h.get('text'))) for h in headings(page or {})}
            missing, extra = set_score(tallies['headings'], want, got)
            if (missing or extra) and keep_misses:
                result['misses'].append({'url': row['url'], 'field': 'headings', 'missing': missing, 'extra': extra})
        if 'links' in fields:
            want = {(norm_href(h), norm_text(a)) for h, a in fields['links']}
            got = {(norm_href(l.get('href')), norm_text(l.get('anchor') if l.get('anchor') is not None else l.get('anchor_text')))
                   for l in link_rows(page or {})}
            missing, extra = set_score(tallies['links'], want, got)
            if (missing or extra) and keep_misses:
                result['misses'].append({'url': row['url'], 'field': 'links', 'missing': missing, 'extra': extra})
        if 'images' in fields:
            want = {(norm_href(s), norm_text(a)) for s, a in fields['images']}
            got = {(norm_href(i.get('src')), norm_text(i.get('alt'))) for i in (page or {}).get('images') or [] if isinstance(i, dict)}
            missing, extra = set_score(tallies['images'], want, got)
            if (missing or extra) and keep_misses:
                result['misses'].append({'url': row['url'], 'field': 'images', 'missing': missing, 'extra': extra})
        for key, target in row.get('attribution', {}).items():
            if key == 'h1':
                edit = next((h.get('edit') for h in headings(page or {}) if h.get('level') == 1 and h.get('edit')), None)
            else:
                edit = field(page, key).get('edit') if page else None
            ok = bool(edit) and norm_file(edit.get('file')) == norm_file(target['file']) \
                and isinstance(edit.get('line'), int) and abs(edit['line'] - target['line']) <= 1
            tallies['attribution'].add(int(ok), 1, 1)
            if not ok and keep_misses:
                result['misses'].append({'url': row['url'], 'attribution': key, 'expected': target,
                                         'actual': {k: (edit or {}).get(k) for k in ('kind', 'file', 'line')} if edit else None})
    actual_urls = {url for page in pages for url in [norm_url(page.get('url'))] if url}
    alt = {norm_url(u) for row in expected['pages'] for u in row.get('alt_urls', [])}
    matched_actual = len({u for u in actual_urls if u in expected_urls or u in alt})
    tallies['urls'].actual = len(actual_urls)
    tallies['urls'].hits_precision = matched_actual
    result['url_extra'] = sorted(actual_urls - expected_urls - alt)
    result['scores'] = {key: tally.as_dict() for key, tally in tallies.items()}
    result['scores']['urls']['precision'] = rounded(matched_actual / len(actual_urls)) if actual_urls else None
    result['_tallies'] = tallies
    return result


def summarize(results):
    keys = ('urls', 'head', 'headings', 'links', 'images', 'attribution')
    totals = {key: Tally() for key in keys}
    url_actual = url_matched = 0
    for result in results:
        for key in keys:
            tally = result['_tallies'][key]
            totals[key].add(tally.hits, tally.expected, tally.actual if key != 'urls' else 0)
        url_actual += result['_tallies']['urls'].actual
        url_matched += getattr(result['_tallies']['urls'], 'hits_precision', 0)
    summary = {key: totals[key].as_dict() for key in keys}
    summary['urls']['actual'] = url_actual
    summary['urls']['precision'] = rounded(url_matched / url_actual) if url_actual else None
    summary['framework_accuracy'] = rounded(sum(r['framework_match'] for r in results) / len(results)) if results else None
    summary['errors'] = sum(bool(r['error']) for r in results)
    measured = {'url_recall': summary['urls']['recall'], 'head_accuracy': summary['head']['recall'],
                'attribution_accuracy': summary['attribution']['recall']}
    summary['targets'] = {key: {'target': TARGETS[key], 'actual': value,
                                'pass': value is not None and value >= TARGETS[key]} for key, value in measured.items()}
    return summary


def fixture_names():
    return sorted(p.name for p in FIXTURES.iterdir() if (p / 'expected.json').is_file())


def load_explore():
    sys.path.insert(0, str(ROOT / 'src'))
    from seo_agents.exploration.repo_analysis.site_model import explore_site
    return explore_site


def pct(value):
    return '  -  ' if value is None else f'{value * 100:5.1f}'


def print_table(results, summary, stream=sys.stdout):
    header = f"{'fixture':<20} {'fw':<3} {'url R':>6} {'url P':>6} {'head':>6} {'hdg R':>6} {'hdg P':>6} {'lnk R':>6} {'lnk P':>6} {'img':>6} {'attr':>6}  error"
    print(header, file=stream)
    print('-' * len(header), file=stream)
    for r in results:
        s = r['scores']
        print(f"{r['fixture']:<20} {'ok' if r['framework_match'] else 'NO':<3} {pct(s['urls']['recall']):>6} {pct(s['urls']['precision']):>6} "
              f"{pct(s['head']['recall']):>6} {pct(s['headings']['recall']):>6} {pct(s['headings']['precision']):>6} "
              f"{pct(s['links']['recall']):>6} {pct(s['links']['precision']):>6} {pct(s['images']['recall']):>6} "
              f"{pct(s['attribution']['recall']):>6}  {(r['error'] or '')[:60]}", file=stream)
    print('-' * len(header), file=stream)
    s = summary
    print(f"{'TOTAL (micro)':<20} {pct(s['framework_accuracy']):>3} {pct(s['urls']['recall']):>6} {pct(s['urls']['precision']):>6} "
          f"{pct(s['head']['recall']):>6} {pct(s['headings']['recall']):>6} {pct(s['headings']['precision']):>6} "
          f"{pct(s['links']['recall']):>6} {pct(s['links']['precision']):>6} {pct(s['images']['recall']):>6} "
          f"{pct(s['attribution']['recall']):>6}  errors={s['errors']}", file=stream)
    for key, target in s['targets'].items():
        print(f"target {key:<22} >= {target['target']:.0%}: actual {pct(target['actual']).strip()}% "
              f"{'PASS' if target['pass'] else 'FAIL'}", file=stream)


def run(names=None, explore=None):
    if explore is None:
        try:
            explore = load_explore()
        except (ImportError, AttributeError) as error:
            message = 'site_model.explore_site unavailable: ' + str(error)

            def explore(path, render=False):
                raise RuntimeError(message)
    results = [score_fixture(name, explore) for name in (names or fixture_names())]
    return results, summarize(results)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--fixture', action='append', help='Score only this fixture (repeatable)')
    parser.add_argument('--json', type=Path, help='Write full results JSON here')
    parser.add_argument('--misses', action='store_true', help='Print every miss')
    args = parser.parse_args(argv)
    results, summary = run(args.fixture)
    print_table(results, summary)
    if args.misses:
        for r in results:
            for miss in r['misses']:
                print(r['fixture'], json.dumps(miss, ensure_ascii=False))
    if args.json:
        payload = {'summary': summary, 'fixtures': [{k: v for k, v in r.items() if k != '_tallies'} for r in results]}
        args.json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
