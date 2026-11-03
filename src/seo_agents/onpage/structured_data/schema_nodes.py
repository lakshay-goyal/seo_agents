"""schema.org node builders shared by the structured-data check and the article renderer.

Conventions: absolute https URLs; `@id` = `<origin>/#organization|#person|#website|#logo`, `<page>#webpage|#breadcrumb|
#article|#product|#faq|#howto|#itemlist|#primaryimage|#video-N`; one entity per @id; nodes reference each other by
`{'@id': ...}` and every reference resolves inside the same @graph.
"""

from __future__ import annotations

from seo_agents.onpage.structured_data.schema_rules import HEADLINE_MAX, plain_text


def ref(node_id):
    return {'@id': node_id}


def compact(node):
    """Drop None / empty values (never ship `"image": null`)."""
    return {k: v for k, v in node.items() if v not in (None, '', [], {})}


def ids(origin, url, entity_type='Organization'):
    origin = (origin or '').rstrip('/')
    return {'entity': f'{origin}/#{entity_type.lower()}', 'website': f'{origin}/#website', 'logo': f'{origin}/#logo',
            'webpage': f'{url}#webpage', 'breadcrumb': f'{url}#breadcrumb', 'article': f'{url}#article',
            'product': f'{url}#product', 'faq': f'{url}#faq', 'howto': f'{url}#howto', 'itemlist': f'{url}#itemlist',
            'image': f'{url}#primaryimage'}


def image_object(node_id, image, *, caption=None):
    return compact({'@type': 'ImageObject', '@id': node_id, 'url': image['url'], 'contentUrl': image['url'],
                    'width': image.get('width'), 'height': image.get('height'), 'caption': caption or image.get('alt')})


def entity(entity_type, node_id, *, name, url, same_as=None, logo_id=None, image=None, email=None, telephone=None,
           job_title=None, description=None):
    node = {'@type': entity_type, '@id': node_id, 'name': name, 'url': url, 'sameAs': list(dict.fromkeys(same_as or [])),
            'email': email, 'telephone': telephone, 'description': description}
    if entity_type == 'Organization':
        node['logo'] = ref(logo_id) if logo_id else None
        node['image'] = ref(logo_id) if logo_id else None
    else:
        node['jobTitle'] = job_title
        node['image'] = image
    return compact(node)


def website(node_id, *, origin, name, publisher_id, lang):
    return compact({'@type': 'WebSite', '@id': node_id, 'url': origin.rstrip('/') + '/', 'name': name,
                    'publisher': ref(publisher_id) if publisher_id else None, 'inLanguage': lang})


def web_page(page_type, node_id, *, url, name, description, website_id, lang, breadcrumb_id=None, image_id=None,
             about_id=None, main_entity=None, published=None, modified=None):
    node = {'@type': page_type, '@id': node_id, 'url': url, 'name': name, 'description': description,
            'isPartOf': ref(website_id), 'inLanguage': lang,
            'breadcrumb': ref(breadcrumb_id) if breadcrumb_id else None,
            'primaryImageOfPage': ref(image_id) if image_id else None, 'image': ref(image_id) if image_id else None,
            'datePublished': published, 'dateModified': modified}
    if page_type == 'ProfilePage':
        node['mainEntity'] = ref(about_id)
    else:
        node['about'] = ref(about_id) if about_id else None
        node['mainEntity'] = main_entity
    return compact(node)


def breadcrumbs(node_id, crumbs):
    """`crumbs`: [(name, absolute url)] from Home to this page."""
    return {'@type': 'BreadcrumbList', '@id': node_id, 'itemListElement': [
        compact({'@type': 'ListItem', 'position': n, 'name': plain_text(name), 'item': url})
        for n, (name, url) in enumerate(crumbs, 1)]}


def headline(text):
    """Headline <= 110 chars, cut at a word boundary (never mid-word, no ellipsis)."""
    text = plain_text(text)
    if len(text) <= HEADLINE_MAX:
        return text
    return text[:HEADLINE_MAX].rsplit(' ', 1)[0].rstrip(' ,;:-—–')


def article(article_type, node_id, *, url, webpage_id, title, description, author, publisher_id, lang, image_ids=(),
            published=None, modified=None, word_count=None, section=None, keywords=None):
    return compact({'@type': article_type, '@id': node_id, 'headline': headline(title), 'description': description,
                    'url': url, 'mainEntityOfPage': ref(webpage_id), 'isPartOf': ref(webpage_id),
                    'author': author, 'publisher': ref(publisher_id) if publisher_id else None,
                    'image': [ref(i) for i in image_ids] or None, 'inLanguage': lang,
                    'datePublished': published, 'dateModified': modified or published,
                    'wordCount': word_count if isinstance(word_count, int) and word_count > 0 else None,
                    'articleSection': section, 'keywords': keywords})


