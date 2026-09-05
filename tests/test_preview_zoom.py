"""Zoom 功能测试：验证 VapourSynth 图放大、GUI 控件行为、键盘快捷键。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import math
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from tests.qt_harness import ensure_app
from PyQt6.QtCore import Qt

from core.media_tools import MediaToolchain

REPO = Path(__file__).resolve().parent.parent
TC = MediaToolchain.discover(str(REPO))
ENCODE_OK = HAS_CV2 and not TC.missing_for_export()


def setUpModule():
    ensure_app()


@unittest.skipUnless(ENCODE_OK, "encode toolchain (tools/media) unavailable")
class PreviewZoomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.helpers.m5_render_fixture import (
            build_default_render_session,
            encode_render_session,
        )

        cls.d = Path(tempfile.mkdtemp())
        png = cls.d / "src.png"
        # 一张 120x80 的简单图（便于验证放大后尺寸）
        img = np.full((80, 120, 3), 128, np.uint8)
        cv2.imwrite(str(png), img)
        cls.mp4 = cls.d / "src.mp4"
        session = build_default_render_session(
            cls.d / "src-session",
            source_path=png,
            source_kind="image",
            end_frame=10,
        )
        encode_render_session(TC, session, cls.mp4)

        # 带内容的源:上半红、下半蓝 —— 放大错位会立刻暴露
        marker_png = cls.d / "marker.png"
        marker = np.zeros((640, 384, 3), np.uint8)
        marker[:320, :] = (0, 0, 255)   # BGR 红
        marker[320:, :] = (255, 0, 0)   # BGR 蓝
        cv2.imwrite(str(marker_png), marker)
        cls.marker_mp4 = cls.d / "marker.mp4"
        marker_session = build_default_render_session(
            cls.d / "marker-session",
            source_path=marker_png,
            source_kind="image",
            end_frame=10,
        )
        encode_render_session(TC, marker_session, cls.marker_mp4)

    @staticmethod
    def _pump_until(condition, timeout=20.0):
        from PyQt6.QtCore import QCoreApplication

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            if condition():
                return True
            time.sleep(0.01)
        return False

    def _load_real(self, path):
        from gui.widgets.video_preview import VideoPreviewWidget

        widget = VideoPreviewWidget()
        self.addCleanup(lambda: widget.clear(sync_shutdown=True))
        self.assertTrue(widget.load_video(str(path)))
        self.assertTrue(
            self._pump_until(lambda: widget.current_frame is not None),
            "worker 未返回首帧",
        )
        return widget

    def test_initial_zoom_is_100_percent(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        w = VideoPreviewWidget()
        self.assertEqual(w.get_zoom_factor(), 1.0)
        self.assertEqual(w.zoom_slider.value(), 0)
        self.assertEqual(w.zoom_label.text(), "100%")

    def test_zoom_slider_changes_factor_logarithmically(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        w = VideoPreviewWidget()
        # slider=100 → 10^(100/100) = 10.0x
        w.zoom_slider.setValue(100)
        self.assertAlmostEqual(w.get_zoom_factor(), 10.0, places=2)
        self.assertEqual(w.zoom_label.text(), "1000%")

        # slider=200 → 10^(200/100) = 100.0x
        w.zoom_slider.setValue(200)
        self.assertAlmostEqual(w.get_zoom_factor(), 100.0, places=2)
        self.assertEqual(w.zoom_label.text(), "10000%")

    def test_quick_zoom_buttons_set_exact_percentages(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        w = VideoPreviewWidget()
        w._set_zoom_percent(1000)
        self.assertAlmostEqual(w.get_zoom_factor(), 10.0, places=2)
        self.assertEqual(w.zoom_slider.value(), 100)

        w._set_zoom_percent(10000)
        self.assertAlmostEqual(w.get_zoom_factor(), 100.0, places=2)
        self.assertEqual(w.zoom_slider.value(), 200)

    def test_set_zoom_factor_api(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        w = VideoPreviewWidget()
        w.set_zoom_factor(5.0)
        # Slider round-trip: 5.0 → slider=70 → 10^0.70 ≈ 5.01, tolerance needed
        self.assertAlmostEqual(w.get_zoom_factor(), 5.0, delta=0.15)
        # 5.0x → log10(5)=0.699 → slider≈70
        self.assertAlmostEqual(w.zoom_slider.value(), 70, delta=1)

    def test_zoom_out_of_range_raises(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        w = VideoPreviewWidget()
        with self.assertRaises(ValueError):
            w.set_zoom_factor(0.005)  # < 0.01
        with self.assertRaises(ValueError):
            w.set_zoom_factor(150.0)  # > 100.0

    def test_zoom_cost_is_flat_because_only_the_viewport_is_rendered(self):
        """放大是"视口放大镜",不是整帧 resize。

        旧写法(整帧 resize)实测:100x → 38400x64000 = 7.37 GB/帧、取帧 9.3 秒,
        而视口最多只能显示约 1000x1800。新写法先 CropAbs 出视口窗口再放大,
        成本与倍率无关。
        """
        widget = self._load_real(self.mp4)
        viewport = widget._viewport_size()
        for factor in (0.01, 1.0, 100.0):
            with self.subTest(zoom=factor):
                previous = widget.current_frame
                widget.current_frame = None
                widget.set_zoom_factor(factor)
                if factor == 1.0:
                    widget._request_current_frame()
                self.assertTrue(
                    self._pump_until(lambda: widget.current_frame is not None),
                    f"zoom={factor} 未返回帧",
                )
                height, width = widget.current_frame.shape[:2]
                self.assertLessEqual(width, viewport[0])
                self.assertLessEqual(height, viewport[1])
                self.assertIsNot(widget.current_frame, previous)

    def test_zoom_preserves_pixel_content(self):
        """放大后内容不能错位:上半仍是红、下半仍是蓝。"""
        widget = self._load_real(self.marker_mp4)
        widget.current_frame = None
        widget.set_zoom_factor(2.0)
        self.assertTrue(self._pump_until(lambda: widget.current_frame is not None))
        frame = widget.current_frame
        h, w = frame.shape[:2]
        top_b, _top_g, top_r = (int(v) for v in frame[h // 8, w // 2])
        bot_b, _bot_g, bot_r = (int(v) for v in frame[7 * h // 8, w // 2])
        self.assertGreater(top_r, top_b, f"上半应偏红, got R={top_r} B={top_b}")
        self.assertGreater(bot_b, bot_r, f"下半应偏蓝, got R={bot_r} B={bot_b}")

    def test_zoom_at_1x_returns_the_clip_untouched(self):
        widget = self._load_real(self.mp4)
        self.assertEqual(widget.get_zoom_factor(), 1.0)
        self.assertIsNotNone(widget.current_frame)
        self.assertNotIn("vapoursynth", __import__("sys").modules)

    def test_cropbox_is_locked_while_zoomed(self):
        """放大后 display_scale 不再对应源坐标,画框与拖拽都必须停手。"""
        from gui.widgets.video_preview import VideoPreviewWidget
        from PyQt6.QtCore import QPointF
        from PyQt6.QtGui import QMouseEvent

        w = VideoPreviewWidget()
        w._has_video = True
        w.video_width, w.video_height = 120, 80
        w._init_cropbox()
        before = list(w.cropbox)
        w.set_zoom_factor(10.0)

        centre = QPointF(w.display_offset_x + before[0] + before[2] / 2,
                         w.display_offset_y + before[1] + before[3] / 2)
        press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, centre,
                            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
        w._handle_mouse_press(w.video_label, press)
        self.assertEqual(w.drag_mode, w.DRAG_NONE, "放大时不得进入拖拽")
        self.assertEqual(list(w.cropbox), before)

    def test_keyboard_shortcuts_adjust_zoom(self):
        from gui.widgets.video_preview import VideoPreviewWidget
        from PyQt6.QtGui import QKeyEvent
        from PyQt6.QtCore import Qt, QEvent

        w = VideoPreviewWidget()
        # 快捷键需要 widget 拥有焦点且处于"有媒体"状态
        w._has_video = True
        w.setFocus()
        initial = w.zoom_slider.value()

        # Ctrl+= (zoom in)
        evt = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Equal, Qt.KeyboardModifier.ControlModifier)
        w.keyPressEvent(evt)
        self.assertEqual(w.zoom_slider.value(), initial + 10)

        # Ctrl+- (zoom out)
        evt = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Minus, Qt.KeyboardModifier.ControlModifier)
        w.keyPressEvent(evt)
        self.assertEqual(w.zoom_slider.value(), initial)

        # Ctrl+0 (reset to 100%)
        w.zoom_slider.setValue(100)
        evt = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_0, Qt.KeyboardModifier.ControlModifier)
        w.keyPressEvent(evt)
        self.assertEqual(w.zoom_slider.value(), 0)
        self.assertEqual(w.get_zoom_factor(), 1.0)


if __name__ == "__main__":
    unittest.main()
