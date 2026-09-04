"""M4：帧转换与 metadata probe 的 VS 隔离回归。

VapourSynth 的 DLL 在已加载 PyQt 的解释器内初始化会使本 bundled runtime
异常退出。因此本模块的父测试进程只验证 worker metadata API；所有确实需要
初始化 VS core 的 frame 转换断言都由一个全新的、未导入 PyQt 的子进程执行。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

try:
    import cv2

    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from core.media_tools import MediaToolchain


REPO = Path(__file__).resolve().parents[1]
CHILD_PROBE = REPO / "tests" / "helpers" / "run_vs_frame_probe.py"
TC = MediaToolchain.discover(str(REPO))
VS_OK = (
    (REPO / "tools" / "media" / "vapoursynth.pyd").is_file()
    and sys.version_info >= (3, 12)
)
ENCODE_OK = HAS_CV2 and not TC.missing_for_export()


class _IsolatedVSCase(unittest.TestCase):
    """Run a core-owning assertion in a clean Python child process."""

    def _run_vs_child(self, case: str, *args: str) -> dict[str, object]:
        completed = subprocess.run(
            [sys.executable, str(CHILD_PROBE), case, *args],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
        )
        detail = (
            f"VS child case {case!r} failed (exit {completed.returncode})\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
        self.assertEqual(completed.returncode, 0, detail)
        try:
            return json.loads(completed.stdout.splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            self.fail(f"VS child {case!r} did not return JSON: {detail}\n{exc}")


class ParentProcessIsolationTests(unittest.TestCase):
    def test_parent_never_imports_vapoursynth_for_frame_or_probe_tests(self):
        # This module runs in the same unittest process as PyQt preview tests.
        self.assertNotIn("vapoursynth", sys.modules)
        self.assertNotIn("core.vs_engine", sys.modules)


@unittest.skipUnless(VS_OK, "bundled VapourSynth unavailable")
class FrameConversionTests(_IsolatedVSCase):
    def test_planar_rgb_and_stride_conversion_contract(self):
        result = self._run_vs_child("frame_contract")
        self.assertEqual(result["status"], "ok")


@unittest.skipUnless(
    VS_OK and ENCODE_OK, "VapourSynth / encode toolchain unavailable"
)
class WorkerProbeTests(_IsolatedVSCase):
    @classmethod
    def setUpClass(cls):
        from core.media_pipeline import MediaEncoder, _quote_vs_string

        cls.d = Path(tempfile.mkdtemp())
        png = cls.d / "m.png"
        cv2.imwrite(str(png), np.full((360, 240, 3), 128, np.uint8))
        vpy = cls.d / "s.vpy"
        vpy.write_text(
            "\n".join(
                [
                    "import vapoursynth as vs",
                    "core = vs.core",
                    f"clip = core.imwri.Read({_quote_vs_string(str(png))})",
                    "clip = clip if clip.format.id == vs.RGB24 else core.resize.Bicubic(clip, format=vs.RGB24)",
                    "clip = core.std.Loop(clip, times=45)",
                    "clip = core.resize.Bicubic(clip, width=240, height=360, format=vs.YUV420P8, matrix_s='170m')",
                    "clip.set_output()",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        cls.mp4 = cls.d / "probe.mp4"
        MediaEncoder(TC).encode_vpy_to_mp4(str(vpy), str(cls.mp4), 30.0)

    def test_probe_reports_exact_geometry_and_frame_count(self):
        from core.video_processor import probe_video_info

        info = probe_video_info(str(self.mp4))
        self.assertEqual(info.width, 240)
        self.assertEqual(info.height, 360)
        self.assertEqual(info.total_frames, 45)
        self.assertAlmostEqual(info.fps, 30.0, places=6)
        self.assertAlmostEqual(info.duration, 1.5, places=3)
        self.assertNotIn("vapoursynth", sys.modules)

    def test_video_processor_prefers_worker_probe(self):
        from core.video_processor import VideoProcessor

        info = VideoProcessor().get_video_info(str(self.mp4))
        self.assertIsNotNone(info)
        self.assertEqual((info.width, info.height, info.total_frames), (240, 360, 45))
        self.assertNotIn("vapoursynth", sys.modules)

    def test_source_node_cache_is_owned_by_the_isolated_vs_process(self):
        result = self._run_vs_child("source_cache", str(self.mp4))
        self.assertEqual(result["status"], "ok")

    def test_real_frame_round_trips_to_bgr_in_isolated_vs_process(self):
        result = self._run_vs_child("real_frame", str(self.mp4))
        self.assertEqual(result["shape"], [360, 240, 3])
        self.assertGreater(result["mean"], 100)

    def test_missing_file_returns_none(self):
        from core.video_processor import VideoProcessor

        self.assertIsNone(VideoProcessor().get_video_info(str(self.d / "nope.mp4")))


if __name__ == "__main__":
    unittest.main()
