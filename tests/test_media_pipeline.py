import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.export_service import VideoExportParams


class MediaToolchainTests(unittest.TestCase):
    def test_discovers_bundled_media_tools_without_ffmpeg(self):
        from core.media_tools import MediaToolchain

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "tools" / "media"
            media_dir.mkdir(parents=True)
            for name in ("VSPipe.exe", "x264-7mod.exe", "MP4Box.exe"):
                (media_dir / name).write_text("", encoding="utf-8")
            (root / "ffmpeg.exe").write_text("", encoding="utf-8")
            (root / "ffprobe.exe").write_text("", encoding="utf-8")

            toolchain = MediaToolchain.discover(root)

        self.assertEqual(Path(toolchain.vspipe_path).name, "VSPipe.exe")
        self.assertEqual(Path(toolchain.x264_path).name, "x264-7mod.exe")
        self.assertEqual(Path(toolchain.muxer_path).name, "MP4Box.exe")
        self.assertNotIn("ffmpeg", toolchain.describe().lower())
        self.assertNotIn("ffprobe", toolchain.describe().lower())

    def test_encoder_commands_use_vspipe_y4m_and_x264_stdin(self):
        from core.media_pipeline import build_vspipe_command, build_x264_command

        vspipe = build_vspipe_command("VSPipe.exe", "script.vpy")
        x264 = build_x264_command("x264-7mod.exe", "out.mp4", crf=26, preset="veryslow")

        # -p enables VSPipe per-frame progress on stderr (drives the dialog).
        self.assertEqual(vspipe, ["VSPipe.exe", "-c", "y4m", "-p", "script.vpy", "-"])
        self.assertIn("--demuxer", x264)
        self.assertIn("y4m", x264)
        self.assertIn("--output", x264)
        self.assertIn("out.mp4", x264)
        self.assertIn("--partitions", x264)
        self.assertNotIn("--x264-params", x264)
        self.assertEqual(x264[-1], "-")
        self.assertNotIn("ffmpeg", " ".join(vspipe + x264).lower())

    def test_x264_signals_smpte170m_colour_in_vui(self):
        # x264-7mod defaults are --colorprim/--transfer/--colormatrix "undef" and
        # --range "auto" (x264-7mod --fullhelp); an untagged sub-HD stream is
        # decoded as BT.601 by convention (H.273), so the pipeline must tag what
        # it actually converted to.
        from core.media_pipeline import build_x264_command

        x264 = build_x264_command("x264-7mod.exe", "out.264")
        for flag, value in (
            ("--colormatrix", "smpte170m"),
            ("--colorprim", "smpte170m"),
            ("--transfer", "smpte170m"),
            ("--range", "tv"),
        ):
            self.assertIn(flag, x264)
            self.assertEqual(x264[x264.index(flag) + 1], value)

    def test_discovers_lsmash_muxer_when_mp4box_is_absent(self):
        from core.media_tools import MediaToolchain

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media_dir = root / "tools" / "media"
            media_dir.mkdir(parents=True)
            for name in ("VSPipe.exe", "x264-7mod.exe", "lsmash-muxer.exe"):
                (media_dir / name).write_text("", encoding="utf-8")

            toolchain = MediaToolchain.discover(root)

        self.assertEqual(Path(toolchain.muxer_path).name, "lsmash-muxer.exe")

    def test_export_requires_external_mp4_muxer(self):
        from core.media_tools import MediaToolchain

        toolchain = MediaToolchain(
            vspipe_path="VSPipe.exe",
            x264_path="x264-7mod.exe",
        )

        self.assertEqual(["MP4Box or lsmash-muxer"], toolchain.missing_for_export())

    def test_export_requires_vapoursynth_source_plugins(self):
        from core import media_tools
        from core.media_tools import MediaToolchain

        toolchain = MediaToolchain(
            vspipe_path="VSPipe.exe",
            x264_path="x264-7mod.exe",
            muxer_path="MP4Box.exe",
        )

        with mock.patch.object(
            media_tools,
            "_missing_vapoursynth_plugins",
            return_value=("VapourSynth plugin lsmas", "VapourSynth plugin imwri"),
        ):
            missing = toolchain.missing_for_export()

        self.assertEqual(
            ["VapourSynth plugin lsmas", "VapourSynth plugin imwri"],
            missing,
        )

    def test_muxer_commands_use_rational_fps(self):
        # A probed 29.97 float is really 30000/1001 (NTSC). Re-stamping the
        # muxer with the lossy float made every frame duration slightly wrong;
        # MP4Box accepts "num/den" (mp4box -h import: "-fps ... as TS/inc").
        from core.media_pipeline import (
            build_lsmash_mux_command,
            build_mp4box_mux_command,
            _fps_to_fraction,
        )

        self.assertEqual(
            build_mp4box_mux_command("MP4Box.exe", "video.264", "out.mp4", 29.97),
            ["MP4Box.exe", "-add", "video.264:fps=30000/1001", "-new", "out.mp4"],
        )
        # Whole rates stay bare integers, not "30/1".
        self.assertEqual(
            build_lsmash_mux_command(
                "lsmash-muxer.exe", "video.264", "out.mp4", 30.0
            ),
            ["lsmash-muxer.exe", "-i", "video.264", "--fps", "30", "-o", "out.mp4"],
        )
        self.assertEqual(_fps_to_fraction(23.976).as_integer_ratio(), (24000, 1001))
        self.assertEqual(_fps_to_fraction(59.94).as_integer_ratio(), (60000, 1001))


