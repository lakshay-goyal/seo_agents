"""Strict schema.org / Google structured-data rules for a JSON-LD @graph.

Severity:
- `error`: the markup is wrong (bad @id/ref, bad URL/date/price/currency/enum, HTML or placeholders in text, content
  that is not visible on the page, self-serving or third-party ratings). Optional nodes with errors are pruned.
- `needs_input`: a property Google requires for the rich result is missing and only the owner can supply it
  (e.g. `Offer.price`, `VideoObject.uploadDate`). The node still describes the page; the owner is asked.
- `warning`: a recommended property is missing or the feature no longer earns a rich result (FAQ, HowTo).

Sources: developers.google.com/search/docs/appearance/structured-data/* (sd-policies, article, breadcrumb, product-snippet,
review-snippet, software-app, video, organization, site-names) as of 2026-11.
"""

from __future__ import annotations

import re

CONTEXT = 'https://schema.org'
PAGE_TYPES = {'WebPage', 'ItemPage', 'AboutPage', 'ContactPage', 'CollectionPage', 'ProfilePage', 'FAQPage', 'QAPage',
              'SearchResultsPage', 'CheckoutPage', 'MedicalWebPage', 'RealEstateListing'}
ARTICLE_TYPES = {'Article', 'BlogPosting', 'NewsArticle', 'TechArticle', 'Report', 'ScholarlyArticle'}
SOFTWARE_TYPES = {'SoftwareApplication', 'WebApplication', 'MobileApplication', 'VideoGame'}
REVIEWABLE = {'Book', 'Course', 'CreativeWorkSeason', 'CreativeWorkSeries', 'Episode', 'Event', 'Game', 'HowTo',
              'LocalBusiness', 'MediaObject', 'Movie', 'MusicPlaylist', 'MusicRecording', 'Organization', 'Product',
              'Recipe'} | SOFTWARE_TYPES
APP_CATEGORIES = {'GameApplication', 'SocialNetworkingApplication', 'TravelApplication', 'ShoppingApplication',
                  'SportsApplication', 'LifestyleApplication', 'BusinessApplication', 'DesignApplication',
                  'DeveloperApplication', 'DriverApplication', 'EducationalApplication', 'HealthApplication',
                  'FinanceApplication', 'SecurityApplication', 'BrowserApplication', 'CommunicationApplication',
                  'DesktopEnhancementApplication', 'EntertainmentApplication', 'MultimediaApplication', 'HomeApplication',
                  'UtilitiesApplication', 'ReferenceApplication'}
AVAILABILITY = {'https://schema.org/' + a for a in ('BackOrder', 'Discontinued', 'InStock', 'InStoreOnly',
                                                    'LimitedAvailability', 'OnlineOnly', 'OutOfStock', 'PreOrder',
                                                    'PreSale', 'SoldOut')}
CURRENCIES = {'USD', 'EUR', 'GBP', 'INR', 'JPY', 'CNY', 'AUD', 'CAD', 'CHF', 'SEK', 'NOK', 'DKK', 'PLN', 'BRL', 'MXN',
              'SGD', 'HKD', 'NZD', 'ZAR', 'AED', 'SAR', 'KRW', 'TRY', 'ILS', 'RUB', 'IDR', 'THB', 'MYR', 'PHP', 'VND',
              'CZK', 'HUF', 'RON', 'NGN', 'EGP', 'PKR', 'BDT', 'ARS', 'CLP', 'COP', 'PEN', 'TWD', 'UAH', 'KES'}

KNOWN_BY_LOWER = {t.lower(): t for t in PAGE_TYPES | ARTICLE_TYPES | REVIEWABLE | {
    'WebSite', 'Person', 'BreadcrumbList', 'ListItem', 'ItemList', 'Question', 'Answer', 'HowToStep', 'HowToSection',
    'HowToSupply', 'HowToTool', 'Offer', 'AggregateOffer', 'AggregateRating', 'Rating', 'Review', 'Brand', 'ImageObject',
    'VideoObject', 'ContactPoint', 'PostalAddress', 'Place', 'UnitPriceSpecification', 'QuantitativeValue', 'Thing'}}

