"""Bounded, local-only source media measurements (not transfer sizes)."""

import json
import math
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import xml.etree.ElementTree as ET


_READ_LIMIT = 1024 * 1024
_VIDEO_SUFFIXES = {'.mp4', '.m4v', '.mov', '.webm', '.mkv', '.avi', '.mpeg', '.mpg'}


def _positive(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _dimensions(data, suffix):
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        if len(data) >= 33 and data[8:16] == b'\x00\x00\x00\rIHDR':
            return struct.unpack('>II', data[16:24])
    elif data[:6] in (b'GIF87a', b'GIF89a'):
        if len(data) >= 13:
            return struct.unpack('<HH', data[6:10])
    elif data.startswith(b'\xff\xd8'):
        offset = 2
        while offset < len(data):
            if data[offset] != 255:
                break
            while offset < len(data) and data[offset] == 255:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            if marker in (0xD9, 0xDA):
                break
            if marker == 0x01 or 0xD0 <= marker <= 0xD7:
                continue
            if offset + 2 > len(data):
                break
            length = int.from_bytes(data[offset:offset + 2], 'big')
            if length < 2 or offset + length > len(data):
                break
            if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF} and length >= 8:
                height, width = struct.unpack('>HH', data[offset + 3:offset + 7])
                return width, height
            offset += length
    elif data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        offset = 12
        end = min(len(data), int.from_bytes(data[4:8], 'little') + 8)
        while offset + 8 <= end:
            kind = data[offset:offset + 4]
            size = int.from_bytes(data[offset + 4:offset + 8], 'little')
            chunk = data[offset + 8: min(offset + 8 + size, end)]
            if len(chunk) != size:
                break
            if kind == b'VP8X' and size == 10:
                return int.from_bytes(chunk[4:7], 'little') + 1, int.from_bytes(chunk[7:10], 'little') + 1
            if kind == b'VP8 ' and size >= 10 and chunk[3:6] == b'\x9d\x01\x2a':
                width, height = struct.unpack('<HH', chunk[6:10])
                return width & 0x3FFF, height & 0x3FFF
            if kind == b'VP8L' and size >= 5 and chunk[0] == 0x2F:
                bits = int.from_bytes(chunk[1:5], 'little')
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            offset += 8 + size + (size & 1)
    elif suffix == '.svg':
        # Reject DTDs/entities before parsing; never resolve external resources.
        if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
            return None, None
        root = ET.fromstring(data)
        if root.tag not in ('svg', '{http://www.w3.org/2000/svg}svg'):
            return None, None
        def length(value):
            match = re.fullmatch(r'\s*([+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(?:px)?\s*', value or '')
            return _positive(match[1]) if match else None
        width, height = length(root.get('width')), length(root.get('height'))
        view = re.split(r'[\s,]+', root.get('viewBox', '').strip())
        if len(view) == 4:
            values = [float(v) for v in view]
            if all(math.isfinite(v) for v in values):
                width = width or _positive(values[2])
                height = height or _positive(values[3])
        return width, height
    return None, None


def _video(path):
    executable = shutil.which('ffprobe')
    if not executable:
        return None, None, None
    # A format allowlist excludes playlists; protocol restriction blocks URLs.
    result = subprocess.run(
        [executable, '-v', 'error', '-protocol_whitelist', 'file',
         '-format_whitelist', 'mov,matroska,webm,avi,mpeg,mpegvideo',
         '-probesize', str(_READ_LIMIT), '-analyzeduration', '1000000',
         '-select_streams', 'v:0', '-show_entries',
         'stream=width,height,duration:format=duration', '-of', 'json', str(path.resolve())],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        timeout=3, check=False,
    )
    if result.returncode or len(result.stdout) > 65536:
        return None, None, None
    metadata = json.loads(result.stdout)
    stream = next(iter(metadata.get('streams', [])), {})
    return stream.get('width'), stream.get('height'), (
        _positive(stream.get('duration')) or _positive(metadata.get('format', {}).get('duration'))
    )


def probe_asset(path: Path) -> dict:
    """Measure a regular local file; unsupported/invalid measurements stay null.

    Headers are limited to 1 MiB; optional video probing has a 3 second timeout.
    Status is ``measured`` when any media measurement is known, ``unknown``
    otherwise, or ``unavailable`` when the local file cannot be read.
    """
    result = dict(size_bytes=None, intrinsic_width=None, intrinsic_height=None,
                  duration_seconds=None, measurement_status='unavailable')
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            return result
        result['size_bytes'] = info.st_size
        with path.open('rb') as source:
            data = source.read(_READ_LIMIT)
        result['measurement_status'] = 'unknown'
        width, height = _dimensions(data, path.suffix.lower())
        duration = None
        if path.suffix.lower() in _VIDEO_SUFFIXES:
            width, height, duration = _video(path)
        result.update(intrinsic_width=_positive(width), intrinsic_height=_positive(height),
                      duration_seconds=_positive(duration))
        if any(result[key] is not None for key in ('intrinsic_width', 'intrinsic_height', 'duration_seconds')):
            result['measurement_status'] = 'measured'
    except (OSError, ValueError, struct.error, ET.ParseError, subprocess.SubprocessError,
            TypeError, AttributeError, StopIteration, OverflowError):
        pass
    return result
