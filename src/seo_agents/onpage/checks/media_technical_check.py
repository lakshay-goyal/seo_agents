"""Check 6 (deterministic): image width/height, lazy-loading above the fold, weight/format/overserving."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlsplit

from seo_agents.onpage.changes import change_operations as ops
from seo_agents.onpage.validation import content_validators as v
from seo_agents.onpage.validation.content_policy import LIMITS
from seo_agents.onpage.checks.check_helpers import result
from seo_agents.onpage.checks.base_check import OnpageCheck

# ====================================================================== 6. image technical (deterministic)
def image_target(image):
    edit = image.get('alt_edit') or {}
    template = edit.get('template') or image.get('template')
    if isinstance(template, dict) and template.get('file'):
        target = ops.target_of({**template, 'kind': 'literal'}, confidence=0.85)
        if target:
            target['scope'] = 'template'
        return target
    src_edit = image.get('edit') or {}
    if src_edit.get('kind') == 'literal':
        return ops.target_of(src_edit, confidence=image.get('confidence') or 0.85)
    return None


BAD_FILENAME = re.compile(r'(?i)^(?:img[_-]?\d+|dsc[_-]?\d+|dscn?\d+|pxl_\d+[\w-]*|image[_-]?\d*|photo[_-]?\d*|untitled[_-]?\d*|'
                          r'screen ?shot[\w -]*|[0-9a-f]{16,})$')
BUILD_HASH = re.compile(r'[-.](?=[A-Za-z0-9_]{8}$)(?=[A-Za-z_]*\d|[a-z0-9_]*[A-Z])[A-Za-z0-9_]{8}$')


LCP_MIN_AREA = 40_000  # px^2 (~200x200 rendered)


def positive_dims(dims):
    return isinstance(dims, (list, tuple)) and len(dims) == 2 and all(
        isinstance(n, (int, float)) and not isinstance(n, bool) and n > 0 for n in dims)


def source_path(src):
    """The image's real path: an image-optimizer URL (Next.js /_next/image?url=...) resolves to its decoded url= param."""
    text = str(src or '')
    try:
        parts = urlsplit(text)
    except ValueError:
        return text.split('?')[0].split('#')[0]
    if parts.path.rstrip('/').endswith('/_next/image') or parts.path.rstrip('/').endswith('/_vercel/image'):
        inner = (parse_qs(parts.query).get('url') or [''])[0]
        if inner:
            return unquote(inner).split('?')[0].split('#')[0]
    return text.split('?')[0].split('#')[0]


def file_stem(src):
    name = source_path(src).rstrip('/').rsplit('/', 1)[-1]
    stem = name.rsplit('.', 1)[0] if '.' in name else name
    return BUILD_HASH.sub('', stem) or stem  # drop a bundler fingerprint (logo-Bx9k2LmQ.svg -> logo)


def lcp_image(ctx, images):
    """The LCP image: matched from performance.mobile.lcp_element, else the largest above-the-fold image."""
    perf = ctx.get('performance') if isinstance(ctx.get('performance'), dict) else {}
    element = ((perf.get('mobile') or {}) if isinstance(perf.get('mobile'), dict) else {}).get('lcp_element')
    if element:
        text = unquote(str(element))
        for image in images:
            src = str(image.get('src') or '')
            candidates = {src, src.split('#')[0], source_path(src), unquote(src)}
            if any(c and len(c) > 1 and c in text for c in candidates):  # full path / decoded url=, never a bare basename
                return image
        return None  # LCP is a known non-image element (text block): no image to prioritise
    fold = [i for i in images if i.get('above_fold')]
    area = lambda i: (i['rendered'][0] * i['rendered'][1]) if positive_dims(i.get('rendered')) else 0  # noqa: E731
    best = max(fold, key=area) if fold else None
    return best if best is not None and area(best) >= LCP_MIN_AREA else None  # never guess a logo/icon as the LCP


