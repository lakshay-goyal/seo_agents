"""Check 17 (deterministic): hreflang annotations and the <html lang> value.

Missing <html lang> is owned by `technical` (technical.lang); this check validates values and consistency.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.reports.exploration_reports import value
from seo_agents.onpage.checks.check_helpers import result
from seo_agents.onpage.checks.base_check import OnpageCheck

CHECK = 'hreflang_i18n'
LANG_CODE = re.compile(r'^[a-z]{2,3}(-[a-z]{4})?(-([a-z]{2}|\d{3}))?$', re.I)  # <html lang> (BCP 47, 2-3 letter language)
HREFLANG_CODE = re.compile(r'^[a-z]{2}(-[a-z]{4})?(-([a-z]{2}|\d{3}))?$', re.I)  # hreflang: ISO 639-1 (2 letters) only
# Common mistakes -> the valid code.
REGION_FIXES = {'uk': 'gb', 'el': 'gr'}  # region subtags people confuse (en-UK -> en-GB)
LANG_FIXES = {'jp': 'ja', 'cn': 'zh', 'kr': 'ko', 'dk': 'da', 'cz': 'cs', 'gr': 'el', 'ua': 'uk', 'iw': 'he',
              'in': 'id', 'ji': 'yi'}
# Country codes used as a whole hreflang value.
COUNTRY_AS_LANG = {'gb': 'en-GB', 'us': 'en-US'}
# ISO 639-2/3 codes that have an ISO 639-1 equivalent (hreflang needs the 2-letter one; BCP 47 prefers it too).
ISO3_TO_1 = {'eng': 'en', 'fra': 'fr', 'fre': 'fr', 'deu': 'de', 'ger': 'de', 'spa': 'es', 'ita': 'it', 'por': 'pt', 'nld': 'nl',
             'dut': 'nl', 'jpn': 'ja', 'zho': 'zh', 'chi': 'zh', 'kor': 'ko', 'rus': 'ru', 'ara': 'ar', 'hin': 'hi', 'pol': 'pl',
             'swe': 'sv', 'dan': 'da', 'nor': 'no', 'fin': 'fi', 'tur': 'tr', 'ukr': 'uk', 'ces': 'cs', 'cze': 'cs', 'ell': 'el',
             'gre': 'el', 'heb': 'he', 'ind': 'id', 'vie': 'vi', 'tha': 'th'}


def valid_code(code, *, allow_default=True):
    """Valid <html lang> value (2-3 letter language)."""
    code = str(code or '').strip()
    return (allow_default and code.lower() == 'x-default') or bool(LANG_CODE.match(code))


def valid_hreflang(code):
    """Valid hreflang value: x-default or ISO 639-1 (2 letters) [+ script] [+ ISO 3166-1 alpha-2 / UN M.49 region]."""
    code = str(code or '').strip()
    return code.lower() == 'x-default' or bool(HREFLANG_CODE.match(code))


def suggest_code(code):
    """Best-effort valid replacement for an invalid/suspicious code (en_US -> en-US, en-UK -> en-GB, jp -> ja), else None."""
    text = str(code or '').strip().replace('_', '-')
    parts = [p for p in text.split('-') if p]
    if not parts or not re.fullmatch(r'[A-Za-z]{2,3}', parts[0]):
        return None
    if len(parts) == 1 and parts[0].lower() in COUNTRY_AS_LANG:
        return COUNTRY_AS_LANG[parts[0].lower()]
    first = parts[0].lower()
    lang = LANG_FIXES.get(first) or ISO3_TO_1.get(first) or first
    rest = []
    for part in parts[1:]:
        if re.fullmatch(r'[A-Za-z]{4}', part):
            rest.append(part.title())
        elif re.fullmatch(r'[A-Za-z]{2}', part):
            rest.append(REGION_FIXES.get(part.lower(), part.lower()).upper())
        elif re.fullmatch(r'\d{3}', part):
            rest.append(part)
        else:
            return None
    fixed = '-'.join([lang] + rest)
    return fixed if LANG_CODE.match(fixed) and fixed != str(code).strip() else None


def suspicious(code):
    """Valid-looking but almost certainly wrong: en-UK (region UK is not ISO 3166), jp/cn/kr/gb/us country codes as languages,
    3-letter codes (eng) that have a 2-letter equivalent, underscores (en_US)."""
    text = str(code or '').strip().lower()
    parts = text.replace('_', '-').split('-')
    return '_' in text or (bool(parts) and (parts[0] in LANG_FIXES or parts[0] in ISO3_TO_1
                                            or (len(parts) == 1 and parts[0] in COUNTRY_AS_LANG)
                                            or (len(parts) > 1 and parts[-1] in REGION_FIXES)))


def norm(url):
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        parts = urlsplit(url.strip())
        return (parts.hostname or '').lower(), parts.path.rstrip('/') or '/'
    except ValueError:  # e.g. 'https://[object Object]/x'
        return None


def self_entry(ctx, entries):
    """The hreflang entry that annotates THIS page: href path == page url (else the entry for the page language)."""
    own = (ctx.get('url') or '/').rstrip('/') or '/'
    for entry in entries:
        href = entry.get('href')
        if isinstance(href, str) and (norm(href) or (None, None))[1] == own:
            return entry
    page_lang = str(value((ctx.get('head') or {}).get('lang')) or '').lower()
    return next((e for e in entries if page_lang and str(e.get('hreflang') or '').lower() == page_lang), None)


def hreflang_findings(ctx, advice, changes, data):
    head = ctx.get('head') or {}
    entries = [e for e in head.get('hreflang') or [] if isinstance(e, dict)]
    data['hreflang_count'] = len(entries)
    if not entries:
        return None
    codes = [str(e.get('hreflang') or '').strip() for e in entries]
    page_primary = str(value(head.get('lang')) or '').strip().split('-')[0].lower()
    # English site: <html lang="en*">, or (no lang declared) another entry of the set is English. ctx['lang'] defaults to 'en'.
    english = page_primary == 'en' or (not page_primary and any(c.lower().split('-')[0] == 'en' for c in codes))
    for entry, code in zip(entries, codes):
        if code.lower() == 'uk':
            if english:  # an English site using "uk" almost always means the United Kingdom
                advice.append(ops.advice(ctx, CHECK, 'hreflang.uk', 'hreflang="uk" means Ukrainian, not the United Kingdom; if this '
                                         'URL is British English use "en-GB".', impact='medium', evidence={'href': entry.get('href')}))
            continue
        if valid_hreflang(code) and not suspicious(code):
            continue
        fixed = suggest_code(code)
        target = ops.target_of(entry.get('edit'), attr='hreflang')
        if fixed and target:
            changes.append(ops.make_change(ctx, CHECK, 'technical.hreflang', code, fixed, op='set_attr', target=target,
                                           confidence=0.8, impact='medium',
                                           rationale=f'"{code}" is not a valid ISO 639-1 (2-letter) language (+ ISO 3166-1 region) '
                                                     'code; Google ignores invalid hreflang entries'))
        else:
            advice.append(ops.advice(ctx, CHECK, f'hreflang.invalid_code.{code or "empty"}', f'hreflang="{code}" is invalid'
                                     + (f'; use "{fixed}"' if fixed else '') + ' (2-letter ISO 639-1 language, optional REGION, e.g. '
                                     'en or en-GB; "uk" is Ukrainian, the United Kingdom is GB; "eng" -> "en").', impact='medium',
                                     evidence={'href': entry.get('href')}))
    lowered = [c.lower() for c in codes]
    duplicates = sorted({c for c in lowered if c and lowered.count(c) > 1})
    if duplicates:
        advice.append(ops.advice(ctx, CHECK, 'hreflang.duplicate', f'hreflang codes used more than once: {", ".join(duplicates)}; '
                                 'each language/region must map to exactly one URL.', evidence={'codes': duplicates}))
    relative = [e.get('href') for e in entries if not re.match(r'(?i)^https?://', str(e.get('href') or ''))]
    if relative:
        advice.append(ops.advice(ctx, CHECK, 'hreflang.relative', 'hreflang hrefs must be absolute URLs (https://host/path).',
                                 evidence={'hrefs': relative[:10]}))
    mine = self_entry(ctx, entries)
    if mine is None:
        advice.append(ops.advice(ctx, CHECK, 'hreflang.self', 'The hreflang set has no self-referencing entry for this page; each '
                                 'page must list itself as well as its alternates.', evidence={'codes': codes}))
    languages = {c.split('-')[0] for c in lowered if c and c != 'x-default'}
    if len(languages) > 1 and 'x-default' not in lowered:
        advice.append(ops.advice(ctx, CHECK, 'hreflang.x_default', 'Several languages but no hreflang="x-default"; add one for '
                                 'users whose language is not covered (usually the language selector or main version).',
                                 impact='low', evidence={'languages': sorted(languages)}))
    canonical = value(head.get('canonical'))
    if mine is not None and isinstance(canonical, str) and canonical.strip() and norm(mine.get('href')) and norm(canonical) \
            and norm(mine.get('href'))[1] != norm(canonical)[1]:
        advice.append(ops.advice(ctx, CHECK, 'hreflang.canonical', 'The self hreflang URL differs from the canonical; hreflang must '
                                 'point at canonical URLs or Google ignores the set.', impact='medium',
                                 evidence={'hreflang': mine.get('href'), 'canonical': canonical}))
    own = (ctx.get('url') or '/').rstrip('/') or '/'
    issues = ((ctx.get('site') or {}).get('hreflang') or {}).get('issues') if isinstance((ctx.get('site') or {}).get('hreflang'), dict) else None
    mine_issues = [i for i in issues or [] if isinstance(i, dict) and (i.get('page') == ctx.get('page_id')
                   or (isinstance(i.get('page'), str) and (norm(i['page']) or (None, None))[1] == own))]
    if mine_issues:
        advice.append(ops.advice(ctx, CHECK, 'hreflang.return_tags', 'hreflang return-tag problems: alternates must link back to this '
                                 'page with the same set; one-way annotations are ignored.', impact='high',
                                 evidence={'issues': mine_issues[:10]}))
    return mine


def lang_findings(ctx, mine, advice, changes, data):
    head = ctx.get('head') or {}
    lang = value(head.get('lang'))
    lang = lang.strip() if isinstance(lang, str) else ''
    data['html_lang'] = lang or None
    if not lang:
        return  # missing lang is technical.lang
    if not valid_code(lang, allow_default=False) or suspicious(lang):
        fixed = suggest_code(lang)
        target = ops.target_of(head.get('lang'), attr='lang')
        changes.append(ops.make_change(ctx, CHECK, 'technical.lang_value', lang, fixed or '[VERIFY: BCP 47 language code, e.g. en or en-GB]',
                                       op='set_attr', target=target, confidence=0.8 if fixed else 0.5, impact='medium',
                                       status='proposed' if fixed else 'needs_input',
                                       rationale=f'<html lang="{lang}"> is not a valid BCP 47 code; screen readers, translation and '
                                                 'search language detection use it'))
        return
    headers = (ctx.get('http') or {}).get('headers') if isinstance((ctx.get('http') or {}).get('headers'), dict) else {}
    content_language = str(headers.get('content-language') or '').split(',')[0].strip()
    primary = lang.split('-')[0].lower()
    if content_language and content_language.split('-')[0].lower() != primary:
        advice.append(ops.advice(ctx, CHECK, 'i18n.content_language', f'<html lang="{lang}"> disagrees with the Content-Language '
                                 f'header ("{content_language}"); make them match.', impact='low',
                                 evidence={'html_lang': lang, 'content-language': content_language}))
    code = str((mine or {}).get('hreflang') or '').strip()
    if code and code.lower() != 'x-default' and code.split('-')[0].lower() != primary:
        advice.append(ops.advice(ctx, CHECK, 'i18n.lang_hreflang', f'<html lang="{lang}"> disagrees with this page\'s own hreflang '
                                 f'("{code}").', evidence={'html_lang': lang, 'hreflang': code}))


def check_hreflang_i18n(ctx, prior=None, writer=None):
    changes, advice, data = [], [], {}
    mine = hreflang_findings(ctx, advice, changes, data)
    lang_findings(ctx, mine, advice, changes, data)
    return result(data=data, changes=changes, advice=advice)


class HreflangI18nCheck(OnpageCheck):
    """Check: hreflang codes, self/x-default/return tags, absolute hrefs, canonical alignment, <html lang> value (deterministic)."""

    name = 'hreflang_i18n'
    skill = 'hreflang-i18n'
    uses_model = False
    depends_on = ()

    def run(self, ctx, prior, writer):
        return check_hreflang_i18n(ctx, prior or {}, writer)
