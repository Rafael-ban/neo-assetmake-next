"""S6.1-6.4: export pipeline robustness — progress, temp cleanup, atomic package.

- VSPipe -p progress ("Frame: n/total") is parsed and forwarded (old code
  drained it into a buffer and the dialog froze at a fixed percentage).
- A failed/cancelled encode cleans up its .tmp.264 / .tmp.mp4 (old code left
  them littering the export dir).
- ExportWorker stages every artifact and promotes atomically: a mid-export
  failure leaves NO half-populated package (old code wrote icon/overlay
  straight into output_dir before the video encode that could still fail).
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from tests.qt_harness import ensure_app


def setUpModule():
    ensure_app()


def _toolchain():
    from core.media_tools import MediaToolchain

    return MediaToolchain(
        vspipe_path="VSPipe.exe",
        x264_path="x264-7mod.exe", muxer_path="MP4Box.exe",
    )


class _StderrStream:
    """Fake stderr exposing read1() (progress drain) and read() (plain drain)."""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    def read1(self, _n):
        return self._chunks.pop(0) if self._chunks else b""

    def read(self):
        data = b"".join(self._chunks)
        self._chunks = []
        return data

    def close(self):
        pass


class ProgressParsingTests(unittest.TestCase):
    def test_vspipe_frame_progress_is_forwarded(self):
        from core.media_pipeline import MediaEncoder

        class FakePipe:
            def close(self):
                pass

        class FakePopen:
            def __init__(self, cmd, **kwargs):
                self.cmd = cmd
                self.returncode = 0
                if cmd[0] == "VSPipe.exe":
                    self.stdout = FakePipe()
                    self.stderr = _StderrStream(
                        [b"Frame: 3/10\r", b"Frame: 7/10\r", b"Frame: 10/10\n"]
                    )
                else:  # x264
                    self.stdout = FakePipe()
                    self.stderr = _StderrStream([b""])
                    output_path = cmd[cmd.index("--output") + 1]
                    Path(output_path).write_bytes(b"raw")

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

            def terminate(self):
                pass

            def kill(self):
                pass

        progress = []

        def fake_run(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"mp4")
            return mock.Mock(returncode=0, stderr="")

        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "loop.mp4"
            encoder = MediaEncoder(_toolchain())
            with mock.patch("core.media_pipeline.subprocess.Popen", FakePopen):
                with mock.patch("core.media_pipeline.subprocess.run", fake_run):
                    encoder.encode_vpy_to_mp4(
                        "s.vpy", str(out), 30.0,
                        progress_cb=lambda done, total: progress.append((done, total)),
                    )

        self.assertIn((3, 10), progress)
        self.assertIn((10, 10), progress)


class TempCleanupTests(unittest.TestCase):
    def test_failed_encode_removes_temp_files(self):
        from core.media_pipeline import MediaEncoder

        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "loop.mp4"

            def boom(self, script_path, raw, is_cancelled=None, progress_cb=None):
                # simulate x264 having written the raw temp before failing
                Path(raw).write_bytes(b"partial")
                return {"vspipe_returncode": 1, "x264_returncode": 0, "stderr": "err"}

            encoder = MediaEncoder(_toolchain())
            with mock.patch.object(MediaEncoder, "_run_encode_pipeline", boom):
                with self.assertRaises(RuntimeError):
                    encoder.encode_vpy_to_mp4("s.vpy", str(out), 30.0)

            leftovers = [p.name for p in Path(d).iterdir()]
            self.assertEqual(leftovers, [], f"temp files left behind: {leftovers}")


@unittest.skipUnless(HAS_CV2, "opencv-python required for PNG artifact tasks")
class AtomicPackageTests(unittest.TestCase):
    def _worker(self, tasks, output_dir):
        from core.export_service import ExportWorker

        w = ExportWorker()
        w.setup(tasks=tasks, output_dir=output_dir, epconfig=None)
        return w

    def test_failed_video_task_leaves_no_partial_package(self):
        from core.export_service import ExportTask, ExportType

        icon = np.full((16, 16, 3), 200, np.uint8)
        with tempfile.TemporaryDirectory() as d:
            tasks = [
                ExportTask(ExportType.ICON, "icon.png", icon),
                ExportTask(ExportType.LOOP_VIDEO, "loop.mp4", object()),
            ]
            w = self._worker(tasks, d)
            w._export_video = mock.Mock(side_effect=RuntimeError("encode boom"))
            failed = []
            w.export_failed.connect(failed.append)
            w.run()

            self.assertTrue(failed, "a failed task must emit export_failed")
            names = sorted(p.name for p in Path(d).iterdir())
            self.assertEqual(
                names, [],
                f"output dir must be clean after a mid-export failure, got {names}",
            )

    def test_aux_images_promoted_atomically_on_success(self):
        from core.export_service import ExportTask, ExportType

        icon = np.full((16, 16, 3), 200, np.uint8)
        class_icon = np.full((32, 32, 3), 100, np.uint8)
        with tempfile.TemporaryDirectory() as d:
            tasks = [
                ExportTask(ExportType.ICON, "icon.png", icon),
                ExportTask(ExportType.AUX_IMAGE, "class_icon.png", class_icon),
            ]
            w = self._worker(tasks, d)
            completed = []
            w.export_completed.connect(completed.append)
            w.run()

            self.assertTrue(completed)
            names = sorted(p.name for p in Path(d).iterdir())
            self.assertEqual(names, ["class_icon.png", "icon.png"])
            self.assertFalse((Path(d) / ".export_tmp").exists())


if __name__ == "__main__":
    unittest.main()
