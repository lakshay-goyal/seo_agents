"""Read SKILL.md-style prompt files: an optional `---` frontmatter block of `key: value` lines, then the body."""

import re

FRONTMATTER_LINE = re.compile(r'(?m)^(\w+):\s*(.+)$')


def split_frontmatter(text):
    """(frontmatter dict, body) for `---\\nkey: value\\n---\\nbody`; ({}, text) when there is no frontmatter.

    The body is returned exactly as written (not stripped); values are strings.
    """
    if not text.startswith('---'):
        return {}, text
    _, front, body = text.split('---', 2)
    return dict(FRONTMATTER_LINE.findall(front)), body
