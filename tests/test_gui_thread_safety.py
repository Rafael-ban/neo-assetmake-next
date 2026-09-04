"""Offscreen GUI regression tests for the USB/preview thread-safety fixes.

Covers:
  B2 - the file-manager delete path raises the busy gate.
  B4 - finished per-op workers are disposed (no QThread accumulation).
  B3 - the preview backend is in-process VapourSynth and frames reach the GUI
       thread (this replaced "mpv QProcess is created on the GUI thread": there
       is no child-process player any more).
"""
import os
import subprocess
import sys
import types
import unittest
from pathlib import Path

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


class PreviewBackendTests(unittest.TestCase):
    """预览后端只剩进程内 VapourSynth:不得再有 mpv 子进程/IPC 机制。

    取代了 B3 的"mpv QProcess/QLocalSocket 必须在 GUI 线程"一组断言 —— 那条
    约束存在的前提(有一个子进程播放器)已经不存在了。现在要守的是相反的性质:
    这些符号不能回来,且帧交付必须落在 GUI 线程。
    """

    def test_no_mpv_process_or_ipc_machinery_remains(self):
        import gui.widgets.video_preview as vp

        self.assertFalse(hasattr(vp, "MpvLaunchWorker"))
        self.assertFalse(hasattr(vp, "_MpvSurface"))
        self.assertFalse(hasattr(vp, "_DYING_MPV_PROCESSES"))
        cls = vp.VideoPreviewWidget
        for gone in ("_start_mpv_preview", "_stop_mpv_process", "_send_mpv_command",
                     "_make_mpv_ipc_server", "_try_mpv_ipc_connect",
                     "_on_mpv_ipc_connected", "_on_mpv_ipc_error",
                     "_on_mpv_process_error", "_on_mpv_launch_failed",
                     "_seek_mpv_to_current_frame", "request_screenshot"):
            self.assertFalse(hasattr(cls, gone), f"{gone} should be gone")

    def test_qt_process_does_not_import_or_prewarm_vapoursynth(self):
        root = Path(__file__).resolve().parents[1]
        main_source = (root / "main.py").read_text(encoding="utf-8")
        harness_source = (root / "tests" / "qt_harness.py").read_text(
            encoding="utf-8"
        )
        preview_source = (
            root / "gui" / "widgets" / "video_preview.py"
        ).read_text(encoding="utf-8")

        self.assertNotIn("vs_engine.prewarm", main_source)
        self.assertNotIn("vs_engine.prewarm", harness_source)
        self.assertNotIn("from core import vs_engine", preview_source)

    def test_widget_constructs_after_qt_without_loading_vapoursynth(self):
        root = Path(__file__).resolve().parents[1]
        code = "\n".join(
            [
                "import os, sys",
                "os.environ['QT_QPA_PLATFORM'] = 'offscreen'",
                "from PyQt6.QtWidgets import QApplication",
                "app = QApplication([])",
                "from gui.widgets.video_preview import VideoPreviewWidget",
                "widget = VideoPreviewWidget()",
                "assert 'vapoursynth' not in sys.modules, sorted(",
                "    name for name in sys.modules if 'vapoursynth' in name)",
                "widget.clear(sync_shutdown=True)",
            ]
        )
        env = os.environ.copy()
        env["QT_QPA_PLATFORM"] = "offscreen"
        result = subprocess.run(
            [sys.executable, "-B", "-c", code],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
