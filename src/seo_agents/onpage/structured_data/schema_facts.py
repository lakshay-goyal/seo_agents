"""Facts for JSON-LD read from the page exactly as visitors see it (never invented).

Every function takes the on-page `ctx` (and the page plan where the plan adds visible content) and returns plain
values. Anything that cannot be read from the page is left out so the caller asks the owner instead.
"""

from __future__ import annotations

from datetime import datetime
import re
from urllib.parse import quote, urljoin, urlsplit

from seo_agents.onpage.structured_data.schema_rules import THIRD_PARTY_REVIEWS, PLACEHOLDER, norm, plain_text

DECORATIVE = re.compile(r'(?i)(?:\bbg\b|bg[-_]|background|pattern|wave|divider|spacer|placeholder|blur|gradient|'
                        r'shape|decor|ornament|texture|overlay|pixel|tracking|1x1)')
LOGOISH = re.compile(r'(?i)(?:logo|favicon|icon|sprite|badge|avatar-placeholder)')
SYMBOLS = {'US$': 'USD', '€': 'EUR', '£': 'GBP', '₹': 'INR',
           'A$': 'AUD', 'C$': 'CAD', 'CA$': 'CAD', 'AU$': 'AUD', 'S$': 'SGD', 'R$': 'BRL', 'NZ$': 'NZD', 'HK$': 'HKD'}
CODES = r'USD|EUR|GBP|INR|JPY|AUD|CAD|SGD|BRL|NZD|HKD|CHF|SEK|NOK|DKK|AED|ZAR|MXN|PLN'
MONEY = re.compile(r'(?:(?<![A-Za-z])(?P<sym>(?-i:US\$|CA\$|AU\$|NZ\$|HK\$|[ACSR]\$|Rs\.?)|\$|€|£|₹|¥)\s?(?P<amt>\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)'
                   rf'|(?P<amt2>\d{{1,3}}(?:,\d{{3}})*(?:\.\d{{1,2}})?|\d+(?:\.\d{{1,2}})?)\s?(?P<code>(?-i:{CODES}))\b'
                   rf'|\b(?P<code2>(?-i:{CODES}))\s?(?P<amt3>\d{{1,3}}(?:,\d{{3}})*(?:\.\d{{1,2}})?|\d+(?:\.\d{{1,2}})?))'
                   r'(?:\s*(?:/|per|a|an)\s*(?P<unit>month|mo|year|yr|annum|user|seat|week|day|hour|license))?', re.I)
DOLLAR_REGIONS = {'us': 'USD', 'au': 'AUD', 'ca': 'CAD', 'nz': 'NZD', 'sg': 'SGD', 'hk': 'HKD', 'mx': 'MXN',
                  'ar': 'ARS', 'cl': 'CLP', 'co': 'COP', 'tw': 'TWD'}


def local_currency(symbol, lang):
    """Ambiguous symbols ($, ¥, Rs) resolved by the page locale; None when the locale does not decide it."""
    lang = (lang or '').lower().replace('_', '-')
    base, _, region = lang.partition('-')
    if symbol == '$':
        return DOLLAR_REGIONS.get(region) if region else ('USD' if base == 'en' else None)
    if symbol == '¥':
        return {'ja': 'JPY', 'zh': 'CNY'}.get(base)
    if symbol in ('Rs', 'Rs.'):
        return 'PKR' if base == 'ur' or region == 'pk' else ('INR' if base == 'hi' or region == 'in' else None)
    return None


UNITS = {'month': 'MON', 'mo': 'MON', 'year': 'ANN', 'yr': 'ANN', 'annum': 'ANN', 'week': 'WEE', 'day': 'DAY',
         'hour': 'HUR'}
FREE_PLAN = re.compile(r'(?i)\bfree\s+(?:plan|tier|forever|edition|version)\b|\bfree\s+for\s+(?:ever|individuals|teams)\b')
RATING = re.compile(r'(?P<value>[0-5](?:\.\d{1,2})?)\s*(?:/\s*5|out of\s*5)(?:\s*stars?)?', re.I)
RATING_COUNT = re.compile(r'(?P<count>\d{1,3}(?:,\d{3})*|\d+)\+?\s*(?:verified\s+)?(?P<kind>reviews|ratings)\b', re.I)
STEP_HEADING = re.compile(r'(?i)\b(how to|how do|steps?|getting started|get started|set ?up|install(?:ation)?|guide|'
                          r'tutorial|walkthrough|process|quick ?start|onboarding)\b')
