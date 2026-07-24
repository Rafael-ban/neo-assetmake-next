"""Unit tests for mpv JSON-IPC request/reply correlation in the preview widget (S1).

mpv echoes ``request_id`` in every command reply together with an ``error``
field (mpv manual, "JSON IPC"). The old code sent bare ``{"command": ...}``
payloads and ignored all replies, so results (screenshots) and failures were
silently dropped.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
import unittest

import numpy as np

from tests.qt_harness import ensure_app
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalSocket


def setUpModule():
    ensure_app()


class _FakeSocket(QObject):
    """Connected-looking socket that records written IPC payloads."""

    readyRead = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.written: list[bytes] = []

    def state(self):
        return QLocalSocket.LocalSocketState.ConnectedState

    def write(self, data):
        self.written.append(bytes(data))
        return len(bytes(data))

    def waitForBytesWritten(self, _ms):
        return True

    def last_payload(self) -> dict:
        return json.loads(self.written[-1].decode("utf-8"))


def _make_widget():
    from gui.widgets.video_preview import VideoPreviewWidget

    w = VideoPreviewWidget()
    w._mpv_socket = _FakeSocket()
    w._mpv_process = object()  # anything non-None: "an mpv session exists"
    w._mpv_ipc_connected = True
    return w


class MpvIpcReplyTests(unittest.TestCase):
    def test_commands_carry_incrementing_request_ids(self):
        w = _make_widget()
        w._send_mpv_command(["get_property", "pause"])
        w._send_mpv_command(["seek", 1.0, "absolute+exact"])
        first = json.loads(w._mpv_socket.written[0].decode("utf-8"))
        second = w._mpv_socket.last_payload()
        self.assertIn("request_id", first)
        self.assertIn("request_id", second)
        self.assertEqual(second["request_id"], first["request_id"] + 1)
        self.assertEqual(first["command"], ["get_property", "pause"])

    def test_reply_dispatches_registered_callback(self):
        w = _make_widget()
        got = {}
        w._send_mpv_command(["get_property", "pause"], on_reply=got.update)
        rid = w._mpv_socket.last_payload()["request_id"]
        w._handle_mpv_message({"request_id": rid, "error": "success", "data": True})
        self.assertEqual(got.get("error"), "success")
        self.assertIs(got.get("data"), True)
        self.assertNotIn(rid, w._mpv_reply_callbacks)  # one-shot

    def test_error_reply_is_logged_not_raised_and_still_delivered(self):
        w = _make_widget()
        got = {}
        w._send_mpv_command(["screenshot-to-file", "x.png", "video"], on_reply=got.update)
        rid = w._mpv_socket.last_payload()["request_id"]
        with self.assertLogs("gui.widgets.video_preview", level="WARNING") as logs:
            w._handle_mpv_message({"request_id": rid, "error": "property unavailable"})
        self.assertTrue(any("property unavailable" in line for line in logs.output))
        self.assertEqual(got.get("error"), "property unavailable")

    def test_reply_without_callback_is_consumed_silently(self):
        w = _make_widget()
        w._send_mpv_command(["observe_property", 1, "time-pos"])
        rid = w._mpv_socket.last_payload()["request_id"]
        w._handle_mpv_message({"request_id": rid, "error": "success"})  # no crash

    def test_events_still_drive_the_frame_counter(self):
        w = _make_widget()
        w.video_fps = 30.0
        w.total_frames = 100
        w._handle_mpv_message(
            {"event": "property-change", "name": "time-pos", "data": 1.0}
        )
        self.assertEqual(w.current_frame_index, 30)

    def test_preconnect_queue_holds_command_and_callback(self):
        w = _make_widget()
        w._mpv_socket = None  # not connected yet
        cb = lambda msg: None
        w._send_mpv_command(["seek", 2.0, "absolute+exact"], on_reply=cb)
        self.assertEqual(
            w._pending_mpv_cmds, [(["seek", 2.0, "absolute+exact"], cb)]
        )

    def test_connected_flush_preserves_callbacks(self):
        w = _make_widget()
        fake = w._mpv_socket
        got = {}
        w._pending_mpv_cmds = [(["get_property", "pause"], got.update)]
        w._on_mpv_ipc_connected()  # flushes the queue with callbacks intact
        self.assertEqual(w._pending_mpv_cmds, [])
        flushed = [
            json.loads(raw.decode("utf-8"))
            for raw in fake.written
            if json.loads(raw.decode("utf-8"))["command"][:1] == ["get_property"]
        ]
        self.assertEqual(len(flushed), 1)
        w._handle_mpv_message(
            {"request_id": flushed[0]["request_id"], "error": "success", "data": False}
        )
        self.assertEqual(got.get("error"), "success")

    def test_capture_frame_async_static_path_returns_in_memory_frame(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        w = VideoPreviewWidget()  # no mpv session at all
        marker = np.full((8, 6, 3), 42, dtype=np.uint8)
        w.current_frame = marker
        box = {}
        w.capture_frame_async(lambda frame: box.update(frame=frame))
        self.assertIs(box.get("frame"), marker)

    def test_request_screenshot_without_mpv_yields_none(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        w = VideoPreviewWidget()
        box = {}
        ok = w.request_screenshot(lambda frame: box.update(frame=frame))
        self.assertFalse(ok)
        self.assertIn("frame", box)
        self.assertIsNone(box["frame"])


if __name__ == "__main__":
    unittest.main()
