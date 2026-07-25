"""S6.5-6.7: validator gaps, overlay text clipping, intro-duration reconcile."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest

import numpy as np

try:
    import cv2  # noqa: F401
    from PIL import Image  # noqa: F401
    HAS_RENDER = True
except ImportError:
    HAS_RENDER = False

from core.validator import EPConfigValidator, ValidationLevel


def _errors(results):
    return [r for r in results if r.level == ValidationLevel.ERROR]


def _base(**overlay):
    cfg = {
        "version": 1,
        "uuid": "12345678-1234-1234-1234-123456789abc",
        "screen": "360x640",
        "loop": {"file": "loop.mp4"},
    }
    if overlay:
        cfg["overlay"] = overlay
    return cfg


class ValidatorArknightsTests(unittest.TestCase):
    def test_empty_required_arknights_fields_are_errors(self):
        v = EPConfigValidator()
        results = v.validate(_base(type="arknights", options={
            "appear_time": 100000,
            "operator_name": "",
            "operator_code": "",
            "barcode_text": "",
            "color": "#000000",
        }))
        fields = {r.field for r in _errors(results)}
        self.assertIn("overlay.options.operator_name", fields)
        self.assertIn("overlay.options.operator_code", fields)
        self.assertIn("overlay.options.barcode_text", fields)

    def test_populated_arknights_fields_pass(self):
        v = EPConfigValidator()
        results = v.validate(_base(type="arknights", options={
            "appear_time": 100000,
            "operator_name": "AMIYA",
            "operator_code": "RHODES-001",
            "barcode_text": "AMIYA - ARKNIGHTS",
            "color": "#000000",
        }))
        overlay_errs = [r for r in _errors(results) if r.field.startswith("overlay")]
        self.assertEqual(overlay_errs, [])


class ValidatorImageOverlayTests(unittest.TestCase):
    def test_zero_duration_is_not_flagged(self):
        # duration == 0 is the documented "show indefinitely" default.
        v = EPConfigValidator()
        results = v.validate(_base(type="image", options={
            "appear_time": 100000, "duration": 0, "image": "overlay.png",
        }))
        dur = [r for r in results if r.field == "overlay.options.duration"]
        self.assertEqual(dur, [], "duration=0 must not warn (it means infinite)")

    def test_negative_duration_is_error(self):
        v = EPConfigValidator()
        results = v.validate(_base(type="image", options={
            "appear_time": 100000, "duration": -5, "image": "overlay.png",
        }))
        self.assertTrue(
            any(r.field == "overlay.options.duration" and r.level == ValidationLevel.ERROR
                for r in results)
        )


@unittest.skipUnless(HAS_RENDER, "cv2/PIL required for overlay rendering")
class OverlayTextClipTests(unittest.TestCase):
    def test_text_near_edge_is_partially_blended_not_dropped(self):
        from core.overlay_renderer import OverlayRenderer

        renderer = OverlayRenderer()
        frame = np.zeros((120, 120, 3), np.uint8)
        # The rotated text box at y=90 overruns the bottom edge — the old
        # shape-equality guard skipped the whole blend and the text vanished.
        renderer._draw_rotated_text(
            frame, "ABCD", x=20, y=90, width=60, height=40,
            font_scale=30.0, color_rgb=(255, 255, 255),
        )
        self.assertGreater(
            frame.sum(), 0,
            "text overrunning the frame edge must still blend its visible part",
        )
        # And nothing beyond the frame was touched (no out-of-bounds wrap).
        self.assertEqual(frame.shape, (120, 120, 3))

    def test_fully_offscreen_text_is_a_noop(self):
        from core.overlay_renderer import OverlayRenderer

        renderer = OverlayRenderer()
        frame = np.zeros((40, 40, 3), np.uint8)
        renderer._draw_rotated_text(
            frame, "X", x=200, y=200, width=20, height=20,
            font_scale=16.0, color_rgb=(255, 255, 255),
        )
        self.assertEqual(frame.sum(), 0)


class IntroDurationReconcileTests(unittest.TestCase):
    def test_export_reconciles_intro_duration_to_trim_length(self):
        from gui.main_window import MainWindow
        from config.epconfig import EPConfig

        w = MainWindow.__new__(MainWindow)
        w._config = EPConfig()
        w._config.intro.enabled = True
        w._config.intro.file = "intro.mp4"
        w._config.intro.duration = 5_000_000  # user left the 5s default
        w._base_dir = ""
        w._is_modified = False
        w._update_title = lambda: None
        w.status_bar = type("S", (), {"showMessage": lambda *a: None})()
        w._snapshot_active_timeline_state = lambda: None
        w.video_preview = object()
        w.intro_preview = object()
        w._collect_arknights_custom_images = lambda: []

        def fake_state(preview, path, **k):
            if preview is w.intro_preview:
                return {
                    "path": "/x/intro.mp4", "cropbox": (0, 0, 100, 200),
                    "rotation": 0, "start_frame": 0, "end_frame": 60,  # 2s @ 30fps
                    "fps": 30.0, "total_frames": 60, "width": 100, "height": 200,
                }
            return None

        w._collect_preview_media_state = fake_state

        data = MainWindow._collect_export_data(w)
        self.assertIn("intro_video_params", data)
        # 60 frames / 30 fps = 2.0s -> 2_000_000 µs, not the stale 5_000_000.
        self.assertEqual(w._config.intro.duration, 2_000_000)
        self.assertTrue(w._is_modified)


if __name__ == "__main__":
    unittest.main()