def faq_page(node_id, *, url, questions, website_id, lang):
    return {'@type': 'FAQPage', '@id': node_id, 'url': url, 'isPartOf': ref(website_id), 'inLanguage': lang,
            'mainEntity': [{'@type': 'Question', 'name': plain_text(q['question']),
                            'acceptedAnswer': {'@type': 'Answer', 'text': plain_text(q['answer'])}} for q in questions]}


def how_to(node_id, *, name, url, steps, lang, image_id=None):
    return compact({'@type': 'HowTo', '@id': node_id, 'name': plain_text(name), 'url': url, 'inLanguage': lang,
                    'image': ref(image_id) if image_id else None,
                    'step': [{'@type': 'HowToStep', 'position': n, 'text': plain_text(text)}
                             for n, text in enumerate(steps, 1)]})


def item_list(node_id, *, name, items):
    """`items`: [(name, absolute url)]."""
    return {'@type': 'ItemList', '@id': node_id, 'name': plain_text(name), 'numberOfItems': len(items),
            'itemListElement': [{'@type': 'ListItem', 'position': n, 'name': plain_text(label), 'url': url}
                                for n, (label, url) in enumerate(items, 1)]}


def showcase_list(node_id, *, name, items, item_type='CreativeWork', creator_id=None):
    """ItemList of the things a page features (projects, products), each described inline."""
    return {'@type': 'ItemList', '@id': node_id, 'name': plain_text(name), 'numberOfItems': len(items),
            'itemListElement': [{'@type': 'ListItem', 'position': n, 'item': compact({
                '@type': item_type, 'name': plain_text(i['name']), 'description': i.get('description'),
                'image': i.get('image'), 'creator' if item_type == 'CreativeWork' else 'brand':
                    ref(creator_id) if creator_id else None})} for n, i in enumerate(items, 1)]}


def offer(row, *, url, currency=None):
    currency = row.get('currency') or currency
    node = {'@type': 'Offer', 'name': row.get('name'), 'price': row['price'], 'priceCurrency': currency, 'url': url}
    if row.get('unit') or row.get('per'):
        node['priceSpecification'] = compact({
            '@type': 'UnitPriceSpecification', 'price': row['price'], 'priceCurrency': currency,
            'billingDuration': 1 if row.get('unit') else None, 'unitCode': row.get('unit'),
            'referenceQuantity': {'@type': 'QuantitativeValue', 'value': 1, 'unitText': row['per']} if row.get('per') else None})
    return compact(node)


def offers_of(rows, *, url):
    """One Offer per visible plan/price; free plans inherit the page currency."""
    currency = next((r['currency'] for r in rows if r.get('currency')), None)
    built = [offer(r, url=url, currency=currency) for r in rows]
    return built[0] if len(built) == 1 else built


def aggregate_rating(row):
    return {'@type': 'AggregateRating', 'ratingValue': row['value'], 'bestRating': 5, 'worstRating': 1,
            row['kind']: row['count']}


def product(product_type, node_id, *, name, url, description, webpage_id, brand_id, image_ids=(), offers=None,
            rating=None, features=None, category=None, operating_systems=None, lang=None):
    node = {'@type': product_type, '@id': node_id, 'name': plain_text(name), 'url': url, 'description': description,
            'mainEntityOfPage': ref(webpage_id), 'image': [ref(i) for i in image_ids] or None, 'offers': offers,
            'aggregateRating': aggregate_rating(rating) if rating else None}
    if product_type == 'Product':
        node['brand'] = ref(brand_id)
    else:
        node.update({'publisher': ref(brand_id), 'applicationCategory': category, 'featureList': features or None,
                     'operatingSystem': ', '.join(operating_systems) if operating_systems else None, 'inLanguage': lang})
    return compact(node)


def video(node_id, row, *, description=None):
    return compact({'@type': 'VideoObject', '@id': node_id, 'name': row.get('name'), 'description': description,
                    'thumbnailUrl': row.get('thumbnailUrl'), 'contentUrl': row.get('contentUrl'),
                    'embedUrl': row.get('embedUrl'), 'uploadDate': row.get('uploadDate')})