URL_PROPS = {'url', 'item', 'contentUrl', 'thumbnailUrl', 'embedUrl', 'sameAs', 'logo', 'image', 'mainEntityOfPage',
             'primaryImageOfPage', 'acquireLicensePage', 'license'}
DATE_PROPS = {'datePublished', 'dateModified', 'dateCreated', 'uploadDate', 'priceValidUntil', 'validFrom', 'startDate',
              'endDate'}
TEXT_PROPS = {'name', 'headline', 'description', 'text', 'caption', 'alternativeHeadline', 'jobTitle', 'articleSection'}
HEADLINE_MAX = 110
ISO_DATE = re.compile(r'^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])(T([01]\d|2[0-3]):[0-5]\d(:[0-5]\d(\.\d+)?)?'
                      r'(Z|[+-]([01]\d|2[0-3]):?[0-5]\d)?)?$')
ABS_URL = re.compile(r'^https?://[^\s/?#]+\.[^\s/?#]+[^\s]*$|^https?://(?:localhost|127\.0\.0\.1)(?::\d+)?(?:/[^\s]*)?$')
PRICE = re.compile(r'^\d+(\.\d{1,4})?$')
HTML_TAG = re.compile(r'</?[a-zA-Z][^>]*>')
PLACEHOLDER = re.compile(r'\[(?:VERIFY|TODO|TBD|S\d+)\b[^\]]*\]|\{\{|\}\}|lorem ipsum', re.I)
THIRD_PARTY_REVIEWS = re.compile(r'(?i)\b(g2|capterra|trustpilot|gartner|trustradius|getapp|software ?advice|'
                                 r'google reviews?|yelp|app store|play store|product ?hunt|clutch)\b')


def types_of(node):
    t = node.get('@type') if isinstance(node, dict) else None
    return set(t) if isinstance(t, list) else ({t} if isinstance(t, str) else set())


def is_ref(value):
    return isinstance(value, dict) and set(value) == {'@id'}


def norm(text):
    return re.sub(r'\s+', ' ', re.sub(r'[^\w%$.+ ]+', ' ', HTML_TAG.sub(' ', str(text or '')).lower())).strip()


def plain_text(text):
    """Text safe for JSON-LD: no HTML tags, no [S#]/[VERIFY] markers, single spaces."""
    text = HTML_TAG.sub('', str(text or ''))
    text = re.sub(r'\s*\[(?:S\d+(?:\s*,\s*S\d+)*|\d+)\]', '', text)
    return re.sub(r'\s+', ' ', text).strip()


class Issues:
    """Issue rows; a nested node's issue is attributed to the top-level node that owns it (so prune drops that)."""

    def __init__(self):
        self.rows = []
        self.owner = None

    def add(self, level, node, prop, message):
        self.rows.append({'level': level, 'id': self.owner or (node or {}).get('@id'), 'type': sorted(types_of(node or {})),
                          'prop': prop, 'message': message})


def _values(value):
    return value if isinstance(value, list) else [value]


def _check_url(issues, node, prop, value):
    for item in _values(value):
        if isinstance(item, dict):
            continue  # nested ImageObject / ref - checked on its own
        if not isinstance(item, str) or not ABS_URL.match(item):
            issues.add('error', node, prop, f'must be an absolute http(s) URL, got {str(item)[:80]!r}')


def _check_text(issues, node, prop, value, visible):
    for item in _values(value):
        if not isinstance(item, str):
            continue
        if not item.strip():
            issues.add('error', node, prop, 'empty text')
        elif HTML_TAG.search(item):
            issues.add('error', node, prop, 'contains HTML markup')
        elif PLACEHOLDER.search(item):
            issues.add('error', node, prop, f'contains a placeholder or citation marker: {item[:60]!r}')


def _visible(visible, text):
    return visible is None or not text or norm(text) in visible


def _check_rating(issues, node, rating, visible):
    if is_ref(rating) or not isinstance(rating, dict):
        return
    best = rating.get('bestRating', 5)
    worst = rating.get('worstRating', 1)
    try:
        value, best, worst = float(rating.get('ratingValue')), float(best), float(worst)
    except (TypeError, ValueError):
        issues.add('error', rating, 'ratingValue', 'ratingValue/bestRating/worstRating must be numbers')
        return
    if not worst <= value <= best:
        issues.add('error', rating, 'ratingValue', f'{value} outside {worst}-{best}')
    if 'AggregateRating' in types_of(rating):
        count = rating.get('ratingCount', rating.get('reviewCount'))
        if count is None:
            issues.add('error', rating, 'ratingCount', 'AggregateRating needs ratingCount or reviewCount')
        elif not str(count).isdigit() or int(count) < 1:
            issues.add('error', rating, 'ratingCount', 'count must be a positive integer')


