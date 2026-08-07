"""Real-mpv integration tests for the S1 screenshot/capture path.

Proves that ``request_screenshot`` reads back the *actual* video frame (the
old code left ``current_frame`` as ``np.zeros`` forever, so 截取帧 saved black
icons) and that mpv's baked-in video-rotate is normalized back to source
orientation. Skipped when tools/media is absent.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import time
import tempfile
import unittest
from pathlib import Path

import numpy as np

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from tests.qt_harness import ensure_app
from PyQt6.QtCore import QCoreApplication

from core.media_tools import MediaToolchain

REPO = Path(__file__).resolve().parent.parent
TC = MediaToolchain.discover(str(REPO))
MPV_OK = bool(TC.mpv_path)
ENCODE_OK = HAS_CV2 and not TC.missing_for_export()


def setUpModule():
    ensure_app()


def _pump_until(condition, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


def _top_quarter_is_red(frame: np.ndarray) -> bool:
    top = frame[: frame.shape[0] // 4]
    return top[:, :, 2].mean() > 150 and top[:, :, 0].mean() < 100


@unittest.skipUnless(MPV_OK and ENCODE_OK, "mpv / encode toolchain (tools/media) unavailable")
class MpvScreenshotCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.media_pipeline import MediaEncoder, _quote_vs_string

        cls.d = Path(tempfile.mkdtemp())
        png = cls.d / "marker.png"
        img = np.zeros((360, 240, 3), np.uint8)
        img[:180, :] = (0, 0, 255)   # top half red (BGR)
        img[180:, :] = (255, 0, 0)   # bottom half blue
        cv2.imwrite(str(png), img)
        vpy = cls.d / "src.vpy"
        vpy.write_text("\n".join([
            "import vapoursynth as vs", "core = vs.core",
            f"clip = core.imwri.Read({_quote_vs_string(str(png))})",
            "clip = clip if clip.format.id == vs.RGB24 else core.resize.Bicubic(clip, format=vs.RGB24)",
            "clip = core.std.Loop(clip, times=30)",
            "clip = core.resize.Bicubic(clip, width=240, height=360, format=vs.YUV420P8, matrix_s='709')",
            "clip.set_output()",
        ]) + "\n", encoding="utf-8")
        cls.mp4 = cls.d / "src.mp4"
        MediaEncoder(TC).encode_vpy_to_mp4(str(vpy), str(cls.mp4), 30.0)

    def _load_widget(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        w = VideoPreviewWidget()
        # These tests exercise the mpv screenshot path specifically, so force
        # the legacy backend even when the in-process VapourSynth core is up.
        w._use_vs_preview = lambda: False
        self.assertTrue(w.load_video(str(self.mp4)))
        self.assertTrue(
            _pump_until(lambda: w._mpv_ipc_connected, 15.0),
            "mpv IPC did not connect",
        )
        self.addCleanup(w.clear)
        return w

    def test_screenshot_returns_real_source_frame(self):
        w = self._load_widget()
        box = {}
        self.assertTrue(w.request_screenshot(lambda f: box.update(frame=f)))
        self.assertTrue(_pump_until(lambda: "frame" in box, 15.0), "no screenshot reply")
        frame = box["frame"]
        self.assertIsNotNone(frame)
        self.assertEqual(frame.shape[:2], (360, 240))  # source orientation
        self.assertGreater(frame.mean(), 10.0, "frame is black — capture still broken")
        self.assertTrue(_top_quarter_is_red(frame), "content mismatch: not the marker frame")
        # current_frame was repopulated (the P0 defect was: stays np.zeros forever)
        self.assertGreater(w.current_frame.mean(), 10.0)

    def test_screenshot_normalizes_baked_in_rotation(self):
        w = self._load_widget()
        w.set_rotation(90)
        time.sleep(0.5)  # let mpv reconfigure its filter chain
        QCoreApplication.processEvents()
        box = {}
        w.request_screenshot(lambda f: box.update(frame=f))
        self.assertTrue(_pump_until(lambda: "frame" in box, 15.0), "no screenshot reply")
        frame = box["frame"]
        self.assertIsNotNone(frame)
        # mpv bakes video-rotate into the PNG (360x240 for rotate=90); the widget
        # must undo it so current_frame stays in source orientation.
        self.assertEqual(frame.shape[:2], (360, 240))
        self.assertTrue(_top_quarter_is_red(frame))

    def test_capture_frame_async_delivers_real_frame(self):
        w = self._load_widget()
        box = {}
        w.capture_frame_async(lambda f: box.update(frame=f))
        self.assertTrue(_pump_until(lambda: "frame" in box, 15.0), "no capture callback")
        frame = box["frame"]
        self.assertIsNotNone(frame)
        self.assertGreater(frame.mean(), 10.0)
        self.assertEqual(frame.shape[:2], (360, 240))


if __name__ == "__main__":
    unittest.main()
