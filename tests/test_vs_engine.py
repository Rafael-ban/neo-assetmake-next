"""Stage 1: in-process VapourSynth engine.

Pins the loading strategy that was established empirically:

- Python 3.11 CANNOT import the bundled binding (wheel is ``cp312-abi3``, a
  stable ABI with a 3.12 FLOOR; its ``.pyd`` needs ``PyObject_Vectorcall`` /
  ``PyType_FromMetaclass`` etc., absent from 3.11's ``python3.dll``) — so the
  engine refuses to try below 3.12 instead of dying in the loader.
- ``tools/media`` must NEVER go on ``sys.path``: it is a flat embedded CPython
  distribution and shadowing the host's ``_ctypes.pyd`` breaks ctypes outright
  (measured: "class must define a '_type_' attribute").
- Plugins autoload relative to the LOADED ``VapourSynth.dll``, so loading from
  ``tools/media`` must yield ``lsmas``/``imwri`` with no wheel installed.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import ctypes
import sys
import unittest
from pathlib import Path

from config.vsconfig import VSConfig, load_vsconfig
from core import vs_engine

REPO = Path(__file__).resolve().parents[1]
VS_PYD = vs_engine.vs_dir() / "vapoursynth.pyd"
VS_OK = VS_PYD.is_file() and sys.version_info >= (3, 12)


@unittest.skipUnless(VS_OK, "bundled VapourSynth (tools/media) or Python 3.12+ unavailable")
class VSEngineTests(unittest.TestCase):
    def test_loads_without_polluting_sys_path(self):
        vs = vs_engine.load_vapoursynth()
        self.assertTrue(hasattr(vs, "core"))
        self.assertNotIn(
            str(vs_engine.vs_dir()), sys.path,
            "tools/media on sys.path shadows the host interpreter's "
            "extension modules (breaks ctypes)",
        )

    def test_host_ctypes_still_works_after_load(self):
        vs_engine.load_vapoursynth()

        class _S(ctypes.Structure):
            _fields_ = [("a", ctypes.c_int)]

        self.assertEqual(ctypes.sizeof(_S), 4)

    def test_module_and_core_are_singletons(self):
        self.assertIs(vs_engine.load_vapoursynth(), vs_engine.load_vapoursynth())
        self.assertIs(vs_engine.get_core(), vs_engine.get_core())

    def test_required_plugins_autoload(self):
        cfg = load_vsconfig()
        present = vs_engine.available_plugins()
        for name in cfg.required_plugins:
            self.assertIn(name, present, f"plugin {name} did not autoload")
        self.assertEqual(vs_engine.missing_plugins(), ())
        vs_engine.verify_plugins()  # must not raise

    def test_missing_plugin_is_reported(self):
        cfg = VSConfig(required_plugins=("lsmas", "imwri", "definitely_absent_ns"))
        self.assertEqual(vs_engine.missing_plugins(cfg), ("definitely_absent_ns",))
        with self.assertRaises(vs_engine.VSUnavailable):
            vs_engine.verify_plugins(cfg)

    def test_core_reports_threads_and_cache(self):
        core = vs_engine.get_core()
        self.assertGreater(core.num_threads, 0)
        self.assertGreater(core.max_cache_size, 0)

    def test_lwi_cache_path_is_stable_and_outside_source_dir(self):
        p1 = vs_engine.lwi_cache_path(r"C:\media\clip.mp4")
        p2 = vs_engine.lwi_cache_path(r"C:\media\clip.mp4")
        self.assertEqual(p1, p2, "index path must be stable for cache reuse")
        self.assertNotIn(os.path.join("media", "clip"), p1)
        self.assertTrue(p1.endswith(".lwi"))
        self.assertNotEqual(p1, vs_engine.lwi_cache_path(r"C:\media\other.mp4"))


class VSEngineGuardTests(unittest.TestCase):
    """These hold regardless of whether the bundle is present."""

    def test_vs_dir_points_at_the_bundle(self):
        self.assertEqual(Path(vs_engine.vs_dir()).name, "media")

    def test_vsunavailable_is_a_runtime_error(self):
        self.assertTrue(issubclass(vs_engine.VSUnavailable, RuntimeError))


if __name__ == "__main__":
    unittest.main()