def check_media_technical(ctx, prior=None, writer=None):
    changes, heavy = [], []
    done = set()
    for image in ctx['images']:
        if not image.get('src'):
            continue
        natural, rendered = image.get('natural') or [], image.get('rendered') or []
        target = image_target(image)
        key = (target or {}).get('key') or image['src']
        if (not image.get('width') or not image.get('height')) and positive_dims(natural) and (key, 'dims') not in done:
            done.add((key, 'dims'))
            shared = (target or {}).get('scope') == 'template'
            changes.append(ops.make_change(ctx, 'media_technical', 'images.dimensions', None,
                                           {'width': '{item.width}' if shared else natural[0], 'height': '{item.height}' if shared else natural[1],
                                            'intrinsic': natural},
                                           target=dict(target, attr='width,height') if target else None, op='set_attr', confidence=0.85,
                                           rationale='width/height (or aspect-ratio) prevent layout shift (CLS)', extra={'src': image['src']}))
        if image.get('above_fold') and image.get('loading') == 'lazy' and (key, 'lazy') not in done:
            done.add((key, 'lazy'))
            if (target or {}).get('scope') == 'template':
                changes.append(ops.make_change(ctx, 'media_technical', 'images.loading', 'lazy', 'loading={index === 0 ? "eager" : "lazy"}',
                                               target=dict(target, attr='loading'), op='set_attr', confidence=0.75,
                                               rationale='Only the first (above-the-fold, likely LCP) item should load eagerly'))
            else:
                changes.append(ops.make_change(ctx, 'media_technical', 'images.loading', 'lazy', {'loading': 'eager', 'fetchpriority': 'high'},
                                               target=dict(target, attr='loading') if target else None, op='set_attr', confidence=0.8,
                                               rationale='Above-the-fold image is lazy-loaded; this delays LCP'))
        size = image.get('transfer_bytes') or image.get('size_bytes')
        ratio_1x = round(natural[0] / rendered[0], 2) if positive_dims(natural) and positive_dims(rendered) else None
        ratio_2x = round(ratio_1x / 2, 2) if ratio_1x else None
        fmt = (image.get('format') or '').lower()
        if (size and size > 200_000) or (ratio_2x and ratio_2x > 1.5) or (fmt in ('png', 'jpg', 'jpeg', 'gif') and not image.get('srcset')):
            heavy.append({'src': image['src'], 'bytes': size, 'format': fmt or None, 'natural': natural or None, 'rendered': rendered or None,
                          'overserve_1x': ratio_1x, 'overserve_2x_dpr': ratio_2x,
                          'suggest': {'max_width': int(rendered[0] * 2) if rendered else None, 'format': 'webp/avif',
                                      'srcset': not image.get('srcset')}})
    advice = []
    images = [i for i in ctx.get('images') or [] if isinstance(i, dict) and i.get('src')]
    lcp = lcp_image(ctx, images)
    if lcp is not None and str(lcp.get('fetchpriority') or '').lower() != 'high' and lcp.get('loading') != 'lazy':
        target = ops.target_of(lcp.get('edit'), attr='fetchpriority')
        shared = (target or {}).get('scope') in ('template', 'data') or (image_target(lcp) or {}).get('scope') == 'template'
        if shared:  # one template line renders every item: the right expression depends on the component, so advise
            advice.append(ops.advice(ctx, 'media_technical', 'images.fetchpriority',
                                     'The LCP image is rendered from a shared template/list; set fetchpriority="high" only on the '
                                     'first (above-the-fold) item, e.g. via the item index or a priority prop.', impact='medium',
                                     evidence={'src': lcp['src'], 'file': (target or {}).get('file')}))
        else:
            changes.append(ops.make_change(ctx, 'media_technical', 'images.fetchpriority', lcp.get('fetchpriority'), 'high',
                                           target=target, op='set_attr', confidence=0.75,
                                           rationale='The LCP image should be fetched with high priority (fetchpriority="high")',
                                           extra={'src': lcp['src']}))
    alt_max = LIMITS['ALT_CHARS'][1]
    long_alts = sorted({i['alt'].strip()[:160] for i in images if isinstance(i.get('alt'), str) and len(i['alt'].strip()) > alt_max})
    if long_alts:
        advice.append(ops.advice(ctx, 'media_technical', 'images.alt_length',
                                 f'{len(long_alts)} alt text(s) exceed {alt_max} characters; screen readers and image search '
                                 'favour a short, specific description.', impact='low', evidence={'alts': long_alts[:5]}))
    by_alt = {}
    for image in images:
        key = v.normalize(image.get('alt') or '')
        if key:
            by_alt.setdefault(key, {'alt': image['alt'].strip(), 'srcs': set()})['srcs'].add(image['src'])
    repeated = [{'alt': g['alt'], 'images': sorted(g['srcs'])[:5]} for g in by_alt.values() if len(g['srcs']) > 1]
    if repeated:
        advice.append(ops.advice(ctx, 'media_technical', 'images.alt_duplicate',
                                 'Different images share the same alt text; describe each image individually.', impact='low',
                                 evidence=repeated[:8]))
    bad_names = sorted({i['src'] for i in images if not str(i['src']).startswith('data:') and BAD_FILENAME.match(file_stem(i['src']))})
    if bad_names:
        advice.append(ops.advice(ctx, 'media_technical', 'images.filename',
                                 f'{len(bad_names)} image file name(s) are not descriptive (camera/hash names); use short '
                                 'hyphenated names that describe the image (helps image search).', impact='low',
                                 evidence={'srcs': bad_names[:10]}))
    if heavy:
        advice.append(ops.advice(ctx, 'media_technical', 'images.weight',
                                 f'{len(heavy)} image(s) are heavier than displayed: resize to ~2x the rendered width, serve WebP/AVIF '
                                 'and add srcset/sizes (ratios measured at 1x and 2x DPR).', impact='medium', evidence=heavy))
    return result(data={'heavy_images': heavy, 'lcp_image': (lcp or {}).get('src')}, changes=changes, advice=advice)


class MediaTechnicalCheck(OnpageCheck):
    """Check: Image width/height, lazy-loading above the fold, weight/format/overserving (deterministic)."""

    name = 'media_technical'
    skill = 'media-technical'
    uses_model = False
    depends_on = ()

    def run(self, ctx, prior, writer):
        return check_media_technical(ctx, prior, writer)
