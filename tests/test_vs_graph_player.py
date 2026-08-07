"""Stage 2 groundwork: programmatic graph + async frame requester.

The parity test is the load-bearing one: it renders the SAME params through the
programmatic graph (what the preview will show) and through the generated .vpy
executed by VSPipe (what the export encodes) and compares frames byte-exactly.
Same source, same chain, same pixel format — so no tolerance is needed and any
divergence between the two constructions is caught mechanically.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
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

from tests.qt_harness import ensure_app   # warms VS before Qt

from core import vs_engine
from core.media_tools import MediaToolchain

REPO = Path(__file__).resolve().parents[1]
TC = MediaToolchain.discover(str(REPO))
VS_OK = (vs_engine.vs_dir() / "vapoursynth.pyd").is_file() and sys.version_info >= (3, 12)
ENCODE_OK = HAS_CV2 and not TC.missing_for_export()


def setUpModule():
    ensure_app()


def _params(path, **kw):
    from core.export_service import VideoExportParams

    base = dict(video_path=str(path), cropbox=(0, 0, 0, 0), start_frame=0,
                end_frame=20, fps=30.0, resolution="360x640")
    base.update(kw)
    return VideoExportParams(**base)


@unittest.skipUnless(VS_OK and ENCODE_OK, "VapourSynth / encode toolchain unavailable")
class GraphParityTests(unittest.TestCase):
    """Programmatic graph must equal the .vpy graph, frame for frame."""

    @classmethod
    def setUpClass(cls):
        from core.media_pipeline import MediaEncoder, _quote_vs_string

        cls.d = Path(tempfile.mkdtemp())
        # A source with structure, so a geometric divergence actually shows.
        img = np.zeros((360, 240, 3), np.uint8)
        img[:120, :] = (0, 0, 255)
        img[120:240, :80] = (0, 255, 0)
        img[240:, 160:] = (255, 0, 0)
        png = cls.d / "m.png"
        cv2.imwrite(str(png), img)
        vpy = cls.d / "src.vpy"
        vpy.write_text("\n".join([
            "import vapoursynth as vs", "core = vs.core",
            f"clip = core.imwri.Read({_quote_vs_string(str(png))})",
            "clip = clip if clip.format.id == vs.RGB24 else core.resize.Bicubic(clip, format=vs.RGB24)",
            "clip = core.std.Loop(clip, times=40)",
            "clip = core.resize.Bicubic(clip, width=240, height=360, format=vs.YUV420P8, matrix_s='170m')",
            "clip.set_output()",
        ]) + "\n", encoding="utf-8")
        cls.mp4 = cls.d / "src.mp4"
        MediaEncoder(TC).encode_vpy_to_mp4(str(vpy), str(cls.mp4), 30.0)

    def setUp(self):
        vs_engine.clear_caches()

    def _vspipe_y4m_frame(self, params, index):
        """Render one frame through the .vpy + VSPipe (the export path)."""
        from core.vs_script import write_vpy_script

        script = self.d / f"parity_{index}.vpy"
        write_vpy_script(str(script), params)
        cmd = [TC.vspipe_path, "-c", "y4m", "-s", str(index), "-e", str(index),
               str(script), "-"]
        env = os.environ.copy()
        from core.media_tools import build_media_subprocess_env

        out = subprocess.run(cmd, capture_output=True,
                             env=build_media_subprocess_env(TC.vspipe_path),
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(out.returncode, 0,
                         f"VSPipe failed: {out.stderr[-400:]!r}")
        # strip the y4m stream header and the FRAME header
        body = out.stdout.split(b"FRAME\n", 1)[1]
        for leftover in (script, script.with_suffix(".lwi")):
            if leftover.exists():
                leftover.unlink()
        return body

    def _graph_frame_bytes(self, params, index):
        """Render the same frame through the programmatic graph."""
        from core.vs_graph import build_export_graph

        clip = build_export_graph(params)
        frame = clip.get_frame(index)
        try:
            planes = [np.asarray(frame[p]) for p in range(len(frame))]
            return b"".join(np.ascontiguousarray(p).tobytes() for p in planes)
        finally:
            frame.close()

    def test_plain_graph_matches_vpy(self):
        p = _params(self.mp4)
        for idx in (0, 7):
            with self.subTest(frame=idx):
                self.assertEqual(self._graph_frame_bytes(p, idx),
                                 self._vspipe_y4m_frame(p, idx),
                                 "programmatic graph diverged from the .vpy graph")

    def test_cropped_rotated_graph_matches_vpy(self):
        p = _params(self.mp4, cropbox=(10, 20, 120, 213), rotation=180,
                    start_frame=3, end_frame=25)
        self.assertEqual(self._graph_frame_bytes(p, 2),
                         self._vspipe_y4m_frame(p, 2))

    def test_graph_geometry_is_padded_target(self):
        from core.vs_graph import build_export_graph

        clip = build_export_graph(_params(self.mp4))
        self.assertEqual((clip.width, clip.height), (384, 640))  # 360 + mod16 pad

    def test_display_graph_is_rgb24(self):
        from core.vs_graph import build_display_graph

        vs = vs_engine.load_vapoursynth()
        clip = build_display_graph(_params(self.mp4))
        self.assertEqual(clip.format.id, vs.RGB24)


@unittest.skipUnless(VS_OK, "VapourSynth unavailable")
class FrameRequesterTests(unittest.TestCase):
    def _clip(self, length=30, color=(255, 0, 0)):
        vs = vs_engine.load_vapoursynth()
        core = vs_engine.get_core()
        return core.std.BlankClip(width=64, height=48, length=length,
                                  format=vs.RGB24, color=list(color))

    def _pump(self, predicate, timeout_s=10.0):
        import time
        from PyQt6.QtCore import QCoreApplication

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        return False

    def test_delivers_frame_on_the_receiving_thread(self):
        from PyQt6.QtCore import QCoreApplication, QThread
        from core.vs_player import FrameRequester

        req = FrameRequester()
        req.set_clip(self._clip(), epoch=1)
        got = {}
        req.frame_ready.connect(
            lambda e, i, a: got.update(epoch=e, index=i, arr=a,
                                      thread=QThread.currentThread()))
        self.assertTrue(req.request(5))
        self.assertTrue(self._pump(lambda: "arr" in got), "no frame delivered")
        self.assertEqual(got["index"], 5)
        self.assertEqual(got["epoch"], 1)
        self.assertEqual(got["arr"].shape, (48, 64, 3))
        # BGR: pure red source -> (0, 0, 255)
        self.assertEqual(tuple(int(v) for v in got["arr"][0, 0]), (0, 0, 255))
        self.assertIs(got["thread"], QCoreApplication.instance().thread(),
                      "frames must arrive on the GUI thread, not a VS worker")

    def test_index_is_clamped_into_range(self):
        from core.vs_player import FrameRequester

        req = FrameRequester()
        req.set_clip(self._clip(length=10), epoch=2)
        seen = []
        req.frame_ready.connect(lambda e, i, a: seen.append(i))
        req.request(999)
        self.assertTrue(self._pump(lambda: seen))
        self.assertEqual(seen[0], 9)

    def test_stale_epoch_is_visible_to_the_receiver(self):
        from core.vs_player import FrameRequester

        req = FrameRequester()
        req.set_clip(self._clip(), epoch=3)
        req.set_clip(self._clip(), epoch=4)   # supersede
        seen = []
        req.frame_ready.connect(lambda e, i, a: seen.append(e))
        req.request(1)
        self.assertTrue(self._pump(lambda: seen))
        self.assertEqual(seen[0], 4, "delivered epoch must be the current one")

    def test_no_clip_means_no_request(self):
        from core.vs_player import FrameRequester

        req = FrameRequester()
        self.assertFalse(req.request(0))
        self.assertFalse(req.has_clip())
        self.assertEqual(req.num_frames(), 0)

    def test_inflight_budget_coalesces_scrubbing(self):
        from core.vs_player import FrameRequester

        req = FrameRequester()
        req.set_clip(self._clip(length=200), epoch=5)
        # Fire far more than the budget; extras must be dropped, not queued.
        accepted = sum(1 for i in range(50) if req.request(i, coalesce=True))
        self.assertLessEqual(accepted, FrameRequester.MAX_INFLIGHT)
        self.assertTrue(self._pump(lambda: req.inflight_count() == 0, 15.0))

    def test_clear_drops_the_clip(self):
        from core.vs_player import FrameRequester

        req = FrameRequester()
        req.set_clip(self._clip(), epoch=6)
        req.clear()
        self.assertFalse(req.has_clip())
        self.assertFalse(req.request(0))


if __name__ == "__main__":
    unittest.main()
