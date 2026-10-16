"""Temporary projects stay beside the app and clean up after worker shutdown."""
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.qt_harness import ensure_app


def setUpModule():
    ensure_app()


class TempProjectStorageTests(unittest.TestCase):
    def _window(self):
        from gui.main_window import MainWindow
        window = MainWindow.__new__(MainWindow)
        window._pending_temp_cleanup = set()
        return window

    def test_unsaved_project_and_cache_are_under_app_root(self):
        window = self._window()
        with tempfile.TemporaryDirectory() as root:
            window._app_dir = root
            for name in ("advanced_config_panel", "json_preview", "video_preview", "status_bar"):
                setattr(window, name, Mock())
            window._configure_preview_render_contexts = Mock()
            window._update_title = Mock()
            window._start_auto_save = Mock()
            window._reset_undo_history = Mock()
            window._init_temp_project()
            project = Path(window._temp_dir)
            self.assertEqual(project.parent, Path(root) / ".assetmaker-work" / "projects")
            self.assertEqual(window._base_dir, str(project))
            window._cleanup_temp_dir()
            self.assertFalse(project.exists())

    def test_locked_project_remains_retryable_without_scanning_other_projects(self):
        window = self._window()
        with tempfile.TemporaryDirectory() as root:
            project = Path(root) / "owned"
            other = Path(root) / "recoverable-other"
            project.mkdir()
            other.mkdir()
            window._temp_dir = str(project)
            with patch("gui.main_window.shutil.rmtree", side_effect=PermissionError("locked")):
                window._cleanup_temp_dir()
            self.assertIsNone(window._temp_dir)
            self.assertIn(str(project), window._pending_temp_cleanup)
            # The old directory remains retryable after switching to a saved project.
            window._cleanup_temp_dir()
            self.assertFalse(project.exists())
            self.assertTrue(other.exists())
            self.assertFalse(window._pending_temp_cleanup)

    def test_shutdown_releases_workers_before_removing_project(self):
        window = self._window()
        events = []
        window._ensure_render_state_committed = Mock()
        window._auto_save_service = types.SimpleNamespace(stop=lambda: events.append("autosave"))
        for name in ("video_preview", "intro_preview", "frame_capture_preview"):
            setattr(window, name, types.SimpleNamespace(
                clear=lambda **kwargs: events.append(("preview", kwargs))))
        window._cleanup_temp_dir = lambda: events.append("cleanup")
        window._shutdown_runtime_resources()
        self.assertEqual(events[0], "autosave")
        self.assertEqual(events[-1], "cleanup")
        self.assertEqual(events[1:-1], [("preview", {"sync_shutdown": True})] * 3)

    def test_save_migration_excludes_runtime_cache_and_recovery(self):
        window = self._window()
        with tempfile.TemporaryDirectory() as root:
            source, destination = Path(root) / "source", Path(root) / "saved"
            source.mkdir()
            for directory in (".assetmaker-vs", ".autosave", ".recovery"):
                (source / directory).mkdir()
                (source / directory / "cache.bin").write_bytes(b"cache")
            (source / "media.bin").write_bytes(b"media")
            window._temp_dir = str(source)
            window._migrate_temp_to_permanent(str(destination))
            self.assertEqual((destination / "media.bin").read_bytes(), b"media")
            self.assertEqual(sorted(p.name for p in destination.iterdir()), ["media.bin"])
