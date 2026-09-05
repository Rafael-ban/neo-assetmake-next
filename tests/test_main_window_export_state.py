import tempfile
import unittest
from unittest import mock
from pathlib import Path

from config.epconfig import EPConfig
from gui.main_window import MainWindow


class MainWindowExportStateTests(unittest.TestCase):
    def test_loop_video_export_uses_preview_frozen_session(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            loop_path = root / "loop.mp4"
            loop_path.write_bytes(b"placeholder")

            window = MainWindow.__new__(MainWindow)
            window._base_dir = str(root)
            window._config = EPConfig()
            window._config.loop.file = "loop.mp4"
            session = object()
            window.video_preview = mock.Mock()
            window.video_preview.flush_render_job.return_value = session
            window.intro_preview = mock.Mock()
            window._snapshot_active_timeline_state = lambda: None

            data = MainWindow._collect_export_data(window)

        window.video_preview.flush_render_job.assert_called_once_with()
        self.assertIs(data["loop_render_session"], session)
        self.assertNotIn("loop_video_params", data)


if __name__ == "__main__":
    unittest.main()
