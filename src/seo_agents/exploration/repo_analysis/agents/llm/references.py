"""Check content references against the verified reports for the same page."""

import json

from seo_agents.exploration.repo_analysis.tools.helpers import REPORT_FILES


REFERENCE_TARGETS = (('link_refs', 'internal_links', 'links'), ('media_refs', 'media', 'items'))


def brief(value):
    while len(json.dumps(value, ensure_ascii=True)) > 180:
        value = value[:len(value) // 2]
    return value


class CrossReportReferenceError(ValueError):
    """Bounded, actionable feedback for the agent that owns the references."""
    def __init__(self, issues):
        self.issues = issues
        super().__init__('Unmatched cross-report reference: ' + json.dumps(issues, ensure_ascii=True) +
            '. Query the prior report for the same url/template and field. Candidate IDs are investigation '
            'leads, not replacements. Correct source identity only after checking the occurrence. '
            'If the earlier report lacks that occurrence, preserve the content blocks and record the '
            'unresolved reference in coverage.limitations instead of emitting an unsupported reference.')


def validate_content_references(content, reports, *, require_targets=False):
    issues = []
    for field, category, records_field in REFERENCE_TARGETS:
        if category not in reports:
            if require_targets:
                raise ValueError('Missing cross-report target: ' + REPORT_FILES[category])
            continue  # Standalone extraction may not have predecessor reports.
        pages = {(page['url'], page['template']): {record['source_id'] for record in page[records_field]}
                 for page in reports[category]['pages']}
        for page_index, page in enumerate(content['pages']):
            targets = pages.get((page['url'], page['template']), set())
            for ref_index, ref in enumerate(page[field]):
                if not isinstance(ref['source_id'], str) or not isinstance(ref['report'], str):
                    raise ValueError(f'/pages/{page_index}/{field}/{ref_index}: source_id and report must be strings')
                filename = REPORT_FILES[category]
                if ref['report'] == filename and ref['source_id'] in targets:
                    continue
                source_id = ref['source_id']
                file_prefix = source_id.rsplit(':', 3)[0] + ':'
                candidates = sorted(target for target in targets if target.startswith(file_prefix))[:2]
                issue = {'pointer': f'/pages/{page_index}/{field}/{ref_index}',
                         'source_id': brief(source_id), 'url': brief(page['url']),
                         'template': brief(page['template']), 'expected_report': filename,
                         'reason': 'wrong report filename' if ref['report'] != filename else 'source_id absent from target page',
                         'target_record_count': len(targets),
                         'candidate_source_ids': [brief(candidate) for candidate in candidates]}
                if len(json.dumps(issues + [issue], ensure_ascii=True)) <= 1400:
                    issues.append(issue)
                # Keep feedback small enough to survive the agent's error budget.
                if len(issues) == 2:
                    raise CrossReportReferenceError(issues)
    if issues:
        raise CrossReportReferenceError(issues)
