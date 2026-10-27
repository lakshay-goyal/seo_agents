import importlib.util
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zlib


# Load only the standalone helper, without running package initialization.
_SPEC = importlib.util.spec_from_file_location(
    'asset_probe', Path(__file__).resolve().parents[1]
    / 'src/seo_agents/exploration/checks/tools/asset_probe.py')
probe_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(probe_module)
probe_asset = probe_module.probe_asset


class AssetProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def asset(self, name, data):
        path = self.directory / name
        path.write_bytes(data)
        return path

    def check_dimensions(self, name, data, width, height):
        result = probe_asset(self.asset(name, data))
        self.assertEqual(set(result), {'size_bytes', 'intrinsic_width',
                         'intrinsic_height', 'duration_seconds', 'measurement_status'})
        self.assertEqual(result['size_bytes'], len(data))
        self.assertEqual((result['intrinsic_width'], result['intrinsic_height']), (width, height))
        self.assertEqual(result['measurement_status'], 'measured')
        self.assertIsNone(result['duration_seconds'])

    def test_png_header(self):
        header = b'IHDR' + struct.pack('>IIBBBBB', 640, 320, 8, 2, 0, 0, 0)
        data = b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + header + struct.pack('>I', zlib.crc32(header))
        self.check_dimensions('image.png', data, 640, 320)

    def test_gif_header(self):
        self.check_dimensions('image.gif', b'GIF89a' + struct.pack('<HHBBB', 20, 10, 0, 0, 0), 20, 10)

    def test_jpeg_header_with_app_segment_and_fill(self):
        frame = bytes([8]) + struct.pack('>HH', 24, 32) + b'\x01\x01\x11\x00'
        data = b'\xff\xd8\xff\xe0\x00\x04AB\xff\xff\xc2' + struct.pack('>H', len(frame) + 2) + frame
        self.check_dimensions('image.jpg', data, 32, 24)

    def test_svg_dimensions_and_viewbox(self):
        self.check_dimensions('image.svg', b'<svg xmlns="http://www.w3.org/2000/svg" width="40px" height="20"/>', 40, 20)
        self.check_dimensions('view.svg', b'<svg viewBox="-10, -20, 60, 30"/>', 60, 30)

    def test_webp_variants(self):
        chunks = [(b'VP8X', b'\x00' * 4 + (31).to_bytes(3, 'little') + (23).to_bytes(3, 'little')),
                  (b'VP8 ', b'\x10\x00\x00\x9d\x01\x2a' + struct.pack('<HH', 32, 24)),
                  (b'VP8L', b'\x2f' + (31 | (23 << 14)).to_bytes(4, 'little'))]
        for kind, payload in chunks:
            with self.subTest(kind=kind):
                chunk = kind + struct.pack('<I', len(payload)) + payload + b'\x00' * (len(payload) % 2)
                data = b'RIFF' + struct.pack('<I', 4 + len(chunk)) + b'WEBP' + chunk
                self.check_dimensions('image.webp', data, 32, 24)

    def test_corrupt_unknown_and_zero_dimensions(self):
        for name, data in [('bad.png', b'\x89PNG'), ('bad.jpg', b'\xff\xd8\xff\xc0\x00\x01'),
                           ('bad.svg', b'<svg'), ('zero.gif', b'GIF89a' + b'\x00' * 7),
                           ('unknown.bin', b'arbitrary bytes'),
                           ('entity.svg', b'<!DOCTYPE svg [<!ENTITY x "bad">]><svg/>')]:
            with self.subTest(name=name):
                result = probe_asset(self.asset(name, data))
                self.assertEqual(result['size_bytes'], len(data))
                self.assertIsNone(result['intrinsic_width'])
                self.assertIsNone(result['intrinsic_height'])
                self.assertEqual(result['measurement_status'], 'unknown')

    def test_missing_and_directory(self):
        for path in (self.directory / 'missing', self.directory):
            self.assertEqual(probe_asset(path)['measurement_status'], 'unavailable')

    def test_video_unavailable_and_timeout(self):
        path = self.asset('movie.mp4', b'bad video')
        with patch.object(probe_module.shutil, 'which', return_value=None):
            self.assertEqual(probe_asset(path)['measurement_status'], 'unknown')
        with patch.object(probe_module.shutil, 'which', return_value='/usr/bin/ffprobe'), patch.object(
                probe_module.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ffprobe', 3)):
            result = probe_asset(path)
            self.assertEqual(result['measurement_status'], 'unknown')
            self.assertEqual(result['size_bytes'], 9)

    def test_video_measurements_and_bounds(self):
        def run(command, **kwargs):
            self.assertEqual(kwargs['timeout'], 3)
            self.assertEqual(command[command.index('-protocol_whitelist') + 1], 'file')
            self.assertIn('-format_whitelist', command)
            self.assertEqual(kwargs['stdout'], subprocess.PIPE)
            return subprocess.CompletedProcess(command, 0, stdout=b'{"streams":[{"width":1920,"height":1080}],"format":{"duration":"2.5"}}')
        with patch.object(probe_module.shutil, 'which', return_value='/usr/bin/ffprobe'), patch.object(
                probe_module.subprocess, 'run', side_effect=run):
            result = probe_asset(self.asset('movie.mp4', b'video'))
        self.assertEqual((result['intrinsic_width'], result['intrinsic_height'], result['duration_seconds']), (1920, 1080, 2.5))


if __name__ == '__main__':
    unittest.main()
