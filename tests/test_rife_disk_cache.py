"""Lossless disk cache and hit graph isolation tests without native VS."""
import ctypes
import gzip
import json
import struct
import importlib.util
import inspect
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

_PATH = Path(__file__).parents[1] / 'resources/vapoursynth/python/assetmaker_vs/rife_cache.py'
_SPEC = importlib.util.spec_from_file_location('rife_cache', _PATH)
cache_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(cache_module)


class Frame:
    def __init__(self):
        self.planes = [(ctypes.c_float * 4)(-0.25, 0.125, 1.1, 123.456) for _ in range(3)]
        self.props = {'_Matrix': 0, 'data': b'\x00\xff', 'array': [1, 2]}

    def get_read_ptr(self, plane):
        return ctypes.c_void_p(ctypes.addressof(self.planes[plane]))

    get_write_ptr = get_read_ptr

    def get_stride(self, plane):
        return 8

    def copy(self):
        return Frame()


class RifeDiskCacheTests(unittest.TestCase):
    def test_roundtrip_preserves_float_bits_and_props_and_corrupt_misses(self):
        with tempfile.TemporaryDirectory() as temp:
            disk = cache_module.DiskFrameCache(temp, {'test': 1}, 2, 2)
            original = Frame()
            disk.write(3, original)
            restored = disk.restore(Frame(), disk.read(3))
            self.assertEqual(original.props, restored.props)
            for left, right in zip(original.planes, restored.planes):
                self.assertEqual(bytes(left), bytes(right))
            raw = disk.directory / '000000003.rgb'
            self.assertTrue(raw.is_file())
            raw.write_bytes(b'broken')
            self.assertIsNone(disk.read(3))

    def test_budget_and_clear(self):
        with tempfile.TemporaryDirectory() as temp:
            root = cache_module.cache_root(temp)
            disk = cache_module.DiskFrameCache(root, {}, 2, 2, max_bytes=1)
            disk.write(0, Frame())
            self.assertFalse(list(root.glob('*/*.rgb')))
            cache_module.clear_rife_cache(temp)
            cache_module.clear_rife_cache(temp)
            self.assertFalse(root.exists())

    def test_clear_reports_permission_failure(self):
        with patch.object(cache_module.shutil, 'rmtree',
                          side_effect=PermissionError('locked')):
            with self.assertRaisesRegex(OSError, '暂停预览/导出'):
                cache_module.clear_rife_cache('project')

    def test_write_failure_is_reported_once(self):
        with tempfile.TemporaryDirectory() as temp:
            disk = cache_module.DiskFrameCache(temp, {}, 2, 2)
            with patch.object(cache_module.Path, 'open',
                              side_effect=OSError('disk full')):
                with self.assertLogs(cache_module._LOG, level='WARNING') as logs:
                    disk.write(0, Frame())
                    disk.write(1, Frame())
            self.assertEqual(len(logs.output), 1)
            self.assertIn('剩余磁盘空间', logs.output[0])

    @staticmethod
    def _legacy_frame(path, frame):
        props = json.dumps(cache_module._encode_props(frame.props),
                           separators=(',', ':')).encode()
        with gzip.open(path, 'wb', compresslevel=1) as stream:
            stream.write(struct.pack('<I', len(props)))
            stream.write(props)
            for plane in frame.planes:
                stream.write(bytes(plane))

    def test_legacy_hit_migrates_atomically_and_preserves_bits_and_props(self):
        with tempfile.TemporaryDirectory() as temp:
            disk = cache_module.DiskFrameCache(temp, {}, 2, 2)
            legacy = disk.directory / '000000003.rgb.gz'
            raw = disk.directory / '000000003.rgb'
            original = Frame()
            self._legacy_frame(legacy, original)
            restored = disk.restore(Frame(), disk.read(3))
            self.assertEqual(original.props, restored.props)
            for left, right in zip(original.planes, restored.planes):
                self.assertEqual(bytes(left), bytes(right))
            self.assertTrue(raw.is_file())
            self.assertFalse(legacy.exists())
            self.assertEqual(disk.read(3)[0], original.props)

    def test_disabled_cache_reads_legacy_without_migrating(self):
        with tempfile.TemporaryDirectory() as temp:
            disk = cache_module.DiskFrameCache(temp, {}, 2, 2, max_bytes=0)
            legacy = disk.directory / '000000003.rgb.gz'
            raw = disk.directory / '000000003.rgb'
            self._legacy_frame(legacy, Frame())
            self.assertEqual(disk.read(3)[0], Frame().props)
            self.assertTrue(legacy.exists())
            self.assertFalse(raw.exists())

    def test_zero_budget_preserves_existing_frames_even_on_explicit_prune(self):
        with tempfile.TemporaryDirectory() as temp:
            writer = cache_module.DiskFrameCache(temp, {}, 2, 2)
            writer.write(0, Frame())
            raw = writer.directory / '000000000.rgb'
            legacy = writer.directory / '000000001.rgb.gz'
            self._legacy_frame(legacy, Frame())

            disabled = cache_module.DiskFrameCache(temp, {}, 2, 2, max_bytes=0)
            self.assertTrue(raw.is_file())
            self.assertTrue(legacy.is_file())
            self.assertEqual(disabled.read(0)[0], Frame().props)
            self.assertEqual(disabled.read(1)[0], Frame().props)
            disabled.prune()
            disabled.write(2, Frame())
            self.assertTrue(raw.is_file())
            self.assertTrue(legacy.is_file())
            self.assertFalse((disabled.directory / '000000002.rgb').exists())

    def test_malformed_props_are_cache_misses(self):
        malformed = (
            {'bad': {'array': False, 'values': []}},
            [],
            {'bad': {'array': False, 'values': [{'bytes': 'a'}]}},
        )
        with tempfile.TemporaryDirectory() as temp:
            disk = cache_module.DiskFrameCache(temp, {}, 2, 2)
            raw = disk.directory / '000000000.rgb'
            for props in malformed:
                with self.subTest(props=props):
                    encoded = json.dumps(props).encode()
                    raw.write_bytes(struct.pack('<I', len(encoded))
                                    + encoded + bytes(2 * 2 * 4 * 3))
                    self.assertIsNone(disk.read(0))

    def test_corrupt_raw_falls_back_to_legacy_and_repairs_raw(self):
        with tempfile.TemporaryDirectory() as temp:
            disk = cache_module.DiskFrameCache(temp, {}, 2, 2)
            legacy = disk.directory / '000000003.rgb.gz'
            raw = disk.directory / '000000003.rgb'
            self._legacy_frame(legacy, Frame())
            raw.write_bytes(b'broken')
            self.assertEqual(disk.read(3)[0], Frame().props)
            self.assertFalse(legacy.exists())
            self.assertEqual(disk.read(3)[0], Frame().props)
            raw.unlink()
            legacy.write_bytes(b'broken')
            self.assertIsNone(disk.read(3))

    def test_migration_failure_preserves_legacy_hit(self):
        with tempfile.TemporaryDirectory() as temp:
            disk = cache_module.DiskFrameCache(temp, {}, 2, 2)
            legacy = disk.directory / '000000003.rgb.gz'
            raw = disk.directory / '000000003.rgb'
            self._legacy_frame(legacy, Frame())
            with patch.object(cache_module.os, 'replace',
                              side_effect=OSError('locked')):
                self.assertEqual(disk.read(3)[0], Frame().props)
            self.assertTrue(legacy.exists())
            self.assertFalse(raw.exists())
            self.assertFalse(list(disk.directory.glob('*.tmp')))

    def test_prune_counts_raw_and_legacy_frames_together(self):
        with tempfile.TemporaryDirectory() as temp:
            disk = cache_module.DiskFrameCache(temp, {}, 2, 2)
            disk.write(0, Frame())
            legacy = disk.directory / '000000001.rgb.gz'
            self._legacy_frame(legacy, Frame())
            raw = disk.directory / '000000000.rgb'
            disk.max_bytes = raw.stat().st_size + legacy.stat().st_size - 1
            disk.prune()
            remaining = list(disk.directory.glob('*.rgb'))
            remaining += list(disk.directory.glob('*.rgb.gz'))
            self.assertEqual(len(remaining), 1)

    def test_hit_does_not_construct_or_reference_rife(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source.mp4'
            source.write_bytes(b'source')
            model = Path(temp) / 'model.bin'
            model.write_bytes(b'model')
            rgb = types.SimpleNamespace(width=2, height=2, num_frames=5,
                fps_num=24, fps_den=1,
                format=types.SimpleNamespace(name='RGBS', id=1))
            calls = []

            class Std:
                def BlankClip(self, clip, **kwargs):
                    self.length = kwargs['length']
                    return 'blank'

                def ModifyFrame(self, clip, clips, selector):
                    calls.append(clips)
                    self.last_selector = selector
                    return selector(n=0, f=Frame())

                def FrameEval(self, clip, eval):
                    return eval

                def SetVideoCache(self, clip, *, mode):
                    self.assert_mode = mode
                    return None

            std = Std()
            factory_calls = []

            def factory():
                factory_calls.append(True)
                return 'rife'

            kwargs = dict(project_root=temp, source_path=source, rotation=0,
                          target_fps=(60, 1), model_path=model, settings={})
            select = cache_module.cached_rife(types.SimpleNamespace(std=std), rgb,
                                              factory, **kwargs)
            select(0)
            self.assertEqual(factory_calls, [True])
            calls.clear()
            select_again = cache_module.cached_rife(types.SimpleNamespace(std=std),
                                                    rgb, factory, **kwargs)
            select_again(0)
            self.assertEqual(factory_calls, [True])
            self.assertEqual(calls, [['blank']])
            captured = inspect.getclosurevars(std.last_selector).nonlocals
            self.assertEqual(captured['ticket'], [None])
            self.assertNotIn('record', captured)
            self.assertEqual(std.length, 12)
            self.assertEqual(std.assert_mode, 0)


if __name__ == '__main__':
    unittest.main()
