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
        window._pending_temp_cleanup = set()
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


    def test_temporary_project_copies_nested_media_and_script_without_cache(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source, target = root / "temporary", root / "saved"
            (source / "media").mkdir(parents=True)
            (source / "scripts" / "helpers").mkdir(parents=True)
            (source / ".assetmaker-vs" / "rife-cache").mkdir(parents=True)
            target.mkdir()
            media = source / "media" / "loop.mp4"
            media.write_bytes(b"media")
            (source / "scripts" / "pipeline.vpy").write_text("# script")
            (source / "scripts" / "helpers" / "effect.py").write_text("# helper")
            (source / ".assetmaker-vs" / "rife-cache" / "frame.rgb").write_bytes(b"cache")
            window = self._window(source, media, temporary=True)
            window._config.loop.file = str(media)
            window._config.editor.vs_script.source = "project"
            window._config.editor.vs_script.path = "scripts/pipeline.vpy"

            self._save_as(window, target / CONFIG_FILENAME)

            self.assertEqual((target / "media" / "loop.mp4").read_bytes(), b"media")
            self.assertTrue((target / "scripts" / "helpers" / "effect.py").is_file())
            self.assertFalse((target / ".assetmaker-vs").exists())
            self.assertEqual(window.video_preview.video_path, str(target / "media" / "loop.mp4"))
            saved = EPConfig.load_from_file(str(target / CONFIG_FILENAME))
            self.assertEqual(saved.loop.file, str(target / "media" / "loop.mp4"))
            self.assertEqual(saved.editor.vs_script.path, "scripts/pipeline.vpy")

    def test_temporary_save_failure_keeps_source_and_original_project_state(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source, target = root / "temporary", root / "saved"
            source.mkdir()
            target.mkdir()
            media = source / "loop.mp4"
            media.write_bytes(b"media")
            window = self._window(source, media, temporary=True)
            window._config.loop.file = str(media)
            with mock.patch.object(window._config, "save_to_file", side_effect=OSError("save failed")), mock.patch(
                "gui.main_window.show_error"
            ):
                self._save_as(window, target / CONFIG_FILENAME)
            self.assertTrue(media.is_file())
            self.assertEqual(window._temp_dir, str(source))
            self.assertEqual(window._base_dir, str(source))
            self.assertEqual(window._config.loop.file, str(media))

    def test_temporary_conflicting_target_preserves_source(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source, target = root / "temporary", root / "saved"
            source.mkdir()
            target.mkdir()
            media = source / "loop.mp4"
            media.write_bytes(b"source")
            (target / "loop.mp4").write_bytes(b"different")
            window = self._window(source, media, temporary=True)

            with mock.patch("gui.main_window.show_error"):
                self._save_as(window, target / CONFIG_FILENAME)

            self.assertTrue(media.is_file())
            self.assertEqual((target / "loop.mp4").read_bytes(), b"different")
            self.assertFalse((target / CONFIG_FILENAME).exists())
            self.assertEqual(window._base_dir, str(source))


    def test_temporary_copy_failure_can_retry_without_losing_source(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source, target = root / "temporary", root / "saved"
            source.mkdir()
            target.mkdir()
            (source / "first.txt").write_text("first")
            (source / "second.txt").write_text("second")
            window = self._window(source, source / "first.txt", temporary=True)
            real_copy = __import__("shutil").copy2
            copied = 0

            def fail_second_copy(src, dst):
                nonlocal copied
                copied += 1
                if copied == 2:
                    raise OSError("copy failed")
                return real_copy(src, dst)

            with mock.patch("gui.main_window.shutil.copy2", side_effect=fail_second_copy), mock.patch(
                "gui.main_window.show_error"
            ):
                self._save_as(window, target / CONFIG_FILENAME)

            self.assertTrue((source / "first.txt").is_file())
            self.assertTrue((source / "second.txt").is_file())
            self.assertEqual(window._base_dir, str(source))
            self.assertFalse((target / CONFIG_FILENAME).exists())
            self.assertEqual(len(list(target.glob("*.txt"))), 1)

            self._save_as(window, target / CONFIG_FILENAME)

            self.assertEqual((target / "first.txt").read_text(), "first")
            self.assertEqual((target / "second.txt").read_text(), "second")
            self.assertTrue((target / CONFIG_FILENAME).is_file())
            self.assertFalse(source.exists())
            self.assertEqual(window._base_dir, str(target))


if __name__ == "__main__":
    unittest.main()
