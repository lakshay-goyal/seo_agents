"""SEO findings derived exclusively from saved inventory JSON, without a checkout."""

import json
from pathlib import Path
from urllib.parse import urlsplit

from seo_agents.exploration.checks.tools.contracts import validate_report

from seo_agents.exploration.checks.tools.helpers import REPORT_FILES, GENERIC_ANCHORS


def analyze_reports(reports):
    findings = []
    def add(category, page, code, evidence, recommendation):
        findings.append({'category': category, 'url': page['url'], 'code': code,
                         'status': 'review', 'evidence': evidence, 'recommendation': recommendation})
    for category, report in reports.items():
        validate_report(category, report)
        for page in report['pages']:
            if category == 'content' and not page['blocks']:
                add(category, page, 'no_static_content', [], 'Resolve coverage gaps and verify the rendered page before assessing content quality.')
            elif category == 'content':
                main = [b for b in page['blocks'] if b['section'] not in {'nav', 'header', 'footer'}]
                if sum(len(b['text'].split()) for b in main) < 100:
                    add(category, page, 'limited_static_copy', main, 'Review page intent and dynamic content before expanding copy; there is no universal minimum word count.')
            elif category == 'page_structure':
                headings = [h for h in page['headings'] if h.get('ui_state', 'initial') == 'initial']
                if not any(h['level'] == 1 for h in headings):
                    add(category, page, 'h1_not_observed', headings, 'Verify runtime headings; add a descriptive page heading if absent.')
                if sum(h['level'] == 1 for h in headings) > 1:
                    add(category, page, 'multiple_h1_candidates', headings, 'Review composed headings and establish a clear page topic.')
                for previous, current in zip(headings, headings[1:]):
                    if current['file'] == previous['file'] and current['level'] > previous['level'] + 1:
                        add(category, page, 'heading_level_skipped', [previous, current], 'Review heading nesting in the rendered section.')
            elif category == 'internal_links':
                for link in page['links']:
                    if link['kind'] == 'internal' and not link.get('accessible_name', link['anchor_text']):
                        add(category, page, 'link_text_not_observed', link, 'Verify an accessible label, including linked image alt, before changing the anchor.')
                    if link['kind'] == 'internal' and link['anchor_text'].lower() in GENERIC_ANCHORS:
                        add(category, page, 'generic_anchor', link, 'Use anchor text that describes the linked destination.')
            elif category == 'metadata':
                declarations = page['declarations']
                for field in ('title', 'description'):
                    if not any(d.get(field) or d.get('name') == field and d.get('content') for d in declarations):
                        add(category, page, field + '_not_observed', declarations, 'Resolve inherited or generated metadata; provide a page-specific value if absent.')
                for declaration in declarations:
                    title = declaration.get('title')
                    description = declaration.get('description') or (declaration.get('content') if declaration.get('name') == 'description' else None)
                    if title and len(title) > 60:
                        add(category, page, 'long_title_candidate', declaration, 'Review title clarity and search preview width; 60 characters is a review heuristic.')
                    if description and len(description) > 160:
                        add(category, page, 'long_description_candidate', declaration, 'Review description relevance and preview truncation; 160 characters is a review heuristic.')
            elif category == 'media':
                for item in page['items']:
                    if item['kind'] in {'img', 'next-image'} and 'alt' not in item and 'alt' not in item.get('unresolved_attributes', []):
                        add(category, page, 'alt_not_observed', item, 'Verify dynamic alt; describe meaningful images and use empty alt for decoration.')
                    if item['size_review'] == 'over_200_kib_review':
                        add(category, page, 'large_source_asset', item, 'Review optimized transfer bytes and rendered size; compress or resize where appropriate.')
                    if item['kind'] in {'img', 'next-image'} and not (item.get('width') and item.get('height')) and item.get('fill') != 'true':
                        add(category, page, 'dimensions_not_declared', item, 'Verify CSS aspect ratio or reserve dimensions to avoid layout shifts.')
            elif category == 'eeat':
                for signal in page['signals']:
                    if signal['status'] != 'candidate_evidence':
                        add(category, page, signal['signal'] + '_not_observed', signal, 'Review relevance to this page and verify runtime evidence; do not invent credentials or trust claims.')
    # Exact observed duplicates and unmatched destinations can be checked from JSON.
    for category, field in [('content', 'blocks'), ('metadata', 'declarations')]:
        seen = {}
        for page in reports.get(category, {}).get('pages', []):
            if category == 'content':
                value = ' '.join(b['text'] for b in page[field] if b['section'] not in {'nav', 'header', 'footer'})
            else:
                value = next((d['title'] for d in reversed(page[field]) if d.get('title')), '')
            if value and value in seen:
                add(category, page, 'duplicate_observed_' + ('copy' if category == 'content' else 'title'), {'matches_url': seen[value], 'observed_value': value}, 'Review whether these pages need distinct content; confirm runtime and inherited values.')
            elif value:
                seen[value] = page['url']
    known = {p['url'] for p in reports.get('urls', {}).get('pages', [])}
    if known:
        for page in reports.get('internal_links', {}).get('pages', []):
            for link in page['links']:
                target = urlsplit(link['target_url']).path
                if link['kind'] == 'internal' and target not in known:
                    add('internal_links', page, 'destination_not_in_inventory', link, 'Verify dynamic routes, redirects, assets and runtime routing before treating this as a broken link.')
    return {'schema_version': '2.0', 'basis': 'inventory-json-only', 'findings': findings,
            'limitations': 'Findings are review candidates. Static absence, byte budgets and E-E-A-T candidates are not verified SEO failures.'}


def analyze_directory(directory):
    """Load the seven saved artifacts; never read application source or the network."""
    directory = Path(directory)
    return analyze_reports({key: json.loads((directory / filename).read_text()) for key, filename in REPORT_FILES.items()})


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(analyze_directory(args.directory), indent=2, ensure_ascii=False))