STEP_PREFIX = re.compile(r'(?i)^\s*(?:step\s*)?(\d{1,2})\s*[.:)\-–]\s*')
FEATURE_HEADING = re.compile(r'(?i)\b(features?|capabilit|what you get|benefits?|why choose|highlights|integrations?|'
                             r'what.s included|includes|key advantages)\b')
FAQ_HEADING = re.compile(r'(?i)\b(faqs?|frequently asked|questions)\b')
DATE_TEXT = re.compile(r'(?i)\b(?P<kind>(?:last\s+)?(?:published|posted|updated|modified|reviewed)(?:\s+on)?|on)?\s*:?\s*'
                       r'(?P<date>\d{4}-\d{2}-\d{2}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}|'
                       r'\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+\d{4})')
NAME = r"[A-Z][a-zA-Z.'-]+(?:\s+[A-Z][a-zA-Z.'-]+){0,3}"
BYLINE = re.compile(rf'^\s*(?:(?i:written\s+)?(?i:by)\s*:?\s+(?P<name>{NAME})|(?i:author)\s*:\s*(?P<name2>{NAME}))\s*(?:$|[,|·•—–-])')
OS_NAMES = (('Windows', r'\bwindows\b'), ('macOS', r'\bmac ?os\b|\bmacos\b|\bos x\b'), ('Linux', r'\blinux\b'),
            ('Android', r'\bandroid\b'), ('iOS', r'\bios\b|\biphone\b|\bipad\b'), ('Web', r'\bweb[- ]based\b|\bin (?:the|your) browser\b|\bbrowser[- ]based\b'))
CATEGORY_HINTS = (('DeveloperApplication', r'\b(test(?:ing)?|automation|developers?|api|sdk|code|devops|ci/cd|debug\w*|qa|deploy\w*)\b'),
                  ('SecurityApplication', r'\b(security|antivirus|vpn|encryption|compliance)\b'),
                  ('DesignApplication', r'\b(design|prototype|figma|mockups?)\b'),
                  ('FinanceApplication', r'\b(accounting|invoic\w+|payroll|finance|payments?)\b'),
                  ('EducationalApplication', r'\b(learning|courses?|students?|education)\b'),
                  ('CommunicationApplication', r'\b(chat|messaging|email|video calls?)\b'),
                  ('ShoppingApplication', r'\b(shop|store|cart|checkout)\b'))
SOFTWARE_WORDS = re.compile(r'(?i)\b(software|saas|platform|app|application|api|sdk|cloud|automation|tool|plugin|extension|'
                            r'dashboard|no-code|low-code)\b')
WEB_APP = re.compile(r'(?i)\b(saas|cloud|web app|online|browser|no-code|low-code)\b')
YOUTUBE = re.compile(r'(?:youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=|shorts/)|youtu\.be/)([\w-]{11})')
VIMEO = re.compile(r'player\.vimeo\.com/video/(\d+)|vimeo\.com/(\d+)')
SEPARATORS = re.compile(r'\s+[|—–:·-]\s+')


# ------------------------------------------------------------------ urls and text
def absolute(ctx, src):
    if not isinstance(src, str) or not src:
        return None
    src = src.strip()
    if src.startswith('//'):
        src = 'https:' + src
    if not re.match(r'^https?://', src):
        origin = (ctx.get('origin') or '').rstrip('/')
        if not origin:
            return None
        base = ctx.get('absolute_url') or origin + (ctx.get('url') or '/')
        src = urljoin(base, src)
    return quote(src, safe=":/?#[]@!$&'()*+,;=%~")


def page_url(ctx):
    return ctx.get('absolute_url') or absolute(ctx, ctx.get('url') or '/')


