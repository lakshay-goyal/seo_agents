"""Core Web Vitals causes (LCP/CLS/INP/TTFB): images, fonts, scripts, bundles, caching, heavy client libraries.

`legacy_format_assets` lists only PNG/JPEG/GIF/BMP files that source actually references and that are not
passed through an image pipeline (next/image, astro:assets, gatsby-plugin-image, @nuxt/image, ...), which
converts formats at build/request time.
"""

from collections import Counter
from pathlib import PurePosixPath
import re

from seo_agents.exploration.repo_analysis.tools.page_inventory import LARGE_ASSET_BYTES  # 200 KiB
from seo_agents.exploration.repo_analysis.tools.skill_runner import run_skill

LEGACY_EXT = ('.png', '.jpg', '.jpeg', '.gif', '.bmp')
HEAVY_CLIENT = re.compile(r'^(?:three|@react-three/[\w-]+|@paper-design/shaders(?:-react)?|ogl|pixi\.js|@pixi/[\w-]+|babylonjs|'
                          r'@babylonjs/[\w-]+|@splinetool/[\w-]+|lottie-web|lottie-react|@lottiefiles/[\w-]+|mapbox-gl|maplibre-gl|'
                          r'leaflet|gsap|framer-motion|aos|locomotive-scroll|moment|lodash|jquery|chart\.js|d3|echarts|plotly\.js|'
                          r'firebase|aws-sdk|@mui/material|antd|swiper|video\.js|highlight\.js|prismjs|styled-components|@emotion/react|'
                          r'monaco-editor|@codemirror/[\w-]+|tsparticles|react-tsparticles|matter-js|cannon-es)$')
WEBGL_HINT = re.compile(r'^(?:three|@react-three/|@paper-design/shaders|ogl|pixi|@pixi/|babylonjs|@babylonjs/|@splinetool/|mapbox-gl|maplibre-gl)')


def referenced_images(index, optimizer_files):
    """Legacy-format image assets referenced from source by path or file name, outside image pipelines."""
    sources = {p: index.files[p] for p in index.paths(exclude_docs=True)}
    rows = []
    for asset, meta in sorted(index.assets.items()):
        if not asset.lower().endswith(LEGACY_EXT):
            continue
        public = re.sub(r'^(?:public|static|assets|src/assets)/', '', asset)
        needles = {'/' + public, public, PurePosixPath(asset).name}
        users = sorted(p for p, text in sources.items() if any(n in text for n in needles))
        if not users:
            continue
        if users and all(u in optimizer_files for u in users):
            continue
        rows.append({'path': asset, 'size_bytes': meta.get('size_bytes'), 'referenced_by': users[:5]})
    return rows


def analyze_performance_assets(index, onpage=None, project=None):
    result = run_skill(index, 'performance-assets')
    images = [m for m in (onpage or {}).get('media', []) if m.get('issues') is not None]
    issue_counts = Counter(issue.split(' ')[0] for m in images for issue in m['issues'])
    result['images'] = {'total': len(images), 'issue_counts': dict(issue_counts),
                        'records': [m['id'] for m in images if m['issues']]}
    result['large_assets'] = sorted(({'path': p, **meta} for p, meta in index.assets.items()
                                     if meta.get('size_bytes', 0) > LARGE_ASSET_BYTES),
                                    key=lambda a: -a['size_bytes'])
    optimizer_files = set(result['signals'].get('image-optimizer', {}).get('files', []))
    result['legacy_format_assets'] = referenced_images(index, optimizer_files)
    deps = [d for d in (project or {}).get('dependencies', []) if isinstance(d, dict) and not d.get('dev')]
    heavy = sorted({d['name'] for d in deps if HEAVY_CLIENT.match(d['name'].lower())} |
                   set((project or {}).get('heavy_client_dependencies', [])))
    result['heavy_client_dependencies'] = [{'name': name, **({'kind': 'webgl'} if WEBGL_HINT.match(name.lower()) else {})}
                                           for name in heavy]
    vitals = Counter(tag for s in result['signals'].values() for r in s['records'] for tag in r.get('tags', []))
    result['signals_by_vital'] = dict(vitals)
    return result
