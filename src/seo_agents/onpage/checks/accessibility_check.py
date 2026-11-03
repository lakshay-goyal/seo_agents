"""Check 7 (deterministic): accessible names for icon-only links, JS-only links, broken links."""

from __future__ import annotations

from urllib.parse import urlsplit

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.checks.check_helpers import result
from seo_agents.onpage.checks.base_check import OnpageCheck

# ====================================================================== 7. accessible names (deterministic)
KNOWN_NAMES = {'github.com': 'GitHub', 'gitlab.com': 'GitLab', 'linkedin.com': 'LinkedIn', 'x.com': 'X (Twitter)',
               'twitter.com': 'X (Twitter)', 'instagram.com': 'Instagram', 'facebook.com': 'Facebook', 'youtube.com': 'YouTube',
               'medium.com': 'Medium', 'dev.to': 'DEV', 'cal.com': 'Book a call', 'calendly.com': 'Book a call'}


def link_name(href):
    if href.startswith('mailto:'):
        return 'Email'
    if href.startswith('tel:'):
        return 'Phone'
    host = (urlsplit(href).hostname or '').lower().removeprefix('www.')
    return KNOWN_NAMES.get(host) or (host.split('.')[0].title() if host else 'Link to ' + href)


def check_accessibility(ctx, prior=None, writer=None):
    changes, advice = [], []
    for link in ctx['links']:
        href = link.get('href') or ''
        if not link['renders']:
            continue
        if href in ('#', '') or href.lower().startswith('javascript:'):
            advice.append(ops.advice(ctx, 'accessibility', 'links.js_only', f'Link "{link["accessible_name"] or href}" has no crawlable '
                                     'href (#/javascript:); use a real URL so crawlers can follow it', impact='medium',
                                     evidence={'link_id': link['id']}))
            continue
        if link['accessible_name']:
            continue  # visible text, aria-label, img alt or title already name it (no false positives)
        edit = link.get('anchor_edit') or link.get('edit')
        changes.append(ops.make_change(ctx, 'accessibility', 'links.accessible_name', None, link_name(href),
                                       target=ops.target_of(edit, attr='aria-label', confidence=link.get('confidence')), op='set_attr',
                                       confidence=0.85, rationale='Icon-only link has no accessible name or anchor context',
                                       extra={'href': href, 'link_id': link['id']}))
        status = link.get('status')
        if isinstance(status, int) and status >= 400 and status not in (401, 403, 429, 999):
            changes.append(ops.make_change(ctx, 'accessibility', 'links.broken', href, '[VERIFY: working URL]',
                                           target=ops.target_of(link.get('edit')), op='set_attr', confidence=0.7,
                                           rationale=f'Link returns HTTP {status}', extra={'link_id': link['id']}))
    for link in ctx['links']:
        status = link.get('status')
        if link['renders'] and link['accessible_name'] and isinstance(status, int) and status >= 400 and status not in (401, 403, 429, 999):
            changes.append(ops.make_change(ctx, 'accessibility', 'links.broken', link['href'], '[VERIFY: working URL]',
                                           target=ops.target_of(link.get('edit')), op='set_attr', confidence=0.7,
                                           rationale=f'Link returns HTTP {status}', extra={'link_id': link['id']}))
    return result(changes=changes, advice=advice)


class LinkAccessibilityCheck(OnpageCheck):
    """Check: Accessible names for icon-only links, JS-only links, broken links (deterministic)."""

    name = 'accessibility'
    skill = 'link-accessibility'
    uses_model = False
    depends_on = ()

    def run(self, ctx, prior, writer):
        return check_accessibility(ctx, prior, writer)