def visible_text(ctx, plan=None):
    """Normalized visible text of the page after the proposal lands (current copy + the plan's new copy)."""
    rows = [ctx.get('title'), ctx.get('description'), ctx.get('corpus')] + list(ctx.get('copy') or [])
    rows += [b.get('text') for s in ctx.get('sections') or [] for b in s.get('blocks') or []]
    rows += [s.get('heading') for s in ctx.get('sections') or []]
    plan = plan or {}
    rows += [plan.get(k) for k in ('title', 'description', 'h1', 'direct_answer')]
    rows += [o.get('text') for o in plan.get('outline') or []]
    for s in plan.get('sections') or []:
        rows.append(s.get('heading'))
        for b in s.get('blocks') or []:
            rows.append(b.get('text'))
            rows += list(b.get('items') or [])
    for q in plan.get('faq') or []:
        rows += [q.get('question'), q.get('answer')]
    return ' \n '.join(norm(plain_text(r)) for r in rows if isinstance(r, str) and r)


def clean_label(text):
    return re.sub(r'\s+', ' ', re.sub(r'[.:;,\s]+$', '', plain_text(text or ''))).strip() or None


def current_h1(ctx):
    for h in ctx.get('headings') or []:
        if h.get('level') == 1 and h.get('text'):
            return clean_label(h['text'])
    return None


def title_head(title):
    """'Pricing — Acme' -> 'Pricing' (first title segment)."""
    parts = [p.strip() for p in SEPARATORS.split(title or '') if p.strip()]
    return parts[0] if parts else None


# ------------------------------------------------------------------ media
def _pixels(image):
    natural = image.get('natural') or [image.get('width'), image.get('height')]
    try:
        return int(natural[0]) * int(natural[1]), int(natural[0]), int(natural[1])
    except (TypeError, ValueError, IndexError):
        return 0, None, None


def page_images(ctx, *, min_pixels=50_000):
    """Content images (not logos/icons), largest first; each {url, width, height, alt}. Google: >= 50k pixels."""
    out, seen = [], set()
    rows = [i for i in ctx.get('images') or [] if isinstance(i, dict) and isinstance(i.get('src'), str)]
    repeats = {}
    for image in rows:
        repeats[image['src']] = repeats.get(image['src'], 0) + 1
    for image in rows:
        src = image.get('src')
        if repeats[src] >= 3:
            continue  # the same image 3+ times on a page is an icon/UI element
        url = absolute(ctx, src)
        if not url or url in seen or LOGOISH.search(src) or re.search(r'(?i)\.svg(\?|$)|^data:', src):
            continue
        alt = image.get('alt')
        if (isinstance(alt, str) and not alt.strip()) or DECORATIVE.search(src.split('?')[0] + ' ' + (alt or '')):
            continue  # alt="" marks a decorative image; backgrounds/patterns are not the page's image
        pixels, width, height = _pixels(image)
        if pixels and pixels < min_pixels:
            continue
        if not pixels and len((alt or '').split()) < 2:
            continue  # unknown size: only a descriptively labelled image can be the page image
        seen.add(url)
        out.append({'url': url, 'width': width, 'height': height, 'alt': clean_label(image.get('alt')),
                    'pixels': pixels, 'above_fold': bool(image.get('above_fold'))})
    return sorted(out, key=lambda i: (not i['above_fold'], -i['pixels']))


def logo(ctx):
    for asset in ctx.get('assets') or []:
        src = asset.get('src') or ''
        if re.search(r'(?i)logo', src) and asset.get('type', 'image') == 'image':
            url = absolute(ctx, src)
            if url:
                _, width, height = _pixels(asset)
                return {'url': url, 'width': width, 'height': height}
    return None


def videos(ctx):
    """Videos on the page: {name, contentUrl|embedUrl, thumbnailUrl?}. Upload dates are never on the page."""
    out, seen = [], set()
    candidates = [(a.get('src'), a.get('alt')) for a in ctx.get('assets') or [] if a.get('type') == 'video' and a.get('on_page')]
    candidates += [(l.get('href'), l.get('accessible_name')) for l in ctx.get('links') or []
                   if isinstance(l.get('href'), str) and (YOUTUBE.search(l['href']) and '/embed/' in l['href'])]
    for src, title in candidates:
        url = absolute(ctx, src)
        if not url or url in seen:
            continue
        seen.add(url)
        row = {'name': clean_label(title)}
        yt, vimeo = YOUTUBE.search(url), VIMEO.search(url)
        if yt:
            row.update(embedUrl=f'https://www.youtube.com/embed/{yt.group(1)}',
                       thumbnailUrl=f'https://i.ytimg.com/vi/{yt.group(1)}/hqdefault.jpg')
        elif vimeo:
            row['embedUrl'] = f'https://player.vimeo.com/video/{vimeo.group(1) or vimeo.group(2)}'
        else:
            row['contentUrl'] = url
        out.append(row)
    return out


