"""Stage 1: VS frame conversion + in-process metadata probe (real media).

Frame conversion pins three empirically-verified details: VS RGB24 is PLANAR
with plane0=R/1=G/2=B (so this app's BGR convention needs [2,1,0]), planes are
stride-padded (measured stride 384 for width 360), and plane views are VS-owned
memory that must be copied before ``frame.close()``.

The probe pins that metadata is now EXACT: the retired mpv path could only estimate
(``round(duration*fps)`` for the frame count, a hardcoded 30.0 for fps).
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
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

from core import vs_engine
from core.media_tools import MediaToolchain

REPO = Path(__file__).resolve().parents[1]
TC = MediaToolchain.discover(str(REPO))
VS_OK = (vs_engine.vs_dir() / "vapoursynth.pyd").is_file() and sys.version_info >= (3, 12)
ENCODE_OK = HAS_CV2 and not TC.missing_for_export()


@unittest.skipUnless(VS_OK, "bundled VapourSynth unavailable")
class FrameConversionTests(unittest.TestCase):
    def _core(self):
        return vs_engine.load_vapoursynth(), vs_engine.get_core()

    def test_planar_rgb_red_becomes_bgr_red(self):
        from core.vs_frame import request_bgr_frame

        vs, core = self._core()
        clip = core.std.BlankClip(width=64, height=32, length=1,
                                  format=vs.RGB24, color=[255, 0, 0])
        arr = request_bgr_frame(clip, 0)
        self.assertIsNotNone(arr)
        self.assertEqual(arr.shape, (32, 64, 3))
        self.assertEqual(arr.dtype, np.uint8)
        # BGR: blue=0, green=0, red=255
        self.assertEqual(tuple(int(v) for v in arr[0, 0]), (0, 0, 255))

    def test_planar_rgb_blue_becomes_bgr_blue(self):
        from core.vs_frame import request_bgr_frame

        vs, core = self._core()
        clip = core.std.BlankClip(width=8, height=8, length=1,
                                  format=vs.RGB24, color=[0, 0, 255])
        arr = request_bgr_frame(clip, 0)
        self.assertEqual(tuple(int(v) for v in arr[0, 0]), (255, 0, 0))

    def test_stride_padded_width_does_not_shear(self):
        from core.vs_frame import request_bgr_frame

        vs, core = self._core()
        # 360 is not a multiple of the 64-byte alignment -> stride 384 measured.
        clip = core.std.BlankClip(width=360, height=16, length=1,
                                  format=vs.RGB24, color=[0, 255, 0])
        arr = request_bgr_frame(clip, 0)
        self.assertEqual(arr.shape, (16, 360, 3))
        # A shear would leave non-green columns at the right edge.
        self.assertTrue(bool((arr[:, :, 1] == 255).all()))
        self.assertTrue(bool((arr[:, :, 0] == 0).all()))

    def test_array_survives_frame_close(self):
        from core.vs_frame import frame_to_bgr

        vs, core = self._core()
        clip = core.std.BlankClip(width=32, height=16, length=1,
                                  format=vs.RGB24, color=[10, 20, 30])
        frame = clip.get_frame(0)
        arr = frame_to_bgr(frame)
        frame.close()
        self.assertEqual(tuple(int(v) for v in arr[0, 0]), (30, 20, 10))
        self.assertEqual(int(arr.sum()), 32 * 16 * 60)

    def test_non_rgb_frame_returns_none(self):
        from core.vs_frame import frame_to_bgr

        vs, core = self._core()
        clip = core.std.BlankClip(width=16, height=16, length=1, format=vs.GRAY8)
        frame = clip.get_frame(0)
        try:
            self.assertIsNone(frame_to_bgr(frame))
        finally:
            frame.close()

    def test_display_conversion_appends_rgb_tail(self):
        from core.vs_frame import to_display_rgb_clip

        vs, core = self._core()
        yuv = core.std.BlankClip(width=48, height=32, length=1, format=vs.YUV420P8)
        rgb = to_display_rgb_clip(yuv, vs)
        self.assertEqual(rgb.format.id, vs.RGB24)
        # already-RGB clips pass through untouched
        self.assertIs(to_display_rgb_clip(rgb, vs), rgb)


@unittest.skipUnless(VS_OK and ENCODE_OK, "VapourSynth / encode toolchain unavailable")
class InProcessProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.media_pipeline import MediaEncoder, _quote_vs_string

        cls.d = Path(tempfile.mkdtemp())
        png = cls.d / "m.png"
        cv2.imwrite(str(png), np.full((360, 240, 3), 128, np.uint8))
        vpy = cls.d / "s.vpy"
        vpy.write_text("\n".join([
            "import vapoursynth as vs", "core = vs.core",
            f"clip = core.imwri.Read({_quote_vs_string(str(png))})",
            "clip = clip if clip.format.id == vs.RGB24 else core.resize.Bicubic(clip, format=vs.RGB24)",
            "clip = core.std.Loop(clip, times=45)",
            "clip = core.resize.Bicubic(clip, width=240, height=360, format=vs.YUV420P8, matrix_s='170m')",
            "clip.set_output()",
        ]) + "\n", encoding="utf-8")
        cls.mp4 = cls.d / "probe.mp4"
        MediaEncoder(TC).encode_vpy_to_mp4(str(vpy), str(cls.mp4), 30.0)

    def setUp(self):
        vs_engine.clear_caches()

    def test_probe_reports_exact_geometry_and_frame_count(self):
        from core.video_processor import probe_video_info

        info = probe_video_info(str(self.mp4))
        self.assertEqual(info.width, 240)
        self.assertEqual(info.height, 360)
        self.assertEqual(info.total_frames, 45)   # exact, not round(duration*fps)
        self.assertAlmostEqual(info.fps, 30.0, places=6)
        self.assertAlmostEqual(info.duration, 1.5, places=3)

    def test_video_processor_prefers_vs(self):
        from core.video_processor import VideoProcessor

        info = VideoProcessor().get_video_info(str(self.mp4))
        self.assertIsNotNone(info)
        self.assertEqual((info.width, info.height, info.total_frames), (240, 360, 45))

    def test_source_clip_is_cached(self):
        c1 = vs_engine.source_clip(str(self.mp4))
        c2 = vs_engine.source_clip(str(self.mp4))
        self.assertIs(c1, c2, "source node cache avoids rebuilding the .lwi index")

    def test_real_frame_round_trips_to_bgr(self):
        from core.vs_frame import request_bgr_frame, to_display_rgb_clip

        vs = vs_engine.load_vapoursynth()
        clip = to_display_rgb_clip(vs_engine.source_clip(str(self.mp4)), vs)
        arr = request_bgr_frame(clip, 10)
        self.assertIsNotNone(arr)
        self.assertEqual(arr.shape[:2], (360, 240))
        self.assertGreater(int(arr.mean()), 100)   # mid-grey source

    def test_missing_file_returns_none(self):
        from core.video_processor import VideoProcessor

        self.assertIsNone(VideoProcessor().get_video_info(str(self.d / "nope.mp4")))


if __name__ == "__main__":
    unittest.main()
