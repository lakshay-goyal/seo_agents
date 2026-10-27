"""Validate the versioned artifact contract before publishing or analyzing JSON."""

from seo_agents.exploration.checks.tools.helpers import REPORT_FILES

PAGE_FIELDS = {
    'content': {'blocks', 'word_count', 'link_refs', 'media_refs'},
    'page_structure': {'headings', 'landmarks'},
    'internal_links': {'links'},
    'metadata': {'declarations', 'inheritance_note'},
    'media': {'items'},
    'urls': {'depth', 'query_links'},
    'eeat': {'signals', 'trust_links', 'citations', 'interpretation'},
}


def validate_report(category, report):
    if category not in REPORT_FILES:
        raise ValueError('Unknown inventory category: ' + category)
    if not isinstance(report, dict) or report.get('schema_version') != '2.0' or report.get('category') != category:
        raise ValueError('Expected schema 2.0 inventory for ' + category)
    top = {'schema_version', 'category', 'extraction', 'pages'}
    optional_top = {'assets'} if category == 'media' else set()
    if not top <= set(report) or set(report) - top - optional_top:
        raise ValueError('Unexpected top-level fields in ' + category)
    if category == 'media' and 'assets' in report and not isinstance(report['assets'], list):
        raise ValueError('Expected media asset list')
    if not isinstance(report['pages'], list) or not isinstance(report['extraction'], dict):
        raise ValueError('Invalid pages or extraction in ' + category)
    required = {'url', 'template', 'is_dynamic', 'coverage'} | PAGE_FIELDS[category]
    for page in report['pages']:
        optional = {'sections'} if category == 'content' else set()
        if not isinstance(page, dict) or not required <= set(page) or set(page) - required - optional:
            raise ValueError('Unexpected page fields in ' + category)
        if not isinstance(page['url'], str) or not page['url'].startswith('/'):
            raise ValueError('Expected route URL in ' + category)
        if not isinstance(page['coverage'], dict) or page['coverage'].get('mode') != 'static-source':
            raise ValueError('Expected static-source coverage in ' + category)
        if category == 'content' and 'sections' in page and not isinstance(page['sections'], list):
            raise ValueError('Expected content section list')
        for field in PAGE_FIELDS[category] - {'word_count', 'depth', 'interpretation', 'inheritance_note'}:
            if not isinstance(page[field], list):
                raise ValueError('Expected list for ' + category + '.' + field)
    return report