# ------------------------------------------------------------------ people, contact, dates
PROFILE_PATHS = {  # host -> how many path segments a profile URL has (deeper = a repo, post or share link)
    'github.com': 1, 'gitlab.com': 1, 'twitter.com': 1, 'x.com': 1, 'instagram.com': 1, 'facebook.com': 1,
    'medium.com': 1, 'dev.to': 1, 'dribbble.com': 1, 'behance.net': 1, 'threads.net': 1, 'tiktok.com': 1,
    'bsky.app': 2, 'linkedin.com': 2, 'youtube.com': 2, 'crunchbase.com': 2, 'g2.com': 2, 'producthunt.com': 2,
    'wikipedia.org': 2, 'wikidata.org': 2, 'stackoverflow.com': 3,
}


def same_as(urls):
    """Profile/identity URLs only (no repositories, posts, share or intent links), deduplicated."""
    out = []
    for url in urls or []:
        if not isinstance(url, str) or not re.match(r'^https?://', url) or re.search(r'(?i)/(?:intent|share|sharer)\b', url):
            continue
        parts = urlsplit(url)
        host = re.sub(r'^(?:www\.|m\.|[a-z]{2}\.)', '', parts.netloc.lower())
        segments = [s for s in parts.path.split('/') if s]
        depth = next((d for h, d in PROFILE_PATHS.items() if host == h or host.endswith('.' + h)), None)
        if depth is not None and len(segments) > depth:
            continue
        clean = f'{parts.scheme}://{parts.netloc}{parts.path.rstrip("/")}'
        if clean not in out:
            out.append(clean)
    return out


def contact(ctx):
    email = phone = None
    for link in ctx.get('links') or []:
        href = link.get('href') if isinstance(link.get('href'), str) else ''
        if href.lower().startswith('mailto:') and not email:
            email = href[7:].split('?')[0].strip() or None
        elif href.lower().startswith('tel:') and not phone:
            digits = re.sub(r'[^\d+]', '', href[4:])
            phone = digits if len(re.sub(r'\D', '', digits)) >= 7 else None
    return {'email': email, 'telephone': phone}


def _iso(text):
    text = re.sub(r'(?i)\bsept\b', 'sep', text.strip().rstrip('.')).replace(',', '')
    for fmt in ('%Y-%m-%d', '%b %d %Y', '%B %d %Y', '%d %b %Y', '%d %B %Y', '%b. %d %Y'):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def meta_value(ctx, *names):
    names = {n.lower() for n in names}
    for row in ctx.get('meta_tags') or []:
        key = str(row.get('property') or row.get('name') or row.get('itemprop') or '').lower()
        if key in names and isinstance(row.get('content'), str) and row['content'].strip():
            return row['content'].strip()
    return None


def dates(ctx):
    """{'published', 'modified'} ISO dates from article meta tags or a visible dated line; None when absent."""
    published = meta_value(ctx, 'article:published_time', 'datepublished', 'date', 'pubdate', 'publish_date')
    modified = meta_value(ctx, 'article:modified_time', 'og:updated_time', 'datemodified', 'last-modified')
    out = {'published': _meta_date(published), 'modified': _meta_date(modified)}
    for signal in ctx.get('eeat_observed') or []:
        if signal.get('signal') != 'dates' or signal.get('status') != 'observed':
            continue
        for row in signal.get('evidence') or []:
            text = row.get('text') if isinstance(row, dict) else row
            for match in DATE_TEXT.finditer(str(text or '')):
                iso = _iso(match.group('date'))
                if not iso:
                    continue
                kind = (match.group('kind') or '').lower()
                if not kind or kind == 'on':
                    continue  # an unlabeled date ("Offer ends Dec 31") is not a publication date
                key = 'modified' if 'updat' in kind or 'modif' in kind else 'published'
                out[key] = out[key] or iso
    if out['modified'] and out['published'] and out['modified'] < out['published']:
        out['modified'] = None
    return out


def _meta_date(value):
    if not value:
        return None
    match = re.match(r'^\d{4}-\d{2}-\d{2}(?:T[\d:.]+(?:Z|[+-]\d{2}:?\d{2})?)?', value.strip())
    return match.group(0) if match else _iso(value)


