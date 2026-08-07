"""S2: asynchronous preview lifecycle tests.

Old code ran the blocking mpv metadata probe (waitForStarted/waitForConnected/
waitForReadyRead chains, PyQt6 QtNetwork.pyi:202-205 + QtCore.pyi:6985-6988)
directly inside load_video on the GUI thread, freezing the UI for up to tens
of seconds per load, and tore mpv down with waitForFinished(3000) x2 on every
reload. These tests pin the new contract: load_video = accepted + async
outcome signals, epoch-guarded continuations, and non-blocking teardown.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import time
import tempfile
import unittest
from pathlib import Path

import numpy as np

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from tests.qt_harness import ensure_app
from PyQt6.QtCore import QCoreApplication, QObject, pyqtSignal

from core.media_tools import MediaToolchain
from core.video_processor import VideoInfo

REPO = Path(__file__).resolve().parent.parent
TC = MediaToolchain.discover(str(REPO))
MPV_OK = bool(TC.mpv_path)
ENCODE_OK = HAS_CV2 and not TC.missing_for_export()


def setUpModule():
    ensure_app()


class FakeProbeWorker(QObject):
    """Stands in for MetadataProbeWorker: resolves only when the test says so."""

    result = pyqtSignal(object)
    failed = pyqtSignal(str)
    finished = pyqtSignal()
    instances: list = []

    def __init__(self, mpv_path, input_path, parent=None):
        super().__init__(parent)
        self.input_path = input_path
        FakeProbeWorker.instances.append(self)

    def start(self):
        pass

    def resolve(self, info):
        self.result.emit(info)
        self.finished.emit()

    def reject(self, message):
        self.failed.emit(message)
        self.finished.emit()


def _info(width=240, height=360, fps=30.0, frames=90):
    return VideoInfo(
        width=width, height=height, duration=frames / fps,
        fps=fps, total_frames=frames, codec="h264",
    )


class AsyncLoadTests(unittest.TestCase):
    def setUp(self):
        import gui.widgets.video_preview as vp

        self.vp = vp
        self._orig_worker = vp.MetadataProbeWorker
        vp.MetadataProbeWorker = FakeProbeWorker
        FakeProbeWorker.instances = []

        self.w = vp.VideoPreviewWidget()
        self.w._media_toolchain = None  # replaced by discover() in load_video
        self._orig_discover = vp.MediaToolchain.discover
        vp.MediaToolchain.discover = staticmethod(
            lambda *a, **k: type("TC", (), {"mpv_path": "mpv-fake"})()
        )
        self.w._start_mpv_preview = lambda path: True  # no real mpv in unit tests

        self.tmp = tempfile.mkdtemp()
        self.file_a = os.path.join(self.tmp, "a.mp4")
        self.file_b = os.path.join(self.tmp, "b.mp4")
        Path(self.file_a).write_bytes(b"x")
        Path(self.file_b).write_bytes(b"x")

    def tearDown(self):
        self.vp.MetadataProbeWorker = self._orig_worker
        self.vp.MediaToolchain.discover = self._orig_discover

    def test_load_is_accepted_immediately_and_resolves_via_signal(self):
        loaded = {}
        self.w.video_loaded.connect(lambda n, fps: loaded.update(n=n, fps=fps))
        t0 = time.perf_counter()
        self.assertTrue(self.w.load_video(self.file_a))
        accept_ms = (time.perf_counter() - t0) * 1000
        self.assertLess(accept_ms, 200, "load_video must not block on the probe")
        self.assertEqual(self.w.video_label.text(), "正在加载视频元数据…")
        self.assertFalse(self.w._has_video)

        FakeProbeWorker.instances[-1].resolve(_info())
        QCoreApplication.processEvents()
        self.assertEqual(loaded.get("n"), 90)
        self.assertTrue(self.w._has_video)
        self.assertEqual(self.w.video_path, self.file_a)
        self.assertEqual((self.w.video_width, self.w.video_height), (240, 360))

    def test_probe_failure_emits_load_failed(self):
        failures = []
        self.w.load_failed.connect(failures.append)
        self.assertTrue(self.w.load_video(self.file_a))
        FakeProbeWorker.instances[-1].reject("boom")
        QCoreApplication.processEvents()
        self.assertEqual(failures, ["boom"])
        self.assertFalse(self.w._has_video)
        self.assertEqual(self.w.video_label.text(), "无法加载视频元数据")

    def test_stale_probe_result_is_discarded_after_newer_load(self):
        self.assertTrue(self.w.load_video(self.file_a))
        worker_a = FakeProbeWorker.instances[-1]
        self.assertTrue(self.w.load_video(self.file_b))
        worker_b = FakeProbeWorker.instances[-1]

        loaded = []
        self.w.video_loaded.connect(lambda n, fps: loaded.append(self.w.video_path))
        worker_a.resolve(_info(width=111, height=222))  # late result from load A
        QCoreApplication.processEvents()
        self.assertEqual(loaded, [], "stale probe result must be discarded")
        self.assertFalse(self.w._has_video)

        worker_b.resolve(_info())
        QCoreApplication.processEvents()
        self.assertEqual(loaded, [self.file_b])
        self.assertEqual(self.w.video_path, self.file_b)

    def test_stale_probe_failure_is_discarded_after_clear(self):
        failures = []
        self.w.load_failed.connect(failures.append)
        self.assertTrue(self.w.load_video(self.file_a))
        worker = FakeProbeWorker.instances[-1]
        self.w.clear()
        worker.reject("late failure")
        QCoreApplication.processEvents()
        self.assertEqual(failures, [], "failure from a cleared load must be dropped")

    def test_stale_ipc_retry_is_discarded(self):
        self.w._load_epoch = 7
        called = {"n": 0}
        self.w._try_mpv_ipc_connect = lambda: called.__setitem__("n", called["n"] + 1)
        self.w._ipc_retry_epoch = 6  # scheduled before a newer load
        self.w._on_ipc_retry_due()
        self.assertEqual(called["n"], 0)
        self.w._ipc_retry_epoch = 7
        self.w._on_ipc_retry_due()
        self.assertEqual(called["n"], 1)


class _FakeProcess:
    """QProcess stand-in that records blocking/kill calls."""

    class _Sig:
        def __init__(self):
            self.slots = []

        def connect(self, slot):
            self.slots.append(slot)

        def emit(self, *args):
            for slot in list(self.slots):
                slot(*args)

    def __init__(self, running=True):
        from PyQt6.QtCore import QProcess as QP

        self._qp = QP
        self._running = running
        self.wait_calls = []
        self.kill_calls = 0
        self.deleted = False
        self.parent_cleared = False
        self.finished = self._Sig()

    def setParent(self, parent):
        if parent is None:
            self.parent_cleared = True

    def state(self):
        from PyQt6.QtCore import QProcess as QP

        return (
            QP.ProcessState.Running if self._running else QP.ProcessState.NotRunning
        )

    def waitForFinished(self, ms):
        self.wait_calls.append(ms)
        return False

    def kill(self):
        self.kill_calls += 1
        self._running = False

    def deleteLater(self):
        self.deleted = True


class AsyncTeardownTests(unittest.TestCase):
    def _widget(self):
        import gui.widgets.video_preview as vp

        w = vp.VideoPreviewWidget()
        return vp, w

    def test_async_stop_never_blocks_gui_thread(self):
        vp, w = self._widget()
        proc = _FakeProcess(running=True)
        w._mpv_process = proc
        w._mpv_socket = None
        w._stop_mpv_process()  # default: async path
        self.assertEqual(
            proc.wait_calls, [],
            "reload/clear teardown must not call the blocking waitForFinished "
            "(QtCore.pyi:6985) on the GUI thread",
        )
        self.assertIn(proc, vp._DYING_MPV_PROCESSES)
        self.assertTrue(
            proc.parent_cleared,
            "detached process must be un-parented so it outlives the widget",
        )
        proc.finished.emit(0)  # mpv exits on its own -> released
        self.assertNotIn(proc, vp._DYING_MPV_PROCESSES)
        self.assertTrue(proc.deleted)

    def test_sync_stop_blocks_bounded_for_window_close(self):
        vp, w = self._widget()
        proc = _FakeProcess(running=True)
        w._mpv_process = proc
        w._mpv_socket = None
        w._stop_mpv_process(sync=True)
        self.assertTrue(proc.wait_calls, "sync path must wait (bounded) for exit")
        self.assertTrue(proc.deleted)
        self.assertNotIn(proc, vp._DYING_MPV_PROCESSES)


@unittest.skipUnless(MPV_OK and ENCODE_OK, "mpv / encode toolchain (tools/media) unavailable")
class AsyncLoadRealMpvTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.media_pipeline import MediaEncoder, _quote_vs_string

        cls.d = Path(tempfile.mkdtemp())
        png = cls.d / "m.png"
        cv2.imwrite(str(png), np.full((360, 240, 3), 128, np.uint8))
        vpy = cls.d / "src.vpy"
        vpy.write_text("\n".join([
            "import vapoursynth as vs", "core = vs.core",
            f"clip = core.imwri.Read({_quote_vs_string(str(png))})",
            "clip = clip if clip.format.id == vs.RGB24 else core.resize.Bicubic(clip, format=vs.RGB24)",
            "clip = core.std.Loop(clip, times=30)",
            "clip = core.resize.Bicubic(clip, width=240, height=360, format=vs.YUV420P8, matrix_s='709')",
            "clip.set_output()",
        ]) + "\n", encoding="utf-8")
        cls.mp4 = cls.d / "src.mp4"
        MediaEncoder(TC).encode_vpy_to_mp4(str(vpy), str(cls.mp4), 30.0)

    def _pump_until(self, condition, timeout_s):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            QCoreApplication.processEvents()
            if condition():
                return True
            time.sleep(0.01)
        return False

    def test_widget_loads_real_video_asynchronously(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        w = VideoPreviewWidget()
        self.addCleanup(lambda: w.clear(sync_shutdown=True))
        loaded = {}
        w.video_loaded.connect(lambda n, fps: loaded.update(n=n, fps=fps))
        t0 = time.perf_counter()
        self.assertTrue(w.load_video(str(self.mp4)))
        accept_ms = (time.perf_counter() - t0) * 1000
        self.assertLess(accept_ms, 500, "acceptance must return without probing")
        self.assertTrue(self._pump_until(lambda: "n" in loaded, 20.0), "no video_loaded")
        self.assertGreater(loaded["n"], 0)
        # Backend-neutral: the preview must end up showing a real frame,
        # whether that came from the in-process VapourSynth graph or the
        # legacy mpv fallback.
        self.assertTrue(
            self._pump_until(
                lambda: (w._vs_active and w.current_frame is not None)
                or w._mpv_ipc_connected,
                20.0,
            ),
            "no preview backend became ready",
        )

    def test_reload_mid_probe_settles_on_second_video(self):
        from gui.widgets.video_preview import VideoPreviewWidget

        w = VideoPreviewWidget()
        self.addCleanup(lambda: w.clear(sync_shutdown=True))
        events = []
        w.video_loaded.connect(lambda n, fps: events.append(w.video_path))
        self.assertTrue(w.load_video(str(self.mp4)))
        self.assertTrue(w.load_video(str(self.mp4)))  # immediate reload mid-probe
        self.assertTrue(self._pump_until(lambda: events, 20.0), "no video_loaded")
        time.sleep(0.3)
        QCoreApplication.processEvents()
        self.assertEqual(len(events), 1, "stale probe must not double-fire video_loaded")
        self.assertEqual(w.video_path, str(self.mp4))
        self.assertTrue(w._has_video)


if __name__ == "__main__":
    unittest.main()