def _check_offer(issues, node, offer):
    if is_ref(offer) or not isinstance(offer, dict):
        return
    kinds = types_of(offer)
    if 'AggregateOffer' in kinds:
        if offer.get('lowPrice') is None:
            issues.add('error', offer, 'lowPrice', 'AggregateOffer needs lowPrice')
        prices = [offer.get('lowPrice'), offer.get('highPrice')]
    else:
        spec = offer.get('priceSpecification') or {}
        prices = [offer.get('price', spec.get('price') if isinstance(spec, dict) else None)]
        if prices[0] is None:
            issues.add('needs_input', offer, 'price', 'Offer needs a price (0 when free)')
    for price in prices:
        if price is not None and not PRICE.match(str(price)):
            issues.add('error', offer, 'price', f'price must be a plain number with a dot decimal, got {price!r}')
    currency = offer.get('priceCurrency')
    if currency is None and any(p is not None and str(p) not in ('0', '0.0', '0.00') for p in prices):
        issues.add('error', offer, 'priceCurrency', 'a non-zero price needs priceCurrency')
    elif currency is not None and currency not in CURRENCIES:
        issues.add('error', offer, 'priceCurrency', f'not an ISO 4217 code: {currency!r}')
    if offer.get('availability') is not None and offer['availability'] not in AVAILABILITY:
        issues.add('error', offer, 'availability', f'unknown availability {offer["availability"]!r}')