def author(ctx):
    """Author name from a visible byline or <meta name=author>; None when absent."""
    for signal in ctx.get('eeat_observed') or []:
        if signal.get('signal') == 'authorship' and signal.get('status') == 'observed':
            for row in signal.get('evidence') or []:
                match = BYLINE.search(str((row.get('text') if isinstance(row, dict) else row) or ''))
                if match:
                    return clean_label(match.group('name') or match.group('name2'))
    name = meta_value(ctx, 'author')
    return clean_label(name) if name and len(name.split()) <= 5 and not re.search(r'https?://|@', name) else None


# ------------------------------------------------------------------ page content: offers, ratings, steps, Q&A, lists
def _blocks(ctx):
    """(heading, level, tag, text) for every visible block, in reading order."""
    for s in ctx.get('sections') or []:
        for b in s.get('blocks') or []:
            if b.get('text'):
                yield s.get('heading'), s.get('level'), b.get('tag'), plain_text(b['text'])


def _amount(text):
    value = text.replace(',', '')
    return value[:-3] if value.endswith('.00') else value


NOT_PLAN = re.compile(r'(?i)\b(faqs?|questions?|answers?|compare|comparison|plans? and features|features? by plan|'
                      r'testimonials?|reviews?|customers?|calculator)\b|\?$')
BADGE = re.compile(r'(?i)\s*(?:recommended|most popular|popular|best value|new|save \d+%)\s*$')
UNIT_NEAR = re.compile(r'(?i)(?:/|per|a|an)\s*(month|mo|year|yr|annum)\b|\b(monthly|annually|yearly)\b')
PER_NEAR = re.compile(r'(?i)(?:/|per)\s*(user|seat|member|license)\b')


def _plan_name(heading):
    name = clean_label(heading)
    return clean_label(BADGE.sub('', name)) if name else None


def offers(ctx):
    """Visible plan prices: [{name, price, currency, unit, per, text}], one per plan section (the heading above the
    price names the plan). Strict: comparison tables, FAQ/question sections, headings that are themselves prices and
    prices with an unresolvable currency are skipped; the first price of a plan wins."""
    out, names = [], set()
    for s in ctx.get('sections') or []:
        heading = clean_label(s.get('heading')) or ''
        texts = [plain_text(b['text']) for b in s.get('blocks') or [] if b.get('text') and b.get('tag') not in ('td', 'th', 'caption')]
        text = ' '.join(texts)
        priced = MONEY.search(heading)
        if priced:  # "Freelancer, $29/mo": the plan name precedes the price, which is read from the heading first
            text = heading[priced.start():] + ' ' + text
            heading = re.sub(r'[\s,:;–—-]+$', '', heading[:priced.start()])
        name = _plan_name(heading)
        if not name or NOT_PLAN.search(name) or name in names:
            continue
        found = None
        for match in MONEY.finditer(text):
            symbol = match.group('sym')
            code = (match.group('code') or match.group('code2') or SYMBOLS.get(symbol or '')
                    or local_currency(symbol, ctx.get('lang')))
            amount = match.group('amt') or match.group('amt2') or match.group('amt3')
            if code and amount:
                after = text[match.end():match.end() + 60]
                unit = (match.group('unit') or '').lower() or None
                if unit not in UNITS:
                    near = UNIT_NEAR.search(after)
                    unit = (near.group(1) or {'monthly': 'month', 'annually': 'year', 'yearly': 'year'}[near.group(2).lower()]).lower() \
                        if near else None
                per = (match.group('unit') or '').lower() if (match.group('unit') or '').lower() in ('user', 'seat', 'license') \
                    else ((PER_NEAR.search(after) or [None, None])[1] or '').lower() or None
                found = {'name': name, 'price': _amount(amount), 'currency': code.upper(), 'unit': UNITS.get(unit),
                         'per': 'member' if per == 'member' else per, 'text': text[:300]}
                break
        if not found and (FREE_PLAN.search(text) or FREE_PLAN.search(name) or re.fullmatch(r'(?i)free', name)) \
                and not MONEY.search(text):
            found = {'name': name, 'price': '0', 'currency': None, 'unit': None, 'per': None, 'text': text[:300]}
        if found:
            names.add(name)
            out.append(found)
    return out


