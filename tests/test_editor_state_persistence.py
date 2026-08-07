"""S5: editor-state (crop/rotation/in-out) persistence tests.

Old behaviour: crop box, rotation and trim points lived only inside the
preview widgets / plain instance vars — never serialized, never marked the
project dirty, silently lost on close/reopen. They now persist in the
PROJECT epconfig.json under an "editor" key which is stripped from the
exported package (the package epconfig.json is the device contract).
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import types
import unittest

from tests.qt_harness import ensure_app

from config.epconfig import EPConfig, EditorState, EditorTrackState


def setUpModule():
    ensure_app()


class EditorStateModelTests(unittest.TestCase):
    def test_project_dict_roundtrips_editor_state(self):
        cfg = EPConfig()
        cfg.editor.loop = EditorTrackState(
            crop=[10, 20, 300, 400], rotation=90, in_frame=5, out_frame=120
        )
        cfg.editor.intro = EditorTrackState(crop=[0, 0, 100, 200])

        data = cfg.to_dict()  # project save (normalize_paths=False)
        self.assertIn("editor", data)
        restored = EPConfig.from_dict(data)
        self.assertEqual(restored.editor.loop.crop, [10, 20, 300, 400])
        self.assertEqual(restored.editor.loop.rotation, 90)
        self.assertEqual(restored.editor.loop.in_frame, 5)
        self.assertEqual(restored.editor.loop.out_frame, 120)
        self.assertEqual(restored.editor.intro.crop, [0, 0, 100, 200])

    def test_exported_package_dict_strips_editor_state(self):
        cfg = EPConfig()
        cfg.loop.file = "source.mp4"
        cfg.editor.loop = EditorTrackState(crop=[1, 2, 3, 4], rotation=180)
        data = cfg.to_dict(normalize_paths=True)  # package export path
        self.assertNotIn(
            "editor", data,
            "editor state is app-internal and must not enter the device package",
        )

    def test_default_editor_state_is_omitted_from_project_dict(self):
        self.assertNotIn("editor", EPConfig().to_dict())

    def test_legacy_file_without_editor_key_loads_defaults(self):
        restored = EPConfig.from_dict({"version": 1, "loop": {"file": "a.mp4"}})
        self.assertTrue(restored.editor.is_default())

    def test_copy_preserves_editor_state(self):
        cfg = EPConfig()
        cfg.editor.loop = EditorTrackState(crop=[9, 9, 90, 160], rotation=270)
        dup = cfg.copy()
        self.assertEqual(dup.editor.loop.crop, [9, 9, 90, 160])
        self.assertEqual(dup.editor.loop.rotation, 270)

    def test_malformed_editor_payload_degrades_to_defaults(self):
        restored = EPConfig.from_dict(
            {"editor": {"loop": {"crop": "garbage", "rotation": None}}}
        )
        self.assertIsNone(restored.editor.loop.crop)
        self.assertEqual(restored.editor.loop.rotation, 0)


class _FakePreview:
    def __init__(self):
        self.video_path = "loaded.mp4"
        self.total_frames = 100
        self.calls = []
        self.cropbox = (11, 22, 333, 444)
        self.rotation = 90

    def get_cropbox_in_rotated_space(self):
        return self.cropbox

    def get_cropbox(self):
        # The restore path compares this against the requested box to detect
        # an aspect-ratio correction; the real widget re-fits the box.
        return self.cropbox

    def get_rotation(self):
        return self.rotation

    def set_rotation(self, deg):
        self.calls.append(("rotation", deg))

    def set_cropbox(self, *box):
        self.calls.append(("crop", box))
        self.cropbox = tuple(box)


class EditorSyncTests(unittest.TestCase):
    def _window(self):
        from gui.main_window import MainWindow

        w = MainWindow.__new__(MainWindow)
        w._restoring_editor_state = False
        w._editor_sync_suspended = set()
        w._pending_editor_restore = {}
        w._config = EPConfig()
        w.video_preview = _FakePreview()
        w.intro_preview = object()
        w._is_timeline_bound_to = lambda p: False
        w._get_cached_in_out = lambda p: (3, 77)
        w._snapshot_active_timeline_state = lambda: None
        w._is_modified = False
        w._update_title = lambda: None
        w._mark_undo_change = lambda: None  # undo subsystem out of scope here
        return w

    def test_change_writes_config_and_marks_dirty(self):
        from gui.main_window import MainWindow

        w = self._window()
        MainWindow._on_editor_state_changed(w, w.video_preview)
        self.assertEqual(w._config.editor.loop.crop, [11, 22, 333, 444])
        self.assertEqual(w._config.editor.loop.rotation, 90)
        self.assertEqual(
            (w._config.editor.loop.in_frame, w._config.editor.loop.out_frame),
            (3, 77),
        )
        self.assertTrue(w._is_modified, "editing must mark the project dirty")

    def test_suspended_preview_is_not_synced(self):
        from gui.main_window import MainWindow

        w = self._window()
        w._editor_sync_suspended.add(w.video_preview)
        MainWindow._on_editor_state_changed(w, w.video_preview)
        self.assertTrue(w._config.editor.loop.is_default())
        self.assertFalse(w._is_modified)

    def test_restore_applies_rotation_before_crop_and_writes_back(self):
        from gui.main_window import MainWindow

        w = self._window()
        preview = w.video_preview
        saved = EditorTrackState(crop=[5, 6, 70, 80], rotation=90,
                                 in_frame=2, out_frame=50)
        w._pending_editor_restore[preview] = saved
        w._editor_sync_suspended.add(preview)
        w._loop_in_out = (0, 99)
        timeline_calls = []
        w._is_timeline_bound_to = lambda p: True
        w.timeline = types.SimpleNamespace(
            set_in_point=lambda v: timeline_calls.append(("in", v)),
            set_out_point=lambda v: timeline_calls.append(("out", v)),
        )

        MainWindow._finish_editor_restore(w, preview)

        kinds = [kind for kind, _ in preview.calls]
        self.assertEqual(
            kinds, ["rotation", "crop"],
            "set_rotation resets the cropbox, so rotation must be applied first",
        )
        self.assertEqual(w._loop_in_out, (2, 50))
        self.assertIn(("in", 2), timeline_calls)
        self.assertIn(("out", 50), timeline_calls)
        self.assertIs(w._config.editor.loop, saved)  # written back verbatim
        self.assertNotIn(preview, w._editor_sync_suspended)
        self.assertFalse(w._is_modified, "restoring saved state is not an edit")


if __name__ == "__main__":
    unittest.main()
