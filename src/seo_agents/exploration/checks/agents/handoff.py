"""Select useful earlier evidence for the next task without sharing conversations."""


PRIOR_WORK = {
    'urls': {},
    'page_structure': {'urls': 'Route entries and framework discovery.'},
    'internal_links': {
        'urls': 'Route destinations and entry templates.',
        'page_structure': 'Shared shells, navigation landmarks and source locations.',
    },
    'media': {
        'urls': 'Page entries and dynamic route instances.',
        'page_structure': 'Shared layouts and section context for media placement.',
    },
    'content': {
        'urls': 'Page entries and local content dependencies.',
        'page_structure': 'Headings, sections and shared layout composition.',
        'internal_links': 'Observed link source IDs for content link references.',
        'media': 'Observed media source IDs for content media references.',
    },
    'metadata': {
        'urls': 'Route scope, entry templates and framework configuration.',
        'page_structure': 'Page headings and shared layouts that may supply inherited head declarations.',
    },
    'eeat': {
        'urls': 'Page purpose and author/profile route candidates.',
        'page_structure': 'Sections and headings surrounding potential trust evidence.',
        'internal_links': 'Contact, about, policy and citation link candidates.',
        'content': 'Literal bylines, dates, credentials and experience copy with source context.',
        'metadata': 'Author/date declarations to investigate in source; not proof of visible copy.',
    },
}


def build_handoff(category, artifacts, runs):
    """Keep only task-relevant prior reports and notes; never include prior scripts."""
    return {key: {'purpose': purpose, 'report': artifacts[key],
                  'framework': runs.get(key, {}).get('framework'),
                  'notes': runs.get(key, {}).get('notes') or '',
                  'inspected_files': runs.get(key, {}).get('inspected_files') or []}
            for key, purpose in PRIOR_WORK[category].items() if key in artifacts}


def handoff_summary(handoff):
    return [{'category': key, 'use_when_relevant': entry['purpose'],
             'pages': len(entry['report']['pages']), 'framework': entry['framework'],
             'notes_preview': entry['notes'][:300], 'inspected_file_count': len(entry['inspected_files']),
             'recovery': 'query_prior_report for page evidence; read_prior_report at /notes or /inspected_files for discovery context'}
            for key, entry in handoff.items()]
