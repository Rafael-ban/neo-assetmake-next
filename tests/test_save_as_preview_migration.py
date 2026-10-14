"""Save As must move active preview jobs to the saved project root."""

import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from config.epconfig import CONFIG_FILENAME, EPConfig
from gui.main_window import MainWindow
from tests.qt_harness import ensure_app


def setUpModule():
    ensure_app()


class _Preview:
    def __init__(self, path):
        self.video_path = str(path)
        self.current_frame_index = 23
        self.is_playing = False
        self.render_root = None
        self.reloads = []

    def current_render_session(self):
        return object() if self.video_path else None

    def reload_rendering(self):
        self.reloads.append((self.render_root, self.video_path))


class SaveAsPreviewMigrationTests(unittest.TestCase):
    def _window(self, source_root, source_media, *, temporary):
        window = MainWindow.__new__(MainWindow)
        window._config = EPConfig()
        window._config.loop.file = source_media.name
        window._config.editor.loop.out_frame = 48
        window._base_dir = str(source_root)
        window._project_path = "" if temporary else str(source_root / CONFIG_FILENAME)
        window._temp_dir = str(source_root) if temporary else None
        window._is_modified = True
        window.video_preview = _Preview(source_media)
        window.intro_preview = _Preview("")
        window.advanced_config_panel = types.SimpleNamespace(set_config=lambda *_: None)
        window.json_preview = types.SimpleNamespace(set_config=lambda *_: None)
        window.status_bar = types.SimpleNamespace(showMessage=lambda *_: None)
        window._start_auto_save = lambda: None
        window._update_title = lambda: None

        def configure():
            for preview in (window.video_preview, window.intro_preview):
                preview.render_root = window._base_dir

        window._configure_preview_render_contexts = configure
        return window

    def _save_as(self, window, target):
        with mock.patch(
            "gui.main_window.QFileDialog.getSaveFileName",
            return_value=(str(target), ""),
        ):
            MainWindow._on_save_as(window)

    def test_temporary_project_rebinds_media_and_session_after_move(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / "temporary"
            target = root / "saved"
            source.mkdir()
            target.mkdir()
            media = source / "loop.mp4"
            media.write_bytes(b"media")
            window = self._window(source, media, temporary=True)

            self._save_as(window, target / CONFIG_FILENAME)

            saved_media = target / media.name
            self.assertTrue(saved_media.is_file())
            self.assertEqual(window.video_preview.video_path, str(saved_media))
            self.assertEqual(
                window.video_preview.reloads, [(str(target), str(saved_media))]
            )
            self.assertEqual(window.video_preview.current_frame_index, 23)
            self.assertFalse(window.video_preview.is_playing)
            self.assertEqual(window._config.editor.loop.out_frame, 48)
            self.assertEqual(
                EPConfig.load_from_file(str(target / CONFIG_FILENAME)).loop.file,
                media.name,
            )

    def test_existing_project_rebases_relative_media_for_new_root(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / "original"
            target = root / "saved"
            source.mkdir()
            target.mkdir()
            media = source / "loop.mp4"
            media.write_bytes(b"media")
            window = self._window(source, media, temporary=False)
            window.video_preview.is_playing = True

            self._save_as(window, target / CONFIG_FILENAME)

            saved = EPConfig.load_from_file(str(target / CONFIG_FILENAME))
            self.assertEqual(saved.loop.file, str(media))
            self.assertEqual(window.video_preview.reloads, [(str(target), str(media))])
            self.assertTrue(window.video_preview.is_playing)
            self.assertEqual(window.video_preview.current_frame_index, 23)

    def test_temporary_project_keeps_external_media_on_another_drive(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / "temporary"
            target = root / "saved"
            external = root / "external"
            for directory in (source, target, external):
                directory.mkdir()
            (source / "loop.mp4").write_bytes(b"project copy")
            media = external / "loop.mp4"
            media.write_bytes(b"external source")
            window = self._window(source, media, temporary=True)
            window.video_preview.is_playing = True

            with mock.patch(
                "gui.main_window.os.path.relpath",
                side_effect=ValueError("path is on drive D:, start on drive C:"),
            ):
                self._save_as(window, target / CONFIG_FILENAME)

            self.assertEqual(window.video_preview.video_path, str(media))
            self.assertEqual(window.video_preview.reloads, [(str(target), str(media))])
            self.assertTrue(window.video_preview.is_playing)
            self.assertEqual(window.video_preview.current_frame_index, 23)


if __name__ == "__main__":
    unittest.main()
