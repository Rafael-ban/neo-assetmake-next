"""VapourSynth and x264-7mod export pipeline."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Optional

from config.constants import get_resolution_spec
from core.media_tools import MediaToolchain, build_media_subprocess_env
from core.video_processor import X264_CLI_ARGS


def build_vspipe_command(vspipe_path: str, script_path: str) -> list[str]:
    """Build a VSPipe command that emits Y4M to stdout."""
    return [vspipe_path, "-c", "y4m", script_path, "-"]


def build_x264_command(
    x264_path: str,
    output_path: str,
    *,
    crf: int = 26,
    preset: str = "veryslow",
) -> list[str]:
    """Build an x264-7mod command that consumes Y4M from stdin."""
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
        *X264_CLI_ARGS,
        "--output",
        output_path,
        "-",
    ]


def _format_fps(fps: float) -> str:
    return f"{float(fps):g}"


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


def _vs_path(path: str) -> str:
    return Path(path).as_posix()


def _quote_vs_string(value: str) -> str:
    return repr(_vs_path(value))


def write_vpy_script(script_path: str | os.PathLike[str], params) -> None:
    """Write a VapourSynth script for one export track."""
    spec = get_resolution_spec(params.resolution)
    target_w = int(spec["width"])
    target_h = int(spec["height"])
    padded_w = int(spec["padded_width"])
    padded_h = int(spec["padded_height"])
    padding_side = spec["padding_side"]
    rotate_180 = bool(spec["rotate_180"])
    start_frame = max(0, int(params.start_frame))
    end_frame = max(start_frame, int(params.end_frame))
    crop_x, crop_y, crop_w, crop_h = [max(0, int(v)) for v in params.cropbox]
    # Snap rotation to a cardinal angle so export can never diverge from the preview
    # (which rotates via cv2.ROTATE_*). Idempotent; keeps existing saved projects working.
    rotation = (round(int(params.rotation) / 90) * 90) % 360

    lines = [
        "import vapoursynth as vs",
        "core = vs.core",
    ]

    if params.is_image:
        lines.extend(
            [
                f"clip = core.imwri.Read({_quote_vs_string(params.video_path)})",
                "clip = clip if clip.format.id == vs.RGB24 else core.resize.Bicubic(clip, format=vs.RGB24)",
                f"clip = core.std.Loop(clip, times={max(1, end_frame - start_frame)})",
            ]
        )
    else:
        lines.extend(
            [
                f"clip = core.lsmas.LWLibavSource({_quote_vs_string(params.video_path)})",
                f"clip = clip[{start_frame}:{end_frame}]",
            ]
        )
        # Match cv2.ROTATE_* used by the preview (video_preview.py). core.std.Transpose
        # is a matrix transpose (reflection across the main diagonal), NOT a rotation:
        # a true 90deg clockwise = Transpose then FlipHorizontal; 270deg (counter-
        # clockwise) = Transpose then FlipVertical; 180deg == Turn180. rotation is already
        # snapped to {0,90,180,270} above, so no arbitrary-angle branch is needed.
        if rotation == 90:
            lines.append("clip = core.std.FlipHorizontal(core.std.Transpose(clip))")
        elif rotation == 180:
            lines.append("clip = core.std.Turn180(clip)")
        elif rotation == 270:
            lines.append("clip = core.std.FlipVertical(core.std.Transpose(clip))")

        if crop_w > 0 and crop_h > 0:
            # Clamp the crop box to the ACTUAL post-rotation clip dimensions at eval
            # time and force every value even. VapourSynth CropAbs on a YUV420 (4:2:0)
            # clip rejects odd offsets/sizes AND a box extending past the frame; either
            # aborts the whole encode. Computing it against clip.width/height in the
            # script keeps it correct regardless of source size or rotation.
            lines.append(f"_cx = min(max(0, {crop_x}), clip.width - 2) & ~1")
            lines.append(f"_cy = min(max(0, {crop_y}), clip.height - 2) & ~1")
            lines.append(f"_cw = min({crop_w}, clip.width - _cx) & ~1")
            lines.append(f"_ch = min({crop_h}, clip.height - _cy) & ~1")
            lines.append("if _cw >= 2 and _ch >= 2:")
            lines.append("    clip = core.std.CropAbs(clip, width=_cw, height=_ch, left=_cx, top=_cy)")

    # The image branch feeds an RGB24 clip here; RGB->YUV requires a colour matrix or
    # VapourSynth raises "Matrix must be specified" and the whole image-loop export fails.
    # The video branch is already YUV, where matrix_s must NOT be passed.
    _resize_matrix = ", matrix_s='709'" if params.is_image else ""
    lines.append(
        f"clip = core.resize.Bicubic(clip, width={target_w}, height={target_h}, "
        f"format=vs.YUV420P8{_resize_matrix})"
    )

    if padding_side == "right":
        lines.append(f"clip = core.std.AddBorders(clip, right={padded_w - target_w})")
    elif padding_side == "bottom":
        lines.append(f"clip = core.std.AddBorders(clip, bottom={padded_h - target_h})")

    if rotate_180:
        lines.append("clip = core.std.Turn180(clip)")

    lines.append("clip.set_output()")

    Path(script_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


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

        result = self._run_encode_pipeline(script_path, temp_raw, is_cancelled)
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
        if os.path.exists(temp_raw):
            os.remove(temp_raw)

    def _run_encode_pipeline(
        self,
        script_path: str,
        output_path: str,
        is_cancelled: Optional[Callable[[], bool]] = None,
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

        readers = [
            threading.Thread(target=_drain, args=(vspipe, "vspipe"), daemon=True),
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
