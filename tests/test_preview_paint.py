"""Focused checks for the preview pixel format and pixmap sizing."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

from tests.qt_harness import ensure_app
from gui.widgets.video_preview import VideoPreviewWidget


class PreviewPaintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_app()

    def setUp(self):
        self.widget = VideoPreviewWidget()
        self.widget.video_label.resize(4, 2)
        self.widget._vs_active = True
        self.addCleanup(self.widget.close)

    def test_bgr_pixels_and_source_ownership_at_native_size(self):
        frame = np.zeros((2, 4, 3), dtype=np.uint8)
        frame[0, 0] = (3, 7, 241)
        self.widget._display_frame(frame)
        pixmap = self.widget.video_label.pixmap()
        self.assertEqual((pixmap.width(), pixmap.height()), (4, 2))
        self.assertEqual(pixmap.toImage().pixelColor(0, 0).getRgb()[:3], (241, 7, 3))
        frame[0, 0] = (250, 250, 250)
        self.assertEqual(pixmap.toImage().pixelColor(0, 0).getRgb()[:3], (241, 7, 3))

    def test_scaled_vs_frame_and_non_vs_fit(self):
        frame = np.zeros((4, 8, 3), dtype=np.uint8)
        self.widget._display_frame(frame)
        pixmap = self.widget.video_label.pixmap()
        self.assertEqual((pixmap.width(), pixmap.height()), (4, 2))

        self.widget._vs_active = False
        self.widget._display_frame(np.zeros((1, 2, 3), dtype=np.uint8))
        pixmap = self.widget.video_label.pixmap()
        self.assertEqual((pixmap.width(), pixmap.height()), (4, 2))

    def test_high_dpi_uses_physical_target_size(self):
        self.widget.video_label.devicePixelRatioF = lambda: 2.0
        captured = []
        self.widget.video_label.setPixmap = captured.append
        self.widget._display_frame(np.zeros((4, 8, 3), dtype=np.uint8))
        pixmap = captured[0]
        self.assertEqual((pixmap.width(), pixmap.height()), (8, 4))
        self.assertEqual(pixmap.devicePixelRatio(), 2.0)

    def test_gray_and_bgra_fallback(self):
        gray = np.full((2, 4), 71, dtype=np.uint8)
        self.widget._display_frame(gray)
        self.assertEqual(
            self.widget.video_label.pixmap().toImage().pixelColor(0, 0).getRgb()[:3],
            (71, 71, 71),
        )
        bgra = np.zeros((2, 4, 4), dtype=np.uint8)
        bgra[0, 0] = (11, 23, 199, 50)
        self.widget._display_frame(bgra)
        self.assertEqual(
            self.widget.video_label.pixmap().toImage().pixelColor(0, 0).getRgb()[:3],
            (199, 23, 11),
        )


if __name__ == "__main__":
    unittest.main()
