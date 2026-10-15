"""Offscreen GUI regression tests for the USB/mpv thread-safety fixes.

Covers:
  B2 - the file-manager delete path raises the busy gate.
  B4 - finished per-op workers are disposed (no QThread accumulation).
  B3 - mpv QProcess is created on the GUI thread (correct affinity).
"""
import sys
import types
import unittest

from tests.qt_harness import ensure_app


def setUpModule():
    ensure_app()


class DeleteBusyGateTests(unittest.TestCase):
    """B2: multi-item delete must call set_busy(True)."""

    def test_on_delete_sets_busy(self):
        import gui.widgets.usb_file_page as ufp
        from gui.widgets.usb_file_page import UsbFilePage

        real_sb = ufp.QMessageBox.StandardButton

        class _FakeMB:
            StandardButton = real_sb
            @staticmethod
            def question(*a, **k):
                return real_sb.Yes

        self.addCleanup(setattr, ufp, "QMessageBox", ufp.QMessageBox)
        ufp.QMessageBox = _FakeMB

        class Ctrl:
            def __init__(self):
                self._is_busy = False
                self._is_connected = True
                self.calls = []
                self.progressBar = types.SimpleNamespace(setVisible=lambda *a: None, setValue=lambda *a: None)
                self.progressLabel = types.SimpleNamespace(setText=lambda *a: None)
                self.usbRC = object()
            def set_busy(self, b):
                self.calls.append(b); self._is_busy = b

        started = {"n": 0}
        fake = types.SimpleNamespace()
        fake.controller = Ctrl()
        fake._current_path = "/sd"
        fake._pending_deletes = []
        fake._get_selected_items = lambda: [{"name": "a", "is_dir": False}, {"name": "b", "is_dir": False}]
        fake._delete_next = lambda: started.__setitem__("n", started["n"] + 1)

        UsbFilePage._on_delete(fake)
        self.assertIn(True, fake.controller.calls)   # old code never set busy
        self.assertEqual(started["n"], 1)


class WorkerDisposalTests(unittest.TestCase):
    """B4: _track_worker disposes finished workers so they do not accumulate."""

    def test_tracked_workers_are_disposed(self):
        from PyQt6.QtCore import QObject, QThread
        from PyQt6.QtWidgets import QApplication
        from gui.widgets.usb_file_page import UsbFilePage

        app = QApplication.instance()

        class QuickThread(QThread):
            def run(self):
                pass

        parent = QObject()
        fake = types.SimpleNamespace(
            _list_worker=None, _upload_worker=None, _download_worker=None,
            _delete_worker=None, _copy_worker=None, _move_worker=None,
            _stat_worker=None, _mkdir_worker=None,
        )
        for _ in range(6):
            w = QuickThread(parent)
            fake._list_worker = w
            UsbFilePage._track_worker(fake, w)
            w.start(); w.wait()
        for _ in range(10):
            app.processEvents()

        children = [c for c in parent.children() if isinstance(c, QThread)]
        self.assertEqual(len(children), 0)


class MpvAffinityTests(unittest.TestCase):
    """B3: mpv QProcess/QLocalSocket must live on the GUI thread."""

    def test_module_retired_worker_and_added_async_handlers(self):
        import gui.widgets.video_preview as vp
        self.assertFalse(hasattr(vp, "MpvLaunchWorker"))
        cls = next(c for c in vars(vp).values()
                   if isinstance(c, type) and hasattr(c, "_start_mpv_preview"))
        self.assertFalse(hasattr(cls, "_on_mpv_launched"))
        for added in ("_on_mpv_process_started", "_try_mpv_ipc_connect",
                      "_on_mpv_ipc_connected", "_on_mpv_ipc_error", "_on_mpv_process_error"):
            self.assertTrue(hasattr(cls, added), added)

    def test_start_creates_gui_thread_affine_process(self):
        from PyQt6.QtCore import QObject, QProcess
        from PyQt6.QtWidgets import QApplication
        import gui.widgets.video_preview as vp

        app = QApplication.instance()
        gui_thread = app.thread()
        cls = next(c for c in vars(vp).values()
                   if isinstance(c, type) and hasattr(c, "_start_mpv_preview"))

        holder = QObject()
        for m in ("_stop_mpv_process", "_make_mpv_ipc_server", "_send_mpv_command",
                  "_on_mpv_process_error", "_on_mpv_process_started", "_try_mpv_ipc_connect",
                  "_on_mpv_ipc_connected", "_on_mpv_ipc_error", "_on_mpv_launch_failed",
                  "_start_mpv_preview"):
            setattr(holder, m, types.MethodType(getattr(cls, m), holder))
        holder._mpv_process = None
        holder._mpv_socket = None
        holder._mpv_ipc_server = ""
        holder._mpv_ipc_attempts = 0
        holder._rotation = 0
        holder._mpv_page_index = 0
        holder._display_stack = types.SimpleNamespace(setCurrentIndex=lambda i: None)
        holder._media_toolchain = types.SimpleNamespace(mpv_path=sys.executable)
        holder.video_label = types.SimpleNamespace(setText=lambda *a: None)

        holder._start_mpv_preview("test.mp4")
        proc = holder._mpv_process
        self.assertIsInstance(proc, QProcess)
        self.assertIs(proc.thread(), gui_thread)
        for _ in range(5):
            app.processEvents()
        holder._stop_mpv_process()


if __name__ == "__main__":
    unittest.main()