def _check_node(issues, node, ids, visible, top=True):
    kinds = types_of(node)
    if not kinds:
        issues.add('error', node, '@type', 'node without @type')
        return
    for kind in kinds:
        proper = KNOWN_BY_LOWER.get(kind.lower())
        if proper and proper != kind:
            issues.add('error', node, '@type', f'unknown type {kind!r}: schema.org types are case-sensitive, use {proper!r}')
    for prop, value in node.items():
        if prop in URL_PROPS:
            _check_url(issues, node, prop, value)
        elif prop in DATE_PROPS:
            for item in _values(value):
                if not isinstance(item, str) or not ISO_DATE.match(item):
                    issues.add('error', node, prop, f'must be ISO 8601, got {item!r}')
        elif prop in TEXT_PROPS:
            _check_text(issues, node, prop, value, visible)
        for item in _values(value):
            if is_ref(item) and item['@id'] not in ids:
                issues.add('error', node, prop, f'reference {item["@id"]} does not resolve in the @graph')
            elif isinstance(item, dict) and '@type' in item and prop != '@context':
                _check_node(issues, item, ids, visible, top=False)

    if not top and set(node) <= {'@type', '@id', 'name', 'url'}:
        return  # a nested stub ({"@type": "WebPage", "@id": ...}) identifies an entity; it is not a full description

    def need(prop, level='error', message=None):
        if node.get(prop) in (None, '', []):
            issues.add(level, node, prop, message or f'{"/".join(sorted(kinds))} requires {prop}')

    if kinds & PAGE_TYPES - {'FAQPage', 'QAPage'}:
        need('url'), need('name')
        need('isPartOf', 'warning')
    if 'WebSite' in kinds:
        need('url'), need('name')
    if kinds & {'Organization', 'Person'}:
        need('name')
        need('url', 'warning')
        if 'Organization' in kinds:
            need('logo', 'needs_input', 'Organization.logo (>=112x112, crawlable) is recommended for the knowledge panel')
    if 'BreadcrumbList' in kinds:
        items = node.get('itemListElement') or []
        if len(items) < 2:
            issues.add('error', node, 'itemListElement', 'BreadcrumbList needs at least 2 ListItem entries')
        for n, item in enumerate(items, 1):
            if item.get('position') != n:
                issues.add('error', node, 'position', f'ListItem positions must run 1..n in order (item {n})')
            if not item.get('name'):
                issues.add('error', node, 'name', f'ListItem {n} needs a name')
            if n < len(items) and not item.get('item'):
                issues.add('error', node, 'item', f'ListItem {n} needs an item URL')
    if kinds & ARTICLE_TYPES:
        need('headline')
        if len(str(node.get('headline') or '')) > HEADLINE_MAX:
            issues.add('error', node, 'headline', f'headline over {HEADLINE_MAX} characters')
        for prop in ('image', 'author', 'datePublished'):
            need(prop, 'needs_input' if prop != 'author' else 'warning')
        for author in _values(node.get('author') or []):
            if isinstance(author, dict) and not is_ref(author) and not types_of(author) & {'Person', 'Organization'}:
                issues.add('error', node, 'author', 'author must be a Person or Organization')
    if 'FAQPage' in kinds:
        questions = node.get('mainEntity') or []
        if not questions:
            issues.add('error', node, 'mainEntity', 'FAQPage needs at least one Question')
        seen = set()
        for q in questions:
            answer = (q.get('acceptedAnswer') or {}).get('text')
            if 'Question' not in types_of(q) or not q.get('name') or not answer:
                issues.add('error', node, 'mainEntity', 'each Question needs name and acceptedAnswer.text')
                continue
            if norm(q['name']) in seen:
                issues.add('error', node, 'mainEntity', f'duplicate question {q["name"][:60]!r}')
            seen.add(norm(q['name']))
            if not (_visible(visible, q['name']) and _visible(visible, answer)):
                issues.add('error', node, 'mainEntity', f'Q&A not visible on the page: {q["name"][:60]!r}')
        issues.add('warning', node, '@type', 'FAQ rich results ended 2026-05; kept for page understanding and answer engines')
    if 'HowTo' in kinds:
        need('name')
        steps = node.get('step') or []
        if len(steps) < 2:
            issues.add('error', node, 'step', 'HowTo needs at least 2 steps')
        for n, step in enumerate(steps, 1):
            if 'HowToStep' not in types_of(step) or not step.get('text'):
                issues.add('error', node, 'step', f'step {n} must be a HowToStep with text')
            elif step.get('position') != n:
                issues.add('error', node, 'step', f'step positions must run 1..n (step {n})')
            elif not _visible(visible, step['text']):
                issues.add('error', node, 'step', f'step {n} text is not visible on the page')
    if kinds & ({'Product'} | SOFTWARE_TYPES):
        need('name')
        offers = node.get('offers')
        for offer in _values(offers or []):
            _check_offer(issues, node, offer)
        if kinds & SOFTWARE_TYPES:
            if not offers:
                issues.add('needs_input', node, 'offers', 'SoftwareApplication rich results need offers.price (0 if free)')
            if not node.get('aggregateRating') and not node.get('review'):
                issues.add('needs_input', node, 'aggregateRating',
                           'SoftwareApplication rich results need first-party aggregateRating or review')
            category = node.get('applicationCategory')
            if category is not None and category not in APP_CATEGORIES:
                issues.add('error', node, 'applicationCategory', f'not a Google application category: {category!r}')
            need('applicationCategory', 'warning')
        elif not (offers or node.get('aggregateRating') or node.get('review')):
            issues.add('needs_input', node, 'offers', 'Product snippets need offers, review or aggregateRating')
        need('image', 'needs_input', f'{"/".join(sorted(kinds))}.image (a product screenshot or photo) is recommended')
        need('description', 'warning')
    if 'AggregateRating' in kinds or 'Rating' in kinds:
        _check_rating(issues, node, node, visible)
    if node.get('aggregateRating') is not None:
        if kinds & {'Organization', 'LocalBusiness'}:
            issues.add('error', node, 'aggregateRating', 'self-serving ratings on Organization/LocalBusiness are not allowed')
        if top and not kinds & REVIEWABLE:
            issues.add('error', node, 'aggregateRating', f'{"/".join(sorted(kinds))} cannot carry aggregateRating')
    if 'VideoObject' in kinds:
        need('name')
        need('thumbnailUrl', 'needs_input', 'VideoObject.thumbnailUrl is required for video results')
        need('uploadDate', 'needs_input', 'VideoObject.uploadDate is required for video results')
        if not (node.get('contentUrl') or node.get('embedUrl')):
            issues.add('error', node, 'contentUrl', 'VideoObject needs contentUrl or embedUrl')
    if 'ImageObject' in kinds and not (node.get('contentUrl') or node.get('url')):
        issues.add('error', node, 'contentUrl', 'ImageObject needs contentUrl or url')
    if 'ItemList' in kinds:
        items = node.get('itemListElement') or []
        if not items:
            issues.add('error', node, 'itemListElement', 'ItemList needs items')
        for n, item in enumerate(items, 1):
            if item.get('position') != n:
                issues.add('error', node, 'position', f'ItemList positions must run 1..n in order (item {n})')
            if not (item.get('url') or item.get('item')):
                issues.add('error', node, 'url', f'ItemList item {n} needs a url or an inline item')
        urls = [i.get('url') or (i['item'].get('url') or i['item'].get('@id') if isinstance(i.get('item'), dict) else i.get('item'))
                for i in items]
        urls = [u for u in urls if isinstance(u, str)]
        if len(set(urls)) != len(urls):
            issues.add('error', node, 'itemListElement', 'ItemList URLs must be unique')
    if 'ContactPoint' in kinds and not (node.get('telephone') or node.get('email')):
        issues.add('error', node, 'telephone', 'ContactPoint needs telephone or email')
    if 'QAPage' in kinds:
        issues.add('error', node, '@type', 'QAPage is only for single-question pages where users submit answers')
    if 'WebSite' in kinds and node.get('potentialAction'):
        issues.add('warning', node, 'potentialAction', 'sitelinks search box retired 2024-11; SearchAction has no effect')


