"""M4 keeps preview/export pixel parity without loading VS in the Qt parent."""

from __future__ import annotations

import sys
import json
import subprocess
import unittest
from pathlib import Path

import numpy as np

try:
    import cv2

    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from core.media_tools import MediaToolchain
from tests.helpers.vs_isolation import IsolatedVSCase
from tests.qt_harness import ensure_app


REPO = Path(__file__).resolve().parents[1]
M5_CHILD_PROBE = REPO / "tests" / "helpers" / "run_m5_render_case.py"
TOOLCHAIN = MediaToolchain.discover(str(REPO))
VS_OK = (
    (REPO / "tools" / "media" / "vapoursynth.pyd").is_file()
    and sys.version_info >= (3, 12)
)
ENCODE_OK = HAS_CV2 and not TOOLCHAIN.missing_for_export()


def setUpModule():
    ensure_app()


class ParentProcessIsolationTests(IsolatedVSCase):
    def test_parent_does_not_import_vapoursynth_or_vs_engine(self):
        self.assert_parent_has_no_vs()


class PreviewWidgetStateTests(IsolatedVSCase):
    """The M4 widget obtains frames from VSWorkerClient, never a local graph."""

    def _widget(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        widget = VideoPreviewWidget()
        self.addCleanup(widget.clear)
        widget.video_width, widget.video_height = 320, 480
        widget.cropbox = [20, 40, 180, 320]
        widget._vs_active = True
        return widget

    def test_worker_frame_is_untouched_before_display_conversion(self):
        widget = self._widget()
        widget._preview_mode = True
        frame = np.random.randint(0, 255, (640, 384, 3), dtype=np.uint8)
        self.assertIs(widget._make_display_frame(frame), frame)
        self.assert_parent_has_no_vs()

    def test_cropbox_drag_is_disabled_in_preview_mode(self):
        from PyQt6.QtCore import QPoint, QPointF, Qt
        from PyQt6.QtGui import QMouseEvent

        widget = self._widget()
        widget._preview_mode = True
        before = list(widget.cropbox)
        event = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            QPointF(QPoint(50, 50)),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        widget._handle_mouse_press(widget.video_label, event)
        self.assertEqual(widget.drag_mode, widget.DRAG_NONE)
        self.assertEqual(widget.cropbox, before)
        self.assert_parent_has_no_vs()


@unittest.skipUnless(
    VS_OK and ENCODE_OK, "VapourSynth / encode toolchain unavailable"
)
class PreviewMatchesEncodedOutputTests(IsolatedVSCase):
    def test_worker_final_bgr_matches_fixed_runner_encoded_output(self):
        """同一 RenderSession 的 worker final 与 production runner 编码只容许有界色差。"""
        self.assert_parent_has_no_vs()
        completed = subprocess.run(
            [sys.executable, str(M5_CHILD_PROBE), "preview_export_contract"],
            cwd=str(REPO),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
        detail = (
            f"M5 child failed (exit {completed.returncode})\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
        self.assertEqual(completed.returncode, 0, detail)
        self.assertEqual(json.loads(completed.stdout.splitlines()[-1])["status"], "ok")
        self.assert_parent_has_no_vs()


if __name__ == "__main__":
    unittest.main()
