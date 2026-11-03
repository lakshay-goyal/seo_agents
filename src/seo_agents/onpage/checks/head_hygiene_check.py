"""Check 12 (deterministic): the CURRENT <title>/<meta description> and head hygiene (page_plan only validates new values)."""

from __future__ import annotations

import re

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.validation import content_validators as v
from seo_agents.onpage.reports.exploration_reports import value
from seo_agents.onpage.validation.content_policy import LIMITS
from seo_agents.onpage.checks.check_helpers import brand, keywords_of, researched_plan, result
from seo_agents.onpage.checks.base_check import OnpageCheck

# ====================================================================== 13. head hygiene (deterministic)
TITLE_MAX_PX = 580        # Google desktop SERP title, Arial 20px
DESCRIPTION_MAX_PX = 920  # Google desktop SERP snippet, Arial 14px
NEAR_DUPLICATE = 0.8
PRIMARY_MAX_POSITION = 30
PLACEHOLDER_TITLES = {'react app', 'vite + react', 'vite + react + ts', 'vite app', 'vite + vue', 'vite + svelte', 'home', 'untitled',
                      'document', 'next.js', 'create next app', 'nuxt', 'gatsby', 'astro', 'my app', 'app', 'index', 'new page',
                      'welcome', 'title', 'page title', 'website', 'my website', 'angular', 'sveltekit', 'remix'}
# Arial advance widths in 1/1000 em (approximate; unknown characters use the average lowercase width).
NARROW = {**dict.fromkeys("ijl|!.,:;'", 222), **dict.fromkeys('ftrI()[]/-', 300), ' ': 278}
WIDE = {**dict.fromkeys('mwMW', 860), **dict.fromkeys('@%', 900)}


def px_width(text, size):
    total = 0
    for ch in text or '':
        if ch in NARROW:
            total += NARROW[ch]
        elif ch in WIDE:
            total += WIDE[ch]
        elif ch.isupper():
            total += 690
        elif ch.isdigit():
            total += 556
        else:
            total += 540
    return round(total * size / 1000)


def squash(text):
    return re.sub(r'\s+', ' ', text).strip() if isinstance(text, str) else ''


def tokens(text, drop=()):
    return {w for w in v.words(text) if w not in v.STOPWORDS and w not in drop}


def jaccard(a, b, drop=()):
    left, right = tokens(a, drop), tokens(b, drop)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def duplicates(text, others, drop=()):
    """(exact, near) matches of `text` among the other pages' values."""
    exact, near = [], []
    if not text:
        return exact, near
    key = squash(text).lower()
    for other in others or []:
        if not isinstance(other, str) or not other.strip():
            continue
        if squash(other).lower() == key:
            exact.append(other)
        elif jaccard(text, other, drop) >= NEAR_DUPLICATE:
            near.append(other)
    return exact, near


def meta_attr(tag, name):
    for key, item in (tag or {}).items():
        if isinstance(key, str) and key.lower() == name:
            return item
    return None


def has_charset(ctx, meta_tags):
    content_type = ((ctx.get('http') or {}).get('headers') or {}).get('content-type') or ''
    if 'charset=' in str(content_type).lower():
        return True
    for tag in meta_tags:
        if not isinstance(tag, dict):
            continue
        if meta_attr(tag, 'charset'):
            return True
        if str(meta_attr(tag, 'http-equiv') or '').lower() == 'content-type' and 'charset=' in str(tag.get('content') or '').lower():
            return True
    return False


