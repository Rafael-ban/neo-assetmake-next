"""VapourSynth and x264-7mod export pipeline."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path
from typing import Callable, Optional

from core.media_tools import MediaToolchain, build_media_subprocess_env
from core.video_processor import X264_CLI_ARGS
# VS script authoring lives in core.vs_script now; re-exported here so the two
# production callers and the test suite keep importing them from core.media_pipeline.
from core.vs_script import write_vpy_script, _quote_vs_string, _vs_path

# VSPipe -p prints "Frame: <done>/<total>" lines (\r-refreshed) to stderr.
_VSPIPE_PROGRESS_RE = re.compile(rb"Frame:\s*(\d+)\s*/\s*(\d+)")


def build_vspipe_command(vspipe_path: str, script_path: str) -> list[str]:
    """Build a VSPipe command that emits Y4M to stdout.

    ``-p`` enables per-frame progress on stderr (VSPipe R73 --help:
    "-p, --progress   Print progress to stderr") so the export dialog can
    show real progress instead of freezing at a fixed percentage.
    """
    return [vspipe_path, "-c", "y4m", "-p", script_path, "-"]


def build_x264_command(
    x264_path: str,
    output_path: str,
    *,
    crf: int = 26,
    preset: str = "veryslow",
    colormatrix: str = "smpte170m",
    colorprim: str = "smpte170m",
    transfer: str = "smpte170m",
    range_: str = "tv",
) -> list[str]:
    """Build an x264-7mod command that consumes Y4M from stdin.

    Colour signalling is written into the H.264 VUI explicitly: x264-7mod's
    defaults are --colorprim/--transfer/--colormatrix "undef" and --range
    "auto" (verified via `x264-7mod --fullhelp`), and an untagged sub-HD
    stream gets decoded with BT.601 by convention (H.273) — every target
    resolution here is sub-HD, and the pipeline converts to SMPTE 170M, so
    the stream must say so or players guess and shift the colours.
    """
    return [
        x264_path,
        "--demuxer",
        "y4m",
        "--preset",
        preset,
        "--crf",
        str(crf),
        "--profile",
        "high",
        "--output-csp",
        "i420",
        "--colormatrix",
        colormatrix,
        "--colorprim",
        colorprim,
        "--transfer",
        transfer,
        "--range",
        range_,
        *X264_CLI_ARGS,
        "--output",
        output_path,
        "-",
    ]


# Exact broadcast rates: the NTSC family MUST stay rational — 29.97 is not
# 30000/1001, and a float re-stamp makes every frame duration slightly wrong,
# which accumulates as timing drift over a looping asset.
_COMMON_FPS = (
    Fraction(24000, 1001),
    Fraction(30000, 1001),
    Fraction(60000, 1001),
    Fraction(24),
    Fraction(25),
    Fraction(30),
    Fraction(50),
    Fraction(60),
)


def _fps_to_fraction(fps: float) -> Fraction:
    """Snap a probed float fps to the nearest common broadcast rational."""
    value = float(fps)
    for candidate in _COMMON_FPS:
        if abs(value - float(candidate)) < 1e-3:
            return candidate
    return Fraction(value).limit_denominator(1001)


def _format_fps(fps: float) -> str:
    """Format fps for the muxers: integers stay bare, others become num/den.

    MP4Box documents ``-fps`` as "expressed as a number, as TS-inc or TS/inc"
    (mp4box -h import), so ``30000/1001`` is valid; the old ``%g`` float form
    (``29.97``) silently discarded the exact rational.
    """
    frac = _fps_to_fraction(fps)
    if frac.denominator == 1:
        return str(frac.numerator)
    return f"{frac.numerator}/{frac.denominator}"


def build_mp4box_mux_command(
    muxer_path: str,
    raw_h264_path: str,
    output_path: str,
    fps: float,
) -> list[str]:
    """Build an MP4Box command for wrapping raw H.264 into MP4."""
    return [
        muxer_path,
        "-add",
        f"{raw_h264_path}:fps={_format_fps(fps)}",
        "-new",
        output_path,
    ]


def build_lsmash_mux_command(
    muxer_path: str,
    raw_h264_path: str,
    output_path: str,
    fps: float,
) -> list[str]:
    """Build an lsmash-muxer command for wrapping raw H.264 into MP4."""
    return [
        muxer_path,
        "-i",
        raw_h264_path,
        "--fps",
        _format_fps(fps),
        "-o",
        output_path,
    ]


def build_mux_command(
    muxer_path: str,
    raw_h264_path: str,
    output_path: str,
    fps: float,
) -> list[str]:
    """Build the configured MP4 muxer command."""
    muxer_name = Path(muxer_path).name.lower()
    if "mp4box" in muxer_name:
        return build_mp4box_mux_command(muxer_path, raw_h264_path, output_path, fps)
    return build_lsmash_mux_command(muxer_path, raw_h264_path, output_path, fps)


class MediaEncoder:
    """Run VSPipe and x264-7mod as a cancellable encode pipeline."""

    def __init__(self, toolchain: MediaToolchain):
        self.toolchain = toolchain
        self.active_processes: list[subprocess.Popen] = []

    def terminate_active_processes(self) -> None:
        processes = list(self.active_processes)
        for process in processes:
            if process.poll() is None:
                try:
                    process.terminate()
                except Exception:
                    pass
        for process in processes:
            if process.poll() is None:
                try:
                    process.wait(timeout=2)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
        self.active_processes.clear()

    def encode_vpy_to_mp4(
        self,
        script_path: str,
        output_path: str,
        fps: float,
        *,
        is_cancelled: Optional[Callable[[], bool]] = None,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> None:
        if is_cancelled and is_cancelled():
            self.terminate_active_processes()
            raise InterruptedError("Export cancelled")

        missing = self.toolchain.missing_for_export()
        if missing:
            raise RuntimeError("Missing media tools: " + ", ".join(missing))
        if not self.toolchain.muxer_path:
            raise RuntimeError(
                "Missing MP4 muxer: MP4Box or lsmash-muxer is required because "
                "x264-7mod writes raw H.264 before MP4 packaging"
            )

        output_root, output_ext = os.path.splitext(output_path)
        temp_output = f"{output_root}.tmp{output_ext or '.mp4'}"
        temp_raw = f"{output_root}.tmp.264"
        if os.path.exists(temp_output):
            os.remove(temp_output)
        if os.path.exists(temp_raw):
            os.remove(temp_raw)

        try:
            result = self._run_encode_pipeline(
                script_path, temp_raw, is_cancelled, progress_cb
            )
            if result["vspipe_returncode"] != 0:
                details = str(result["stderr"])[-1000:].strip()
                message = f"VSPipe failed with code {result['vspipe_returncode']}"
                if details:
                    message = f"{message}: {details}"
                raise RuntimeError(message)
            if result["x264_returncode"] != 0:
                raise RuntimeError(
                    f"x264-7mod failed with code {result['x264_returncode']}: "
                    + result["stderr"][-500:]
                )

            self._run_muxer(temp_raw, temp_output, fps)
            os.replace(temp_output, output_path)
        finally:
            # A failed/cancelled encode used to litter the export dir with
            # .tmp.264 / .tmp.mp4 leftovers — clean up on every exit path.
            for leftover in (temp_raw, temp_output):
                try:
                    if os.path.exists(leftover):
                        os.remove(leftover)
                except OSError:
                    pass

    def _run_encode_pipeline(
        self,
        script_path: str,
        output_path: str,
        is_cancelled: Optional[Callable[[], bool]] = None,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> dict[str, object]:
        vspipe_cmd = build_vspipe_command(self.toolchain.vspipe_path, script_path)
        x264_cmd = build_x264_command(self.toolchain.x264_path, output_path)
        env = build_media_subprocess_env(self.toolchain.vspipe_path)
        popen_kwargs = {
            "env": env,
            "creationflags": subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        }
        if not popen_kwargs["creationflags"]:
            popen_kwargs.pop("creationflags")

        with _suppress_windows_error_dialogs():
            vspipe = subprocess.Popen(
                vspipe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **popen_kwargs
            )
            x264 = subprocess.Popen(
                x264_cmd,
                stdin=vspipe.stdout,
                # x264 writes the H.264 bitstream to its --output file; its stdout is
                # unused. Piping it to an unread PIPE was pure deadlock surface -> DEVNULL.
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                **popen_kwargs,
            )
        if vspipe.stdout is not None:
            vspipe.stdout.close()
        self.active_processes = [vspipe, x264]

        # Drain both children's stderr concurrently in background threads. vspipe emits
        # per-frame progress and x264 (veryslow) prints periodic progress to stderr; on a
        # long encode a full OS pipe buffer blocks the writing child, which stalls the
        # pipeline and hangs the poll loop below. The Python subprocess docs warn that a
        # poll/wait loop with unread PIPEs deadlocks — reader threads are the fix.
        stderr_bufs: dict[str, bytes] = {}

        def _drain(proc, key):
            try:
                stderr_bufs[key] = proc.stderr.read() if proc.stderr is not None else b""
            except Exception:
                stderr_bufs[key] = b""
            finally:
                try:
                    if proc.stderr is not None:
                        proc.stderr.close()
                except Exception:
                    pass

        def _drain_vspipe_with_progress(proc, key):
            """Drain vspipe stderr AND surface `Frame: n/total` progress.

            The plain _drain above swallowed the -p output, leaving the export
            dialog frozen at a fixed percentage for the whole encode. VSPipe
            refreshes the progress line with \r, so split on [\r\n].
            """
            chunks: list[bytes] = []
            pending = b""
            try:
                stream = proc.stderr
                if stream is None:
                    return
                while True:
                    chunk = stream.read1(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    pending += chunk
                    *complete, pending = re.split(rb"[\r\n]", pending)
                    for line in complete:
                        match = _VSPIPE_PROGRESS_RE.search(line)
                        if match:
                            try:
                                progress_cb(int(match.group(1)), int(match.group(2)))
                            except Exception:
                                pass
            except Exception:
                pass
            finally:
                stderr_bufs[key] = b"".join(chunks)
                try:
                    if proc.stderr is not None:
                        proc.stderr.close()
                except Exception:
                    pass

        vspipe_drain = (
            _drain_vspipe_with_progress if progress_cb is not None else _drain
        )
        readers = [
            threading.Thread(target=vspipe_drain, args=(vspipe, "vspipe"), daemon=True),
            threading.Thread(target=_drain, args=(x264, "x264"), daemon=True),
        ]
        for reader in readers:
            reader.start()

        try:
            while x264.poll() is None:
                if is_cancelled and is_cancelled():
                    self.terminate_active_processes()
                    if os.path.exists(output_path):
                        os.remove(output_path)
                    raise InterruptedError("Export cancelled")
                time.sleep(0.1)
            vspipe.wait(timeout=5)
        finally:
            self.terminate_active_processes()

        for reader in readers:
            reader.join(timeout=5)

        vspipe_stderr = stderr_bufs.get("vspipe", b"")
        x264_stderr = stderr_bufs.get("x264", b"")
        return {
            "vspipe_returncode": int(vspipe.returncode or 0),
            "x264_returncode": int(x264.returncode or 0),
            "stderr": (
                x264_stderr.decode("utf-8", errors="replace")
                + vspipe_stderr.decode("utf-8", errors="replace")
            ),
        }

    def _run_muxer(self, raw_h264_path: str, output_path: str, fps: float) -> None:
        cmd = build_mux_command(self.toolchain.muxer_path, raw_h264_path, output_path, fps)
        run_kwargs = {
            "capture_output": True,
            "env": build_media_subprocess_env(self.toolchain.muxer_path),
            "text": True,
            "timeout": 120,
        }
        if sys.platform == "win32":
            run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        with _suppress_windows_error_dialogs():
            result = subprocess.run(cmd, **run_kwargs)
        if result.returncode != 0:
            raise RuntimeError(f"MP4 muxer failed: {result.stderr[-500:]}")


@contextmanager
def _suppress_windows_error_dialogs():
    if sys.platform != "win32":
        yield
        return
    import ctypes

    kernel32 = ctypes.windll.kernel32
    old_mode = kernel32.SetErrorMode(
        0x0001 | 0x0002 | 0x8000
    )
    try:
        yield
    finally:
        kernel32.SetErrorMode(old_mode)


__all__ = [
    "MediaEncoder",
    "MediaToolchain",
    "build_vspipe_command",
    "build_x264_command",
    "build_mp4box_mux_command",
    "build_lsmash_mux_command",
    "build_mux_command",
    "write_vpy_script",
]
