"""Derived initial-HTML views. No rendering, crawl expansion or SEO verdicts."""

from collections import Counter

from seo_agents.exploration.checks.agents.context import SourceHTML
from seo_agents.exploration.checks.evidence import resolve_destination


def extract_initial_response(response, response_ref, *, site_url):
    url = response.get('final_url') or response['requested_url']
    parser = SourceHTML(url)
    parser.feed(response['initial_html'])
    parser.close()
    records = [{**record, 'method': 'initial-response-html', 'url': url,
                'response_ref': response_ref} for record in parser.records]
    response['initial_html_evidence'] = records
    base_href = next((r['attributes']['href'] for r in records if r['kind'] == 'head-declaration'
                      and r.get('tag') == 'base' and 'href' in r['attributes']), None)
    links, titles = [], []
    body_text, main_text = [], []
    counts = Counter()
    for index, record in enumerate(records):
        reference = response_ref + '/initial_html_evidence/' + str(index)
        counts[record['kind']] += 1
        if record['kind'] == 'head-declaration' and record.get('tag') == 'title':
            titles.append({'value': record['text'], 'evidence_ref': reference,
                           'element_closed': record['element_closed']})
        if record['kind'] == 'literal-text':
            ancestors = set(record['ancestors'])
            if not ancestors & {'head', 'title', 'script', 'style', 'noscript', 'template'}:
                body_text.append(record['text'])
                if not ancestors & {'header', 'nav', 'footer', 'aside'}:
                    main_text.append(record['text'])
        if record['kind'] == 'navigation-link':
            try:
                resolved = resolve_destination(record['raw_value'] or '', url, site_url, base_href)
            except ValueError as error:
                resolved = {'kind': 'unresolved', 'destination': None, 'resolved_url': None,
                            'health': 'not-checked', 'error': type(error).__name__}
            attrs = record['attributes']
            links.append({**resolved, 'raw_value': record['raw_value'], 'anchor_text': record['anchor_text'],
                'accessible_name': attrs.get('aria-label') or record['anchor_text'] or ' '.join(record['image_alternatives']),
                'rel': attrs.get('rel'), 'target': attrs.get('target'), 'section': record['section'],
                'evidence_ref': reference, 'source_id': record['source_id'], 'observation': 'initial-response'})
    response['initial_html_summary'] = {'url': url, 'base_href': base_href, 'titles': titles,
        'record_counts': dict(counts), 'link_occurrences': links,
        'body_word_count': sum(len(text.split()) for text in body_text),
        'non_chrome_word_count': sum(len(text.split()) for text in main_text),
        'extraction_status': 'partial-body' if response.get('body_truncated') else 'captured-body-parsed',
        'limitations': ['Literal response HTML, not browser DOM or verified visible content.',
            'Text counts use whitespace splitting and semantic ancestor exclusions; CSS visibility and layout are unverified.',
            'Links are observations only. New destinations are not automatically fetched.']}
    return links


def reconcile_initial_responses(bundle, responses, *, site_url):
    """Compare observed sets without deciding which source/deployment is authoritative."""
    source_pages = {}
    for index, page in enumerate(bundle.get('reports', {}).get('metadata', {}).get('pages', [])):
        try:
            key = resolve_destination(page['url'], '/', site_url)['destination']
        except ValueError:
            continue
        source_pages.setdefault(key, []).append((index, page))
    comparisons = []
    for index, response in enumerate(responses):
        summary = response.get('initial_html_summary')
        if summary is None:
            continue
        matched = source_pages.get(response['requested_url'], [])
        source_rows = [page for _, page in matched]
        source_titles = sorted({d['title'] for p in source_rows for d in p.get('declarations', [])
                                if isinstance(d.get('title'), str)})
        initial_titles = sorted({r['value'] for r in summary['titles']})
        reasons = []
        if not source_rows:
            reasons.append('no-source-page-match')
        if not source_titles:
            reasons.append('no-source-title-declarations')
        if not initial_titles:
            reasons.append('no-response-title-declarations')
        if response.get('status') != 'success':
            reasons.append('unsuccessful-http-response')
        if response.get('body_truncated'):
            reasons.append('truncated-response-body')
        if any(not r['element_closed'] for r in summary['titles']):
            reasons.append('unclosed-response-title')
        comparisons.append({'requested_url': response['requested_url'], 'resolved_url': summary['url'],
            'response_ref': '/runtime/responses/' + str(index),
            'source_templates': sorted({p['template'] for p in source_rows}),
            'source_report_refs': ['/reports/metadata/pages/' + str(i) for i, _ in matched],
            'field': 'title-declaration-set', 'source_values': source_titles, 'initial_response_values': initial_titles,
            'status': ('observed-set-equal' if source_titles == initial_titles else 'observed-set-differs')
                if not reasons else 'not-comparable', 'limitations': reasons,
            'interpretation': 'Source declarations may include inherited defaults. Differences are evidence for review, not a verified metadata defect.'})
    bundle['runtime']['source_comparisons'] = comparisons
