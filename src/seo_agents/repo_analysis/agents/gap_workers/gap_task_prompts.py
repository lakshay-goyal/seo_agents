"""Gap-worker skill prompts: task -> SKILL.md (lib/prompts/gap_workers), numeric limits filled in, and only the\nexamples for the repository's template family (progressive disclosure)."""

from __future__ import annotations

import re

from seo_agents.lib.models import skill_model_meta
from seo_agents.lib.prompts import GAP_WORKER_PROMPTS_DIR
from seo_agents.utils.prompt_files import split_frontmatter

SKILLS_DIR = GAP_WORKER_PROMPTS_DIR
TASK_SKILL = {'attribute_value': 'attribute-value', 'explain_computed': 'explain-computed', 'topic_intent': 'topic-intent',
              'eeat_classify': 'eeat-classify', 'enumerate_instances': 'enumerate-instances', 'route_adapter': 'route-adapter'}


# Numeric limits: the single source for validators AND the skill text ({{NAME}} placeholders).
LIMITS = {
    'MAX_CANDIDATES': 10, 'REASON_MAX': 200, 'VALUE_SOURCE_MAX': 160,
    'TOPIC_MIN_WORDS': 2, 'TOPIC_MAX_WORDS': 6, 'QUERY_MIN_WORDS': 2, 'QUERY_MAX_WORDS': 6, 'MAX_QUERIES': 3,
    'MIN_RULES': 1, 'MAX_RULES': 8, 'HELPER_LINES': 40, 'MAX_HELPERS': 3, 'HEAD_LINES': 20, 'MAX_HEADS': 30,
    'COPY_WORDS': 300, 'MAX_FILES': 400,
}


# ---------------------------------------------------------------- skills
def load_skill(name, family=None):
    """Skill text with numeric limits filled in and only this family's examples (progressive disclosure).

    Example sections are `### <family> ...`; `### any ...` sections are always kept. Each family
    should ship one positive and one negative example.
    """
    text = (SKILLS_DIR / name / 'SKILL.md').read_text()
    for key, value in LIMITS.items():
        text = text.replace('{{' + key + '}}', str(value))
    meta, body = split_frontmatter(text)
    meta = {**meta, **skill_model_meta('gap_worker.' + name)}  # tier / ladder / votes live in lib/models.py
    body = body.strip() if text.startswith('---') else text
    match = re.search(r'(## Examples\n)(.*?)(?=\n## |\Z)', body, re.S)
    if match:
        sections = re.split(r'(?m)^(?=### )', match.group(2))
        intro = [s for s in sections if not s.startswith('### ')]
        named = [s for s in sections if s.startswith('### ')]

        def label(section):
            return section[4:].split('\n', 1)[0].strip().split()[0].casefold() if section[4:].strip() else ''
        always = [s for s in named if label(s) == 'any']
        family_specific = [s for s in named if family and label(s) in family_aliases(family)]
        keep = always + family_specific if family_specific else always + [s for s in named if s not in always][:2]
        body = body[:match.start(2)] + ''.join(intro + keep).rstrip() + '\n' + body[match.end(2):]
    return body, meta


def family_aliases(family):
    return {'react': {'jsx', 'next'}, 'vue': {'vue', 'jsx'}, 'svelte': {'svelte', 'jsx'}, 'astro': {'astro', 'jsx'},
            'liquid': {'liquid'}, 'php': {'php'}, 'blade': {'php'}, 'twig': {'php', 'jinja'}, 'jinja': {'jinja', 'php'},
            'erb': {'php'}, 'handlebars': {'liquid'}, 'go-template': {'liquid'}, 'html': {'php', 'jsx'}}.get(family, {family})
