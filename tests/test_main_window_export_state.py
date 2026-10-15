import tempfile
import unittest
from pathlib import Path

from config.epconfig import EPConfig
from gui.main_window import MainWindow


class MainWindowExportStateTests(unittest.TestCase):
    def test_loop_video_fallback_exports_full_clip_when_timeline_is_unset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            loop_path = root / "loop.mp4"
            loop_path.write_bytes(b"placeholder")

            window = MainWindow.__new__(MainWindow)
            window._base_dir = str(root)
            window._loop_in_out = (0, 0)
            window._config = EPConfig()
            window._config.loop.file = "loop.mp4"
            window.video_preview = object()
            window.intro_preview = object()
            window._snapshot_active_timeline_state = lambda: None
            window._preview_has_loaded_media = lambda preview: False
            window._probe_video_metadata = lambda path: (596, 1280, 1108, 30.000271)

            data = MainWindow._collect_export_data(window)

        params = data["loop_video_params"]
        self.assertEqual(str(loop_path), params.video_path)
        self.assertEqual((0, 0, 596, 1280), params.cropbox)
        self.assertEqual(0, params.start_frame)
        self.assertEqual(1108, params.end_frame)
        self.assertAlmostEqual(30.000271, params.fps)


if __name__ == "__main__":
    unittest.main()
