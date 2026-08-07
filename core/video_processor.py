"""Video metadata helpers and shared x264 parameter defaults."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)

X264_PARAMS = (
    "partitions=all"
    ":rc-lookahead=150"
    ":bframes=16:b-adapt=2"
    ":me=umh:subme=9:merange=48"
    ":no-fast-pskip=1:direct=auto:no-weightb=0"
    ":keyint=300:min-keyint=5:ref=16"
    ":chroma-qp-offset=-3"
    ":aq-mode=1:aq-strength=0.6:trellis=2"
    ":deblock=1,1:psy-rd=0.4,0"
)

X264_CLI_ARGS = [
    "--partitions",
    "all",
    "--rc-lookahead",
    "150",
    "--bframes",
    "16",
    "--b-adapt",
    "2",
    "--me",
    "umh",
    "--subme",
    "9",
    "--merange",
    "48",
    "--no-fast-pskip",
    "--direct",
    "auto",
    "--keyint",
    "300",
    "--min-keyint",
    "5",
    "--ref",
    "16",
    "--chroma-qp-offset",
    "-3",
    "--aq-mode",
    "1",
    "--aq-strength",
    "0.6",
    "--trellis",
    "2",
    "--deblock",
    "1:1",
    "--psy-rd",
    "0.4:0",
]

@dataclass
class VideoInfo:
    """Basic video stream information."""

    width: int
    height: int
    duration: float
    fps: float
    total_frames: int


def probe_video_info(input_path: str) -> VideoInfo:
    """Read metadata straight off a VapourSynth clip (in-process, EXACT).

    This replaced an mpv JSON-IPC probe that could only report *estimates*: it
    fell back to ``round(duration * fps)`` for the frame count and to a
    hardcoded 30.0 for fps, so the preview's frame indices only ever
    approximated the export's ``clip[start:end]``. A VS clip carries the real
    values as attributes (VS R73 stub: ``width``/``height``/``fps_num``/
    ``fps_den``/``num_frames``), and it is the SAME source node the export
    uses, so preview and export cannot disagree about frame indices.

    Raises VSUnavailable when VapourSynth or a required plugin is missing.
    """
    from core.vs_engine import source_clip

    clip = source_clip(input_path)
    fps_num = int(getattr(clip, "fps_num", 0) or 0)
    fps_den = int(getattr(clip, "fps_den", 0) or 0)
    fps = (fps_num / fps_den) if fps_num > 0 and fps_den > 0 else 30.0
    total_frames = max(1, int(clip.num_frames))
    duration = total_frames / fps if fps > 0 else 0.0
    return VideoInfo(
        width=int(clip.width),
        height=int(clip.height),
        duration=duration,
        fps=fps,
        total_frames=total_frames,
    )


class VideoProcessor:
    """Probe video metadata through the in-process VapourSynth source node."""

    def get_video_info(self, input_path: str) -> Optional[VideoInfo]:
        """Return metadata for the first video stream, or None on failure.

        A file VapourSynth cannot open now fails *here*, at load time, instead
        of previewing fine under mpv and only blowing up during export.
        """
        if not Path(input_path).exists():
            logger.error("Video file does not exist: %s", input_path)
            return None
        try:
            return probe_video_info(input_path)
        except Exception as exc:
            logger.error("metadata probe failed for %s: %s", input_path, exc)
            return None


class MetadataProbeWorker(QThread):
    """Probe media metadata off the GUI thread.

    Still a worker thread even though the probe is now in-process: ``lsmas``
    builds a full ``.lwi`` index the first time it opens a file, which blocks
    the calling thread just as long as the old mpv JSON-IPC probe did (that one
    accumulated up to ~46s of ``waitFor*`` timeouts on a dead pipe — PyQt6
    QtNetwork.pyi:202-205, QtCore.pyi:6985-6988 — and froze the whole UI on
    every video load because it ran inline on the GUI thread).
    """

    result = pyqtSignal(object)  # VideoInfo
    failed = pyqtSignal(str)

    def __init__(self, input_path: str, parent=None) -> None:
        super().__init__(parent)
        self.input_path = input_path
        self.epoch = -1  # set by the owner to correlate results to a load

    def run(self) -> None:  # executed on the worker thread
        try:
            if not Path(self.input_path).exists():
                self.failed.emit(f"文件不存在: {self.input_path}")
                return
            self.result.emit(probe_video_info(self.input_path))
        except Exception as exc:
            logger.error(
                "metadata probe failed for %s: %s", self.input_path, exc
            )
            self.failed.emit(str(exc))
