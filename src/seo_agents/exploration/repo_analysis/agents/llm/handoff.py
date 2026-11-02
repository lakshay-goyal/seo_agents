"""Select useful earlier evidence for the next task without sharing conversations."""


# Dependency DAG (graph.py): urls -> {page_structure, internal_links, media, metadata} -> content -> eeat.
# A category may only consult reports that are guaranteed complete before it starts.
PRIOR_WORK = {
    'urls': {},
    'page_structure': {'urls': 'Route entries and framework discovery.'},
    'internal_links': {'urls': 'Route destinations and entry templates.'},
    'media': {'urls': 'Page entries and dynamic route instances.'},
    'metadata': {'urls': 'Route scope, entry templates and framework configuration.'},
    'content': {
        'urls': 'Page entries and local content dependencies.',
        'page_structure': 'Headings, sections and shared layout composition.',
        'internal_links': 'Observed link source IDs for content link references.',
        'media': 'Observed media source IDs for content media references.',
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
             'notes_preview': entry['notes'][:300], 'inspected_file_count': len(entry['inspected_files'])}
            for key, entry in handoff.items()]
