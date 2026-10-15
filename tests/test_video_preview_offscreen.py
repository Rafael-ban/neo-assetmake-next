"""Offscreen regression tests for the mpv preview reconnect fix (Pb)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import types
import unittest

from tests.qt_harness import ensure_app


def setUpModule():
    ensure_app()


class MpvReconnectTests(unittest.TestCase):
    """Pb: an IPC error AFTER a successful connect (normal mpv quit) must not be
    treated as a failed initial connect (no retry loop, no spurious failure UI)."""

    def _preview_cls(self):
        import gui.widgets.video_preview as vp
        return next(c for c in vars(vp).values()
                    if isinstance(c, type) and hasattr(c, "_on_mpv_ipc_error"))

    def test_post_connect_error_does_not_report_failure(self):
        cls = self._preview_cls()
        holder = types.SimpleNamespace(
            _mpv_socket=None,
            _mpv_process=object(),      # process still alive
            _mpv_ipc_connected=True,    # already connected -> a later error is a disconnect
            _mpv_ipc_attempts=1,
        )
        flags = {"failed": False, "retried": False}
        holder._on_mpv_launch_failed = lambda msg: flags.__setitem__("failed", True)
        holder._try_mpv_ipc_connect = lambda: flags.__setitem__("retried", True)

        cls._on_mpv_ipc_error(holder, None)
        self.assertFalse(flags["failed"], "post-connect error wrongly reported a launch failure")

    def test_initial_connect_failure_still_fails_after_budget(self):
        cls = self._preview_cls()
        from gui.widgets.video_preview import _MPV_IPC_MAX_ATTEMPTS
        holder = types.SimpleNamespace(
            _mpv_socket=None,
            _mpv_process=object(),
            _mpv_ipc_connected=False,
            _mpv_ipc_attempts=_MPV_IPC_MAX_ATTEMPTS,
        )
        flags = {"failed": False}
        holder._on_mpv_launch_failed = lambda msg: flags.__setitem__("failed", True)
        cls._on_mpv_ipc_error(holder, None)
        self.assertTrue(flags["failed"], "exhausted initial connect should report failure")


class MpvPlaybackSyncTests(unittest.TestCase):
    """Pc: early commands are queued (not dropped), and mpv time-pos drives the counter."""

    def _preview_cls(self):
        import gui.widgets.video_preview as vp
        return next(c for c in vars(vp).values()
                    if isinstance(c, type) and hasattr(c, "_send_mpv_command"))

    def test_command_before_connect_is_queued(self):
        cls = self._preview_cls()
        holder = types.SimpleNamespace(_mpv_socket=None, _mpv_process=object(), _pending_mpv_cmds=[])
        cls._send_mpv_command(holder, ["seek", 1.0, "absolute+exact"])
        self.assertEqual(holder._pending_mpv_cmds, [["seek", 1.0, "absolute+exact"]])

    def test_time_pos_property_change_drives_frame_counter(self):
        cls = self._preview_cls()
        emitted = {"idx": None}
        holder = types.SimpleNamespace(video_fps=30.0, total_frames=100, current_frame_index=0)
        holder.frame_changed = types.SimpleNamespace(emit=lambda i: emitted.__setitem__("idx", i))
        holder._update_info_label = lambda: None

        cls._handle_mpv_message(holder, {"event": "property-change", "name": "time-pos", "data": 2.0})
        self.assertEqual(holder.current_frame_index, 60)   # 2.0s * 30fps
        self.assertEqual(emitted["idx"], 60)

        cls._handle_mpv_message(holder, {"event": "property-change", "name": "time-pos", "data": 9999.0})
        self.assertEqual(holder.current_frame_index, 99)   # clamped to total_frames - 1


if __name__ == "__main__":
    unittest.main()
