"""S3: still-frame crop mode tests.

mpv embeds via --wid, creating its own child window over the surface (mpv
manual, --wid): QPainter overlays are occluded and mouse events never reach
Qt, so interactive cropping over the LIVE video never worked. Crop mode
freezes the current frame onto the QLabel page, where the existing painted
cropbox + drag machinery (already used by the static-image path) applies.
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
from PyQt6.QtCore import QCoreApplication, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent

from core.media_tools import MediaToolchain

REPO = Path(__file__).resolve().parent.parent
TC = MediaToolchain.discover(str(REPO))
MPV_OK = bool(TC.mpv_path)
ENCODE_OK = HAS_CV2 and not TC.missing_for_export()


def setUpModule():
    ensure_app()


def _marker(w=240, h=360):
    frame = np.zeros((h, w, 3), np.uint8)
    frame[: h // 2] = (0, 0, 255)
    return frame


def _mpv_widget():
    """Widget that thinks it has a connected mpv session, screenshot mocked."""
    from gui.widgets.video_preview import VideoPreviewWidget

    w = VideoPreviewWidget()
    w._mpv_process = object()
    w._mpv_ipc_connected = True
    w._has_video = True
    w.video_width, w.video_height = 240, 360
    w.video_fps, w.total_frames = 30.0, 90
    w._send_mpv_command = lambda *a, **k: None  # no real socket
    w.request_screenshot = (
        lambda callback=None, *a, **k: callback(_marker()) if callback else None
    )
    w._init_cropbox()  # what a finished load would have done
    w._display_stack.setCurrentIndex(w._mpv_page_index)
    return w


class CropModeTests(unittest.TestCase):
    def test_enter_freezes_frame_onto_label_page(self):
        w = _mpv_widget()
        states = []
        w.crop_mode_changed.connect(states.append)
        self.assertTrue(w.enter_crop_mode())
        self.assertTrue(w.is_crop_mode())
        self.assertEqual(w._display_stack.currentIndex(), 0)  # QLabel page
        self.assertEqual(states, [True])

    def test_exit_returns_to_mpv_page(self):
        w = _mpv_widget()
        w.enter_crop_mode()
        w.exit_crop_mode()
        self.assertFalse(w.is_crop_mode())
        self.assertEqual(w._display_stack.currentIndex(), w._mpv_page_index)

    def test_play_auto_exits_crop_mode(self):
        w = _mpv_widget()
        w.enter_crop_mode()
        states = []
        w.crop_mode_changed.connect(states.append)
        w.play()
        self.assertFalse(w.is_crop_mode())
        self.assertEqual(states, [False])
        self.assertEqual(w._display_stack.currentIndex(), w._mpv_page_index)

    def test_seek_auto_exits_crop_mode(self):
        w = _mpv_widget()
        w.enter_crop_mode()
        w.seek_to_frame(10)
        self.assertFalse(w.is_crop_mode())

    def test_clear_resets_crop_mode(self):
        w = _mpv_widget()
        w.enter_crop_mode()
        states = []
        w.crop_mode_changed.connect(states.append)
        w.clear()
        self.assertFalse(w.is_crop_mode())
        self.assertEqual(states, [False])

    def test_static_image_enters_without_screenshot(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        w = VideoPreviewWidget()  # no mpv session
        w.load_static_image_from_array(_marker())
        self.assertTrue(w.enter_crop_mode())
        self.assertTrue(w.is_crop_mode())
        self.assertEqual(w._display_stack.currentIndex(), 0)

    def test_enter_without_media_fails(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        w = VideoPreviewWidget()
        self.assertFalse(w.enter_crop_mode())
        self.assertFalse(w.is_crop_mode())

    def test_cropbox_drag_works_on_frozen_frame(self):
        w = _mpv_widget()
        w.enter_crop_mode()
        w.video_label.resize(240, 360)
        w._display_frame(_marker())  # recompute display geometry at 1:1
        x, y, bw, bh = w.cropbox
        center = QPoint(
            int(w.display_offset_x + (x + bw / 2) * w.display_scale),
            int(w.display_offset_y + (y + bh / 2) * w.display_scale),
        )
        moved = []
        w.cropbox_changed.connect(lambda *box: moved.append(box))

        def _ev(kind, pos):
            return QMouseEvent(
                kind, QPointF(pos), Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
            )

        w._handle_mouse_press(w.video_label, _ev(QMouseEvent.Type.MouseButtonPress, center))
        self.assertEqual(w.drag_mode, w.DRAG_MOVE)
        w._handle_mouse_move(
            w.video_label,
            _ev(QMouseEvent.Type.MouseMove, center + QPoint(12, 0)),
        )
        w._handle_mouse_release(_ev(QMouseEvent.Type.MouseButtonRelease, center + QPoint(12, 0)))
        self.assertTrue(moved, "drag on the frozen frame must move the cropbox")
        self.assertNotEqual(w.cropbox[0], x)

    def test_mpv_surface_has_no_dead_overlay_hooks(self):
        # The occluded QPainter/mouse path on the native mpv surface is gone;
        # crop interaction lives exclusively on the QLabel page now.
        from gui.widgets.video_preview import _MpvSurface

        self.assertNotIn("paintEvent", _MpvSurface.__dict__)
        self.assertNotIn("mousePressEvent", _MpvSurface.__dict__)


class TimelineCropButtonTests(unittest.TestCase):
    def test_button_emits_toggle_and_setter_is_silent(self):
        from gui.widgets.timeline import TimelineWidget

        t = TimelineWidget()
        hits = []
        t.crop_mode_toggled.connect(lambda: hits.append(1))
        t.btn_crop.click()
        self.assertEqual(hits, [1])
        t.set_crop_mode_checked(True)   # display sync must not re-emit
        self.assertEqual(hits, [1])
        self.assertTrue(t.btn_crop.isChecked())


@unittest.skipUnless(MPV_OK and ENCODE_OK, "mpv / encode toolchain (tools/media) unavailable")
class CropModeRealMpvTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.media_pipeline import MediaEncoder, _quote_vs_string

        cls.d = Path(tempfile.mkdtemp())
        png = cls.d / "m.png"
        cv2.imwrite(str(png), _marker())
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

    def _pump_until(self, cond, timeout_s):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            if cond():
                return True
            time.sleep(0.01)
        return False

    def test_crop_mode_shows_real_frozen_frame(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        w = VideoPreviewWidget()
        self.addCleanup(lambda: w.clear(sync_shutdown=True))
        self.assertTrue(w.load_video(str(self.mp4)))
        self.assertTrue(self._pump_until(lambda: w._mpv_ipc_connected, 20.0))
        self.assertTrue(w.enter_crop_mode())
        self.assertTrue(self._pump_until(w.is_crop_mode, 15.0), "crop mode never engaged")
        self.assertEqual(w._display_stack.currentIndex(), 0)
        self.assertIsNotNone(w.current_frame)
        self.assertGreater(w.current_frame.mean(), 10.0, "frozen frame is black")


if __name__ == "__main__":
    unittest.main()