def check_head_hygiene(ctx, prior=None, writer=None):
    prior = prior or {}
    changes, advice = [], []
    head = ctx.get('head') or {}
    title = squash(ctx.get('title') or value(head.get('title')))
    description = squash(ctx.get('description') or value(head.get('description')))
    name = squash(brand(ctx, prior) or '')
    brand_words = set(v.words(name))
    t_low, t_high = LIMITS['TITLE_CHARS']
    d_low, d_high = LIMITS['DESCRIPTION_CHARS']
    title_px = px_width(title, 20)
    description_px = px_width(description, 14)
    note = lambda topic, text, impact='medium', evidence=None: advice.append(  # noqa: E731
        ops.advice(ctx, 'head_hygiene', topic, text, impact=impact, evidence=evidence))
    # A shared shell serves one <head> to every route: duplicate/placeholder titles are a symptom of the missing prerender
    # (technical check owns that change), so report them once on the home page instead of on every route.
    shell = ctx.get('head_scope') == 'shared-shell'
    owner = ctx.get('site_items_owner', ctx.get('is_home'))
    shell_report = not shell or bool(owner)
    # page_plan already proposes a new title/description when the current one is missing or out of range: no second report
    planned = {c.get('field') for c in ((prior or {}).get('page_plan') or {}).get('changes') or [] if isinstance(c, dict)}
    report_title = shell_report and 'head.title' not in planned
    report_description = shell_report and 'head.description' not in planned
    shell_note = ' (Shared shell: every route serves this same <head>; the root fix is prerendering / per-route heads.)'

    # ---- title
    if not title and report_title:
        note('head.title_missing', 'The page has no <title>; every route needs its own descriptive title.', 'high')
    elif title:
        if len(title) < t_low and report_title:
            note('head.title_length', f'Title is {len(title)} characters (aim for {t_low}-{t_high}); it under-describes the page.',
                 'medium', {'title': title, 'chars': len(title)})
        elif (len(title) > t_high or title_px > TITLE_MAX_PX) and report_title:
            note('head.title_length', f'Title is {len(title)} characters / ~{title_px}px; Google truncates beyond ~{TITLE_MAX_PX}px '
                 f'({t_high} chars). Put the distinguishing words first.', 'medium', {'title': title, 'chars': len(title), 'px': title_px})
        if v.normalize(title) in PLACEHOLDER_TITLES:
            if shell_report:
                note('head.title_placeholder', f'Title "{title}" is a framework/boilerplate placeholder, not a description of this '
                     'page.' + (shell_note if shell else ''), 'high', {'title': title})
        elif name and v.normalize(title) == v.normalize(name):
            note('head.title_brand_only', f'Title is only the brand name "{name}"; add what the page is about before the brand.',
                 'high', {'title': title})
        if name and v.normalize(title) != v.normalize(name):
            count = title.lower().count(name.lower())
            if count >= 2:
                note('head.title_brand', f'Brand "{name}" appears {count} times in the title; keep it once (as a suffix).', 'low',
                     {'title': title})
            elif count == 0 and owner:  # a site-wide convention: reported once, on the home page
                note('head.title_brand', f'Title has no brand suffix ("... | {name}"); a consistent suffix across the site helps '
                     'recognition in results.', 'low', {'title': title})
        if description and squash(description).lower() == title.lower():
            note('head.title_equals_description', 'The meta description repeats the title word for word; write a one-line answer '
                 'with a reason to click instead.', 'medium', {'title': title})
        exact, near = duplicates(title, ctx.get('other_titles'), brand_words)
        if (exact or near) and shell_report:
            note('head.title_duplicate', f'Title is {"identical" if exact else "nearly identical"} to {len(exact) + len(near)} other '
                 'page(s); each URL needs a unique title or search engines rewrite/fold them.' + (shell_note if shell else ''),
                 'high' if exact else 'medium',
                 {'title': title, 'exact': exact[:5], 'near': near[:5]})
        primary = keywords_of(prior).get('primary') or (researched_plan(ctx) or {}).get('primary')
        if isinstance(primary, str) and primary.strip():
            position = title.lower().find(primary.strip().lower())
            if position > PRIMARY_MAX_POSITION:
                note('head.title_keyword_position', f'Primary keyword "{primary}" starts at character {position}; front-load it '
                     f'(within the first {PRIMARY_MAX_POSITION}) so it survives truncation.', 'low',
                     {'title': title, 'primary': primary, 'position': position})

    # ---- description
    if not description and report_description:
        note('head.description_missing', 'No meta description; search engines will pick arbitrary page text for the snippet.', 'high')
    elif description:
        if len(description) < d_low and report_description:
            note('head.description_length', f'Meta description is {len(description)} characters (aim for {d_low}-{d_high}).', 'low',
                 {'description': description, 'chars': len(description)})
        elif (len(description) > d_high or description_px > DESCRIPTION_MAX_PX) and report_description:
            note('head.description_length', f'Meta description is {len(description)} characters / ~{description_px}px; it will be '
                 f'truncated beyond ~{DESCRIPTION_MAX_PX}px.', 'low',
                 {'description': description, 'chars': len(description), 'px': description_px})
        exact, near = duplicates(description, ctx.get('other_descriptions'), brand_words)
        if (exact or near) and shell_report:
            note('head.description_duplicate', f'Meta description is {"identical" if exact else "nearly identical"} to '
                 f'{len(exact) + len(near)} other page(s); write one per URL.' + (shell_note if shell else ''),
                 'medium' if exact else 'low',
                 {'description': description, 'exact': exact[:5], 'near': near[:5]})

    # ---- meta keywords (ignored by Google, leaks targeting to competitors)
    keywords_field = head.get('keywords')
    keywords = value(keywords_field)
    if isinstance(keywords, str) and keywords.strip():
        changes.append(ops.make_change(ctx, 'head_hygiene', 'head.meta_keywords', keywords, None,
                                       target=ops.target_of(keywords_field), op='remove', confidence=0.8, impact='low',
                                       rationale='<meta name="keywords"> is ignored by Google and publishes your keyword targeting'))

    # ---- raw meta tags (only when exploration persisted them)
    meta_tags = [t for t in (ctx.get('meta_tags') or []) if isinstance(t, dict)]
    refresh = [t for t in meta_tags if str(meta_attr(t, 'http-equiv') or '').lower() == 'refresh']
    if refresh:
        note('head.meta_refresh', 'A <meta http-equiv="refresh"> redirect/reload is present; use a server-side 301/308 instead '
             '(meta refresh is a weak, slow redirect signal and hurts accessibility).', 'high',
             {'content': [t.get('content') for t in refresh][:3]})
    if meta_tags and not has_charset(ctx, meta_tags):
        note('head.charset', 'No character encoding declared (no <meta charset> and no charset in Content-Type); add '
             '<meta charset="utf-8"> as the first element in <head>.', 'low')

    data = {'title': title or None, 'title_chars': len(title), 'title_px': title_px, 'description_chars': len(description),
            'description_px': description_px, 'meta_keywords': bool(isinstance(keywords, str) and keywords.strip()),
            'meta_refresh': bool(refresh)}
    return result(data=data, changes=changes, advice=advice)


class HeadHygieneCheck(OnpageCheck):
    """Check: Current title/description length, pixel width, placeholders, duplicates, brand, meta keywords/refresh/charset (deterministic)."""

    name = 'head_hygiene'
    skill = 'head-hygiene'
    uses_model = False
    depends_on = ()

    def run(self, ctx, prior, writer):
        return check_head_hygiene(ctx, prior or {}, writer)