class VapourSynthScriptTests(unittest.TestCase):
    def test_writes_video_script_with_trim_crop_resize_and_padding(self):
        from core.media_pipeline import write_vpy_script

        params = VideoExportParams(
            video_path=r"C:\media\loop.mp4",
            cropbox=(10, 20, 100, 200),
            start_frame=5,
            end_frame=35,
            fps=30.0,
            resolution="360x640",
            rotation=180,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            script_path = Path(temp_dir) / "loop.vpy"
            write_vpy_script(script_path, params)
            script = script_path.read_text(encoding="utf-8")

        self.assertIn("import vapoursynth as vs", script)
        self.assertIn("LWLibavSource", script)
        self.assertIn("clip = clip[5:35]", script)
        self.assertIn("core.std.Crop", script)
        self.assertIn("core.resize.Bicubic", script)
        self.assertIn("core.std.AddBorders", script)
        self.assertIn("format=vs.YUV420P8", script)
        # Video sources are normalized to SMPTE 170M: unspecified _Matrix (2)
        # gets the H.273 resolution heuristic stamped, then resize converts.
        self.assertIn("matrix_s='170m'", script)
        self.assertIn("_Matrix", script)
        self.assertNotIn("ffmpeg", script.lower())

    def test_video_script_never_emits_empty_trim(self):
        from core.media_pipeline import write_vpy_script

        params = VideoExportParams(
            video_path=r"C:\media\loop.mp4",
            cropbox=(0, 0, 0, 0),
            start_frame=5,
            end_frame=5,  # degenerate trim: clip[5:5] would be an EMPTY clip
            fps=30.0,
            resolution="360x640",
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            script_path = Path(temp_dir) / "loop.vpy"
            write_vpy_script(script_path, params)
            script = script_path.read_text(encoding="utf-8")

        self.assertIn("clip = clip[5:6]", script)

    def test_writes_image_loop_script(self):
        from core.media_pipeline import write_vpy_script

        params = VideoExportParams(
            video_path=r"C:\media\logo.png",
            cropbox=(0, 0, 0, 0),
            start_frame=0,
            end_frame=30,
            fps=30.0,
            resolution="480x854",
            is_image=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            script_path = Path(temp_dir) / "image.vpy"
            write_vpy_script(script_path, params)
            script = script_path.read_text(encoding="utf-8")

        self.assertIn("core.imwri.Read", script)
        self.assertIn("core.std.Loop", script)
        self.assertIn("times=30", script)
        self.assertNotIn("length=30", script)
        self.assertIn("width=480", script)
        self.assertIn("height=854", script)
        # RGB->YUV conversion matrix must match the smpte170m VUI tags (sub-HD
        # targets decode as BT.601 by convention; the old '709' produced a
        # visible colour shift on export).
        self.assertIn("matrix_s='170m'", script)
        self.assertNotIn("matrix_s='709'", script)

    def test_image_loop_script_applies_crop_and_rotation(self):
        # The crop/rotation blocks used to live only in the video branch, so an
        # image loop silently ignored the user's framing. They are shared now.
        from core.media_pipeline import write_vpy_script

        params = VideoExportParams(
            video_path=r"C:\media\bg.png",
            cropbox=(10, 20, 100, 200),
            start_frame=0,
            end_frame=30,
            fps=30.0,
            resolution="360x640",
            is_image=True,
            rotation=90,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            script_path = Path(temp_dir) / "image.vpy"
            write_vpy_script(script_path, params)
            script = script_path.read_text(encoding="utf-8")

        self.assertIn("core.std.Transpose", script)
        self.assertIn("core.std.FlipHorizontal", script)
        self.assertIn("core.std.CropAbs", script)
        # Loop AFTER rotate/crop: one processed frame duplicated.
        self.assertLess(script.index("CropAbs"), script.index("std.Loop"))


class EncoderRunTests(unittest.TestCase):
    def test_run_encoder_terminates_pipeline_on_cancellation(self):
        from core.media_pipeline import MediaEncoder, MediaToolchain

        toolchain = MediaToolchain(
            vspipe_path="VSPipe.exe",
            x264_path="x264-7mod.exe",
            muxer_path="MP4Box.exe",
        )
        encoder = MediaEncoder(toolchain)
        cancelled = mock.Mock(return_value=True)

        with self.assertRaises(InterruptedError):
            encoder.encode_vpy_to_mp4("script.vpy", "out.mp4", 30.0, is_cancelled=cancelled)

        self.assertEqual(encoder.active_processes, [])

    def test_encoder_uses_external_muxer_without_trying_x264_mp4_output(self):
        from core.media_pipeline import MediaEncoder, MediaToolchain

        class FakePipe:
            def close(self):
                pass

        class FakePopen:
            calls = []

            def __init__(self, cmd, **kwargs):
                self.cmd = cmd
                self.kwargs = kwargs
                self.stdout = FakePipe()
                self.returncode = 0
                self.stderr_bytes = b""
                FakePopen.calls.append(cmd)
                if cmd[0] == "x264-7mod.exe":
                    output_path = cmd[cmd.index("--output") + 1]
                    if output_path.endswith(".mp4"):
                        raise AssertionError("x264 must not write MP4 directly")
                    Path(output_path).write_bytes(b"raw")

            def poll(self):
                return self.returncode

            def communicate(self, timeout=None):
                return b"", self.stderr_bytes

            def wait(self, timeout=None):
                return self.returncode

            def terminate(self):
                self.returncode = 0

            def kill(self):
                self.returncode = 0

        mux_calls = []

        def fake_run(cmd, **kwargs):
            mux_calls.append(cmd)
            Path(cmd[-1]).write_bytes(b"mp4")
            return mock.Mock(returncode=0, stderr="")

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "out.mp4"
            toolchain = MediaToolchain(
                    vspipe_path="VSPipe.exe",
                x264_path="x264-7mod.exe",
                muxer_path="MP4Box.exe",
            )
            encoder = MediaEncoder(toolchain)

            with mock.patch("core.media_pipeline.subprocess.Popen", FakePopen):
                with mock.patch("core.media_pipeline.subprocess.run", fake_run):
                    encoder.encode_vpy_to_mp4("script.vpy", str(output_path), 30.0)

            self.assertTrue(output_path.exists())

        x264_outputs = [
            call[call.index("--output") + 1]
            for call in FakePopen.calls
            if call[0] == "x264-7mod.exe"
        ]
        self.assertEqual(1, len(x264_outputs))
        self.assertTrue(x264_outputs[0].endswith(".tmp.264"))
        self.assertEqual(
            mux_calls[0][0:3],
            ["MP4Box.exe", "-add", str(output_path.with_suffix(".tmp.264")) + ":fps=30"],
        )

    def test_vspipe_failure_includes_stderr_details(self):
        from core.media_pipeline import MediaEncoder, MediaToolchain

        class FakePipe:
            def close(self):
                pass

            def read(self):
                return b"Script evaluation failed: missing lsmas plugin"

        class FakePopen:
            def __init__(self, cmd, **kwargs):
                self.cmd = cmd
                self.kwargs = kwargs
                self.stdout = FakePipe()
                self.stderr = FakePipe()
                self.returncode = 1 if cmd[0] == "VSPipe.exe" else 0

            def poll(self):
                return self.returncode

            def communicate(self, timeout=None):
                return b"", b""

            def wait(self, timeout=None):
                return self.returncode

            def terminate(self):
                pass

            def kill(self):
                pass

        toolchain = MediaToolchain(
            vspipe_path="VSPipe.exe",
            x264_path="x264-7mod.exe",
            muxer_path="MP4Box.exe",
        )
        encoder = MediaEncoder(toolchain)

        with mock.patch("core.media_pipeline.subprocess.Popen", FakePopen):
            with self.assertRaisesRegex(RuntimeError, "missing lsmas plugin"):
                encoder.encode_vpy_to_mp4("script.vpy", "out.mp4", 30.0)


if __name__ == "__main__":
    unittest.main()
