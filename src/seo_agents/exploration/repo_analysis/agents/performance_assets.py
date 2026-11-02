"""Core Web Vitals causes (LCP/CLS/INP/TTFB): images, fonts, scripts, bundles, caching, measurement."""

from collections import Counter

from seo_agents.exploration.repo_analysis.tools.page_inventory import LARGE_ASSET_BYTES  # 200 KiB
from seo_agents.exploration.repo_analysis.tools.skill_runner import run_skill


def analyze_performance_assets(index, onpage=None, project=None):
    result = run_skill(index, 'performance-assets')
    images = [m for m in (onpage or {}).get('media', []) if m.get('issues') is not None]
    issue_counts = Counter(issue.split(' ')[0] for m in images for issue in m['issues'])
    result['images'] = {'total': len(images), 'issue_counts': dict(issue_counts),
                        'records': [m['id'] for m in images if m['issues']]}
    result['large_assets'] = sorted(({'path': p, **meta} for p, meta in index.assets.items()
                                     if meta.get('size_bytes', 0) > LARGE_ASSET_BYTES),
                                    key=lambda a: -a['size_bytes'])
    result['legacy_format_assets'] = sorted(p for p in index.assets if p.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')))
    result['heavy_client_dependencies'] = (project or {}).get('heavy_client_dependencies', [])
    vitals = Counter(tag for s in result['signals'].values() for r in s['records'] for tag in r.get('tags', []))
    result['signals_by_vital'] = dict(vitals)
    return result
