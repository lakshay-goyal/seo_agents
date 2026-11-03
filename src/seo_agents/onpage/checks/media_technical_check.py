"""Check 6 (deterministic): image width/height, lazy-loading above the fold, weight/format/overserving."""

from __future__ import annotations

from seo_agents.onpage.changes import change_operations as ops
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


def check_media_technical(ctx, prior=None, writer=None):
    changes, heavy = [], []
    done = set()
    for image in ctx['images']:
        if not image.get('src'):
            continue
        natural, rendered = image.get('natural') or [], image.get('rendered') or []
        target = image_target(image)
        key = (target or {}).get('key') or image['src']
        if (not image.get('width') or not image.get('height')) and len(natural) == 2 and (key, 'dims') not in done:
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
        ratio_1x = round(natural[0] / rendered[0], 2) if len(natural) == 2 and len(rendered) == 2 and rendered[0] else None
        ratio_2x = round(ratio_1x / 2, 2) if ratio_1x else None
        fmt = (image.get('format') or '').lower()
        if (size and size > 200_000) or (ratio_2x and ratio_2x > 1.5) or (fmt in ('png', 'jpg', 'jpeg', 'gif') and not image.get('srcset')):
            heavy.append({'src': image['src'], 'bytes': size, 'format': fmt or None, 'natural': natural or None, 'rendered': rendered or None,
                          'overserve_1x': ratio_1x, 'overserve_2x_dpr': ratio_2x,
                          'suggest': {'max_width': int(rendered[0] * 2) if rendered else None, 'format': 'webp/avif',
                                      'srcset': not image.get('srcset')}})
    advice = []
    if heavy:
        advice.append(ops.advice(ctx, 'media_technical', 'images.weight',
                                 f'{len(heavy)} image(s) are heavier than displayed: resize to ~2x the rendered width, serve WebP/AVIF '
                                 'and add srcset/sizes (ratios measured at 1x and 2x DPR).', impact='medium', evidence=heavy))
    return result(data={'heavy_images': heavy}, changes=changes, advice=advice)


class MediaTechnicalCheck(OnpageCheck):
    """Check: Image width/height, lazy-loading above the fold, weight/format/overserving (deterministic)."""

    name = 'media_technical'
    skill = 'media-technical'
    uses_model = False
    depends_on = ()

    def run(self, ctx, prior, writer):
        return check_media_technical(ctx, prior, writer)
