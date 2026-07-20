"""Regression tests for the mpv metadata-probe IPC connection (L1).

Proves the old no-backoff retry loop could not tolerate a pipe that appears
slightly late, that the new backoff loop can, and that a real mpv probe now
returns valid metadata (skipped when tools/media is absent).
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import time
import uuid
import threading
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
from PyQt6.QtNetwork import QLocalSocket, QLocalServer

from core.media_tools import MediaToolchain

REPO = Path(__file__).resolve().parent.parent
TC = MediaToolchain.discover(str(REPO))
MPV_OK = bool(TC.mpv_path)
ENCODE_OK = HAS_CV2 and not TC.missing_for_export()


def setUpModule():
    ensure_app()


def _old_connect(name):
    """The old strategy: sleep(0.2) then a tight 75x loop with NO sleep between attempts."""
    time.sleep(0.2)
    s = QLocalSocket()
    for _ in range(75):
        s.connectToServer(name)
        if s.waitForConnected(200):
            s.abort(); return True
        s.abort()
    return False


def _new_connect(name):
    """The new strategy: a wall-clock deadline with sleep(0.1) between attempts."""
    s = QLocalSocket()
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        s.connectToServer(name)
        if s.waitForConnected(200):
            s.abort(); return True
        s.abort()
        time.sleep(0.1)
    return False


def _run_with_late_server(connect_fn, delay=0.8):
    name = "neo_test_slowpipe_" + uuid.uuid4().hex
    box = {}

    def serve():
        time.sleep(delay)
        QLocalServer.removeServer(name)
        srv = QLocalServer()
        box["ok"] = srv.listen(name)
        box["srv"] = srv
        time.sleep(3.0)
        srv.close()

    t = threading.Thread(target=serve)
    t.start()
    connected = connect_fn(name)
    t.join()
    return connected


class MpvIpcConnectStrategyTests(unittest.TestCase):
    def test_wait_for_connected_returns_immediately_for_absent_pipe(self):
        # The premise of the bug: waitForConnected does NOT wait its timeout when the
        # pipe does not exist -> a no-sleep retry loop provides no real retry window.
        s = QLocalSocket()
        t0 = time.perf_counter()
        s.connectToServer("neo_absent_" + uuid.uuid4().hex)
        ok = s.waitForConnected(200)
        dt_ms = (time.perf_counter() - t0) * 1000
        self.assertFalse(ok)
        self.assertLess(dt_ms, 100)  # not ~200ms

    def test_backoff_tolerates_late_pipe_but_tight_loop_does_not(self):
        self.assertFalse(_run_with_late_server(_old_connect),
                         "old no-sleep loop must fail when the pipe appears at 0.8s")
        self.assertTrue(_run_with_late_server(_new_connect),
                        "new backoff loop must connect once the pipe appears")


@unittest.skipUnless(MPV_OK and ENCODE_OK, "mpv / encode toolchain (tools/media) unavailable")
class MpvMetadataProbeRealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.media_pipeline import MediaEncoder, _quote_vs_string
        cls.d = Path(tempfile.mkdtemp())
        png = cls.d / "m.png"
        cv2.imwrite(str(png), np.full((360, 240, 3), 200, np.uint8))
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

    def test_probe_returns_valid_metadata(self):
        from core.video_processor import VideoProcessor
        info = VideoProcessor(TC.mpv_path).get_video_info(str(self.mp4))
        self.assertIsNotNone(info)
        self.assertEqual(info.width, 240)
        self.assertEqual(info.height, 360)
        self.assertGreater(info.total_frames, 0)
        self.assertGreater(info.fps, 0)


if __name__ == "__main__":
    unittest.main()