def subscription(rows):
    """Recurring or per-seat pricing: the product is software sold as a service."""
    return any(r.get('unit') or r.get('per') for r in rows or [])


def rating(ctx):
    """First-party aggregate rating visible on the page: {value, count, kind, third_party, text} or None."""
    for heading, _level, _tag, text in _blocks(ctx):
        value, count = RATING.search(text), RATING_COUNT.search(text)
        if value and count:
            return {'value': value.group('value'), 'count': int(count.group('count').replace(',', '')),
                    'kind': 'reviewCount' if count.group('kind').lower() == 'reviews' else 'ratingCount',
                    'third_party': bool(THIRD_PARTY_REVIEWS.search(text + ' ' + (heading or ''))), 'text': text}
    return None


def _list_items(blocks):
    return [t for tag, t in blocks if tag in ('li', 'dd') and t]


def steps(ctx, plan=None, *, how_to=False):
    """A visible how-to: {name, steps: [text]} from an ordered list under a step-like heading (plan sections first,
    since they will be visible once the proposal lands), or numbered "Step N" paragraphs. `how_to`: the whole page is a
    how-to (title says so), so any ordered list counts; otherwise a ranked list is not mistaken for steps."""
    for s in (plan or {}).get('sections') or []:
        if not (how_to or STEP_HEADING.search(s.get('heading') or '')):
            continue
        for b in s.get('blocks') or []:
            items = [plain_text(i) for i in b.get('items') or [] if isinstance(i, str) and i.strip()]
            if b.get('tag') == 'ol' and len(items) >= 2 and not any(PLACEHOLDER.search(i) for i in items):
                return {'name': clean_label(s.get('heading')), 'steps': items}
    for s in ctx.get('sections') or []:
        heading = s.get('heading') or ''
        blocks = [(b.get('tag'), plain_text(b.get('text'))) for b in s.get('blocks') or [] if b.get('text')]
        numbered = [STEP_PREFIX.sub('', t) for _tag, t in blocks if STEP_PREFIX.match(t) and re.match(r'(?i)\s*step', t)]
        items = _list_items(blocks)
        if STEP_HEADING.search(heading) and len(items) >= 2:
            return {'name': clean_label(heading), 'steps': [STEP_PREFIX.sub('', i) for i in items]}
        if len(numbered) >= 2:
            return {'name': clean_label(heading) or None, 'steps': numbered}
    return None


def questions(ctx, plan=None, *, min_answer_words=8):
    """Visible Q&A: the plan FAQ (rendered verbatim) + question-shaped H2/H3 (or dt) followed by an answer."""
    out, seen = [], set()

    def add(question, answer):
        question, answer = plain_text(question), plain_text(answer)
        if not question.endswith('?') or len(answer.split()) < min_answer_words or PLACEHOLDER.search(question + answer):
            return
        if norm(question) not in seen:
            seen.add(norm(question))
            out.append({'question': question, 'answer': answer})

    for q in (plan or {}).get('faq') or []:
        if isinstance(q, dict):
            add(q.get('question') or '', q.get('answer') or '')
    for s in ctx.get('sections') or []:
        blocks = [b for b in s.get('blocks') or [] if b.get('text')]
        if s.get('heading') and (s.get('level') or 2) in (2, 3, 4) and blocks:
            answer = next((b['text'] for b in blocks if b.get('tag') in ('p', 'dd', 'div', 'section', None)), None)
            if answer:
                add(s['heading'], answer)
        for i, b in enumerate(blocks[:-1]):  # dt/dd pairs and accordions (<details><summary>Q?</summary>answer)
            nxt = blocks[i + 1]
            if (b.get('tag') == 'dt' and nxt.get('tag') == 'dd') or \
                    (b.get('tag') in ('summary', 'button') and nxt.get('tag') in ('p', 'div', 'dd', 'li')):
                add(b['text'], nxt['text'])
    return out


def features(ctx, *, limit=20):
    """List items under a features/benefits heading (visible feature list)."""
    out = []
    for s in ctx.get('sections') or []:
        if FEATURE_HEADING.search(s.get('heading') or ''):
            out += [clean_label(b['text']) for b in s.get('blocks') or [] if b.get('tag') == 'li' and b.get('text')]
    return [f for f in dict.fromkeys(out) if f and len(f) <= 160][:limit]


