"""Zoom 功能测试：验证 VapourSynth 图放大、GUI 控件行为、键盘快捷键。"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import math
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
        from core.media_pipeline import MediaEncoder, _quote_vs_string

        cls.d = Path(tempfile.mkdtemp())
        png = cls.d / "src.png"
        # 一张 120x80 的简单图（便于验证放大后尺寸）
        img = np.full((80, 120, 3), 128, np.uint8)
        cv2.imwrite(str(png), img)
        vpy = cls.d / "src.vpy"
        vpy.write_text("\n".join([
            "import vapoursynth as vs", "core = vs.core",
            f"clip = core.imwri.Read({_quote_vs_string(str(png))})",
            "clip = clip if clip.format.id == vs.RGB24 else core.resize.Bicubic(clip, format=vs.RGB24)",
            "clip = core.std.Loop(clip, times=10)",
            "clip = core.resize.Bicubic(clip, width=120, height=80, format=vs.YUV420P8, matrix_s='170m')",
            "clip.set_output()",
        ]) + "\n", encoding="utf-8")
        cls.mp4 = cls.d / "src.mp4"
        MediaEncoder(TC).encode_vpy_to_mp4(str(vpy), str(cls.mp4), 30.0)

        # 带内容的源:上半红、下半蓝 —— 放大错位会立刻暴露
        marker_png = cls.d / "marker.png"
        marker = np.zeros((640, 384, 3), np.uint8)
        marker[:320, :] = (0, 0, 255)   # BGR 红
        marker[320:, :] = (255, 0, 0)   # BGR 蓝
        cv2.imwrite(str(marker_png), marker)
        marker_vpy = cls.d / "marker.vpy"
        marker_vpy.write_text("\n".join([
            "import vapoursynth as vs", "core = vs.core",
            f"clip = core.imwri.Read({_quote_vs_string(str(marker_png))})",
            "clip = clip if clip.format.id == vs.RGB24 else core.resize.Bicubic(clip, format=vs.RGB24)",
            "clip = core.std.Loop(clip, times=10)",
            "clip = core.resize.Bicubic(clip, width=384, height=640, format=vs.YUV420P8, matrix_s='170m')",
            "clip.set_output()",
        ]) + "\n", encoding="utf-8")
        cls.marker_mp4 = cls.d / "marker.mp4"
        MediaEncoder(TC).encode_vpy_to_mp4(str(marker_vpy), str(cls.marker_mp4), 30.0)

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
        from core.vs_graph import apply_preview_zoom, build_source_graph

        viewport = (1000, 1800)
        base = build_source_graph(str(self.mp4), is_image=False, rotation=0)
        for factor in (2.0, 10.0, 100.0):
            with self.subTest(zoom=factor):
                clip = apply_preview_zoom(base, zoom_factor=factor,
                                          viewport=viewport, kernel="Point")
                # 永不超过视口 —— 这正是旧写法违反的那条
                self.assertLessEqual(clip.width, viewport[0])
                self.assertLessEqual(clip.height, viewport[1])

    def test_zoom_preserves_pixel_content(self):
        """放大后内容不能错位:上半仍是红、下半仍是蓝。"""
        from core.vs_graph import apply_preview_zoom, build_source_graph

        base = build_source_graph(str(self.marker_mp4), is_image=False, rotation=0)
        # 2x：视口窗口覆盖足够多的源行，红/蓝分界仍落在窗口中间。
        # (100x 时窗口只有 6 行源像素，整窗都是分界过渡带，测不出方位。)
        clip = apply_preview_zoom(base, zoom_factor=2.0, viewport=(400, 600),
                                  pan=(0.5, 0.5), kernel="Point")
        frame = clip.get_frame(0)
        try:
            r = np.asarray(frame[0]); b = np.asarray(frame[2])
            h, w = r.shape
            top_r, top_b = int(r[h // 8, w // 2]), int(b[h // 8, w // 2])
            bot_r, bot_b = int(r[7 * h // 8, w // 2]), int(b[7 * h // 8, w // 2])
            self.assertGreater(top_r, top_b, f"上半应偏红, got R={top_r} B={top_b}")
            self.assertGreater(bot_b, bot_r, f"下半应偏蓝, got R={bot_r} B={bot_b}")
        finally:
            frame.close()

    def test_zoom_at_1x_returns_the_clip_untouched(self):
        from core.vs_graph import apply_preview_zoom, build_source_graph

        base = build_source_graph(str(self.mp4), is_image=False, rotation=0)
        self.assertIs(apply_preview_zoom(base, zoom_factor=1.0,
                                         viewport=(800, 600)), base)

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