def validate(data, *, visible=None):
    """All issues of a `{'@context', '@graph'}` document. `visible`: normalized visible text (see `norm`) used to
    check that FAQ/HowTo/rating text is on the page; None skips the visibility checks."""
    issues = Issues()
    if not isinstance(data, dict) or data.get('@context') not in (CONTEXT, CONTEXT + '/', 'http://schema.org'):
        issues.add('error', None, '@context', '@context must be https://schema.org')
        return issues.rows
    graph = data.get('@graph')
    if not isinstance(graph, list) or not graph:
        issues.add('error', None, '@graph', '@graph must be a non-empty list')
        return issues.rows
    ids = {}
    for node in graph:
        node_id = node.get('@id') if isinstance(node, dict) else None
        if not node_id:
            issues.add('error', node, '@id', 'every top-level node needs an @id')
            continue
        if not ABS_URL.match(node_id.split('#')[0] or 'x') or '#' not in node_id:
            issues.add('error', node, '@id', f'@id must be an absolute URL with a #fragment, got {node_id!r}')
        if node_id in ids:
            issues.add('error', node, '@id', f'duplicate @id {node_id}')
        ids[node_id] = node
    for node in graph:
        if isinstance(node, dict):
            issues.owner = node.get('@id')
            _check_node(issues, node, ids, visible)
    issues.owner = None
    return issues.rows


def errors_of(rows):
    return [r for r in rows if r['level'] == 'error']


def prune(data, *, visible=None, keep=()):
    """Drop top-level nodes that have errors (except `keep` @ids) and refs to them, until the graph is clean.
    Returns (data, issues, dropped)."""
    dropped = []
    for _ in range(10):
        rows = validate(data, visible=visible)
        bad = {r['id'] for r in errors_of(rows) if r['id'] and r['id'] not in keep}
        bad &= {n.get('@id') for n in data['@graph']}
        if not bad:
            return data, rows, dropped
        dropped += [{'@id': n['@id'], '@type': n.get('@type'),
                     'errors': [r['message'] for r in rows if r['id'] == n['@id'] and r['level'] == 'error']}
                    for n in data['@graph'] if n.get('@id') in bad]
        graph = [strip_refs(n, bad) for n in data['@graph'] if n.get('@id') not in bad]
        data = {**data, '@graph': graph}
    return data, validate(data, visible=visible), dropped


def strip_refs(value, ids):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if is_ref(v) and v['@id'] in ids:
                continue
            if isinstance(v, list):
                v = [strip_refs(i, ids) for i in v if not (is_ref(i) and i['@id'] in ids)]
                if not v:
                    continue
            else:
                v = strip_refs(v, ids)
            out[k] = v
        return out
    return value