SHOWCASE_HEADING = re.compile(r'(?i)\b(projects?|products?|apps?|portfolio|work|case stud|solutions?|tools?|services?)\b')


NOT_SHOWCASE = re.compile(r'(?i)\b(experience|career|employment|education|skills?|jobs?|hiring|team|testimonials?)\b')


def showcase(ctx, *, limit=30):
    """Items a page features under a Projects/Products-style H2: each H3 below it with its first paragraph and an
    image whose alt/src names it (each image used once). {name: <H2>, items: [{name, description, image}]} or None."""
    sections, items, active, title, used = ctx.get('sections') or [], [], False, None, set()
    for s in sections:
        level, heading = s.get('level') or 0, s.get('heading') or ''
        if level <= 2:
            if items:
                break
            active = level == 2 and bool(SHOWCASE_HEADING.search(heading)) and not NOT_SHOWCASE.search(heading)
            title = clean_label(heading) if active else None
            continue
        if not active or level != 3 or not heading:
            continue
        name = clean_label(heading)
        text = next((plain_text(b['text']) for b in s.get('blocks') or [] if b.get('tag') in ('p', 'div', 'dd') and b.get('text')), None)
        key = norm(re.split(r'\s+[-–—:]\s+', name)[0])
        image = next((i for i in page_images(ctx, min_pixels=0) if i['url'] not in used and key and
                      (key in norm(i.get('alt') or '') or key.replace(' ', '') in norm(i['url']).replace(' ', ''))), None)
        if image:
            used.add(image['url'])
        items.append({'name': name, 'description': text, 'image': image['url'] if image else None})
    return {'name': title, 'items': items[:limit]} if len(items) >= 2 else None


def operating_systems(ctx):
    text = (ctx.get('corpus') or '').lower()
    return [name for name, pattern in OS_NAMES if re.search(pattern, text)]


def app_category(ctx, offering=None):
    """Google applicationCategory from what the product IS (title, H1, researched offering) - not the description,
    which lists features ("enhanced security controls" does not make a security app). Default BusinessApplication."""
    text = ' '.join(filter(None, [ctx.get('title'), offering, current_h1(ctx)])).lower()
    for category, pattern in CATEGORY_HINTS:
        if re.search(pattern, text):
            return category
    return 'BusinessApplication'


def is_software(ctx, offering=None):
    text = ' '.join(filter(None, [ctx.get('title'), ctx.get('description'), offering, current_h1(ctx)] + features(ctx)))
    return bool(SOFTWARE_WORDS.search(text))


def is_web_app(ctx, offering=None):
    text = ' '.join(filter(None, [ctx.get('title'), ctx.get('description'), offering, ctx.get('corpus')]))
    return bool(WEB_APP.search(text))


# ------------------------------------------------------------------ site structure
def _path(url):
    path = urlsplit(url or '/').path or '/'
    return path if path == '/' else path.rstrip('/')


def known_pages(ctx):
    """{path: title} of internal pages that exist (manifest + resolved links), noindex/redirected pages excluded."""
    pages = ctx.get('site_pages') or {}
    out = {}
    for row in ctx.get('site_urls') or []:
        path = _path(row.get('url'))
        info = pages.get(row.get('url')) or pages.get(path) or {}
        if info.get('noindex') or info.get('redirects_to') or (info.get('http_status') or 200) >= 400:
            continue
        out[path] = clean_label(title_head(row.get('title'))) if row.get('title') else None
    return out


def trail(ctx):
    """Breadcrumb ancestors that exist as pages: [(path, name)] from the home page down to (excluding) this page."""
    pages = known_pages(ctx)
    parts = [p for p in _path(ctx.get('url')).split('/') if p]
    out, path = [], ''
    for part in parts[:-1]:
        path += '/' + part
        if path in pages:
            out.append((path, pages[path] or part.replace('-', ' ').replace('_', ' ').title()))
    return out


def children(ctx, *, limit=50):
    """Direct child pages of this (collection) page: [(path, name)]."""
    base = _path(ctx.get('url'))
    prefix = '/' if base == '/' else base + '/'
    rows = [(p, n) for p, n in known_pages(ctx).items()
            if p.startswith(prefix) and p != base and '/' not in p[len(prefix):] and n]
    return sorted(rows)[:limit]
