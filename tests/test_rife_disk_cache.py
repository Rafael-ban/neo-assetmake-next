"""Lossless disk cache and hit graph isolation tests without native VS."""
import ctypes
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
            (disk.directory / '000000003.rgb.gz').write_bytes(b'broken')
            self.assertIsNone(disk.read(3))

    def test_budget_and_clear(self):
        with tempfile.TemporaryDirectory() as temp:
            root = cache_module.cache_root(temp)
            disk = cache_module.DiskFrameCache(root, {}, 2, 2, max_bytes=1)
            disk.write(0, Frame())
            self.assertFalse(list(root.glob('*/*.rgb.gz')))
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
            with patch.object(cache_module.gzip, 'open',
                              side_effect=OSError('disk full')):
                with self.assertLogs(cache_module._LOG, level='WARNING') as logs:
                    disk.write(0, Frame())
                    disk.write(1, Frame())
            self.assertEqual(len(logs.output), 1)
            self.assertIn('剩余磁盘空间', logs.output[0])

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
