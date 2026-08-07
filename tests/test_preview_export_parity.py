"""Stage 3: 预览与导出共用同一张图。

旧写法无效:预览模式下 ``_make_display_frame`` 用 cv2 重新裁剪 + ``cv2.resize``
近似导出结果 —— 换了重采样器(cv2 双线性 vs VapourSynth Bicubic)、丢了 mod16
补边、也丢了导出末尾的 180° 翻转,所以"看到的"从来不等于"导出的"。

新写法有效:预览模式直接渲染 ``build_display_graph(导出参数)``,即**真实导出图**
回转 RGB;``_make_display_frame`` 在 VS 路径上原样返回。本文件钉死这个契约:
widget 交付的帧 == 导出图的帧,且与真实编码出的 mp4 在几何/色彩上一致。
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
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

from core import vs_engine
from core.media_tools import MediaToolchain

REPO = Path(__file__).resolve().parents[1]
TC = MediaToolchain.discover(str(REPO))
VS_OK = vs_engine._core is not None and not vs_engine.missing_plugins()
ENCODE_OK = HAS_CV2 and not TC.missing_for_export()


def setUpModule():
    ensure_app()


def _quadrants(w=320, h=480):
    """四色象限:任何裁剪/缩放/补边/翻转错误都会显形。"""
    img = np.zeros((h, w, 3), np.uint8)
    img[: h // 2, : w // 2] = (255, 0, 0)
    img[: h // 2, w // 2:] = (0, 255, 0)
    img[h // 2:, : w // 2] = (0, 0, 255)
    img[h // 2:, w // 2:] = (255, 255, 0)
    return img


@unittest.skipUnless(VS_OK, "VapourSynth 核心未预热 / 缺插件")
class PreviewGraphIsTheExportGraphTests(unittest.TestCase):
    """widget 在预览模式下取的 clip,必须就是导出图。"""

    @classmethod
    def setUpClass(cls):
        cls.d = Path(tempfile.mkdtemp())
        cls.png = cls.d / "q.png"
        if HAS_CV2:
            cv2.imwrite(str(cls.png), _quadrants())

    def _widget(self, *, rotation=0, cropbox=(20, 40, 180, 320)):
        from gui.widgets.video_preview import VideoPreviewWidget

        w = VideoPreviewWidget()
        self.addCleanup(w.clear)
        w.video_path = str(self.png)
        w.video_width, w.video_height = 320, 480
        w.video_fps, w.total_frames = 30.0, 10
        w.cropbox = list(cropbox)
        w._rotation = rotation
        w._vs_active = True
        return w

    @unittest.skipUnless(HAS_CV2, "cv2 unavailable")
    def test_preview_mode_clip_equals_export_display_graph(self):
        from core.vs_frame import request_bgr_frame
        from core.vs_graph import build_display_graph

        w = self._widget(rotation=90)
        w._preview_mode = True
        mine = request_bgr_frame(w._build_preview_clip(), 0)
        theirs = request_bgr_frame(build_display_graph(w._build_export_params()), 0)
        self.assertIsNotNone(mine)
        np.testing.assert_array_equal(
            mine, theirs,
            "预览模式必须逐字节等于导出图,不能是近似",
        )

    @unittest.skipUnless(HAS_CV2, "cv2 unavailable")
    def test_edit_mode_clip_is_the_rotated_source(self):
        w = self._widget(rotation=90)
        w._preview_mode = False
        clip = w._build_preview_clip()
        # 90° 后源变为 480x320:编辑模式给出整幅源图,裁剪框叠在上面。
        self.assertEqual((clip.width, clip.height), (480, 320))

    @unittest.skipUnless(HAS_CV2, "cv2 unavailable")
    def test_export_params_mirror_the_widget_state(self):
        w = self._widget(rotation=180, cropbox=(4, 8, 120, 213))
        p = w._build_export_params()
        self.assertEqual(p.video_path, str(self.png))
        self.assertEqual(p.cropbox, (4, 8, 120, 213))
        self.assertEqual(p.rotation, 180)
        self.assertEqual(p.resolution, f"{w.target_width}x{w.target_height}")
        self.assertTrue(p.is_image, ".png 必须走图片循环分支")

    def test_display_frame_is_untouched_on_the_vs_path(self):
        """VS 路径上不得再用 cv2 二次加工(那正是旧的近似来源)。"""
        w = self._widget()
        w._preview_mode = True
        frame = np.random.randint(0, 255, (640, 384, 3), dtype=np.uint8)
        out = w._make_display_frame(frame)
        self.assertIs(out, frame)

    def test_cropbox_drag_is_disabled_in_preview_mode(self):
        from PyQt6.QtCore import QPoint, QPointF, Qt
        from PyQt6.QtGui import QMouseEvent

        w = self._widget()
        w._preview_mode = True
        before = list(w.cropbox)
        ev = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress, QPointF(QPoint(50, 50)),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        w._handle_mouse_press(w.video_label, ev)
        self.assertEqual(w.drag_mode, w.DRAG_NONE, "预览模式不应开始拖拽")
        self.assertEqual(w.cropbox, before)


@unittest.skipUnless(VS_OK and ENCODE_OK, "VapourSynth / 编码工具链不可用")
class PreviewMatchesEncodedOutputTests(unittest.TestCase):
    """端到端:屏幕上的帧 vs 真实编码出来的 mp4。"""

    @classmethod
    def setUpClass(cls):
        cls.d = Path(tempfile.mkdtemp())
        cls.png = cls.d / "q.png"
        cv2.imwrite(str(cls.png), _quadrants())

    def _params(self, **kw):
        from core.export_service import VideoExportParams

        base = dict(video_path=str(self.png), cropbox=(20, 40, 180, 320),
                    start_frame=0, end_frame=10, fps=30.0,
                    resolution="360x640", is_image=True, rotation=90)
        base.update(kw)
        return VideoExportParams(**base)

    def _preview_and_encoded(self, params, tag):
        from core.media_pipeline import MediaEncoder
        from core.vs_frame import request_bgr_frame
        from core.vs_graph import build_display_graph
        from core.vs_script import write_vpy_script

        preview = request_bgr_frame(build_display_graph(params), 0)
        self.assertIsNotNone(preview, "预览取帧失败")
        vpy = self.d / f"{tag}.vpy"
        write_vpy_script(vpy, params)
        mp4 = self.d / f"{tag}.mp4"
        MediaEncoder(TC).encode_vpy_to_mp4(str(vpy), str(mp4), params.fps)
        cap = cv2.VideoCapture(str(mp4))
        try:
            ok, encoded = cap.read()
        finally:
            cap.release()
        self.assertTrue(ok, "编码产物解码失败")
        return preview, encoded

    def test_geometry_and_colour_match_the_encoded_frame(self):
        preview, encoded = self._preview_and_encoded(self._params(), "rot90")
        self.assertEqual(preview.shape, encoded.shape,
                         "几何不一致:补边/翻转没走同一条链")
        diff = np.abs(preview.astype(int) - encoded.astype(int))
        # 4:2:0 色度抽样 + x264 8bit 是有损的,差异只允许出现在硬边界上;
        # 旧的 cv2 近似连尺寸都对不上,更不可能落在这个范围里。
        self.assertLess(diff.mean(), 2.0, f"整体色彩偏移过大: {diff.mean():.2f}")
        self.assertLess((diff.max(axis=2) > 30).mean(), 0.01,
                        "超出色度抽样可解释范围的像素太多")

    def test_flat_region_is_bit_exact(self):
        """无硬边界时,预览与编码输出应逐字节相同(排除构图分歧)。"""
        flat = self.d / "flat.png"
        cv2.imwrite(str(flat), np.full((480, 320, 3), 128, np.uint8))
        preview, encoded = self._preview_and_encoded(
            self._params(video_path=str(flat)), "flat")
        np.testing.assert_array_equal(preview, encoded)

    def test_padding_is_present_in_both(self):
        preview, encoded = self._preview_and_encoded(
            self._params(rotation=0), "rot0")
        # 360 目标宽被补到 mod16 = 384,预览必须一起补,否则就是两条链。
        self.assertEqual(preview.shape[:2], (640, 384))
        self.assertEqual(encoded.shape[:2], (640, 384))


if __name__ == "__main__":
    unittest.main()
