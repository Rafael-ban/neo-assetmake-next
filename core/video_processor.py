"""Video metadata helpers and shared x264 parameter defaults."""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from config.vs_runtime import load_vs_runtime
from core.vs_runtime.job import (
    CropSpec,
    OutputSpec,
    PathSpec,
    RenderJob,
    SourceSpec,
    TimelineSpec,
    TransformSpec,
    write_render_job,
)
from core.vs_runtime.script_header import parse_script_header
from core.vs_runtime.session import (
    RenderSession,
    ScriptSelection,
    compute_job_sha256,
    compute_script_bundle_hash,
)
from core.vs_runtime.vs_loader import compute_runtime_fingerprint
from core.vs_runtime.worker_process import SyncVSWorkerProcess
from utils.file_utils import get_app_dir

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
    """用短生命周期 worker/default vpy 读取精确 editor metadata。"""
    source = Path(input_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    app_dir = Path(get_app_dir()).resolve()
    script = app_dir / "resources" / "vapoursynth" / "default_pipeline.vpy"
    header = parse_script_header(script)
    selection = ScriptSelection.from_header(
        script, header, compute_script_bundle_hash(script)
    )
    runtime = load_vs_runtime()
    fingerprint = compute_runtime_fingerprint(app_dir, runtime)
    with tempfile.TemporaryDirectory(prefix="assetmaker-vs-probe-") as temp:
        cache = Path(temp).resolve()
        job = RenderJob(
            api_version=1,
            epoch=1,
            track="loop",
            project_root=str(source.parent),
            source=SourceSpec(
                path=str(source), kind="video", virtual_frame_count=None
            ),
            timeline=TimelineSpec(start_frame=0, end_frame=None, fps=None),
            transform=TransformSpec(
                rotation=0,
                crop=CropSpec(
                    coordinate_space="post_rotation_source_pixels",
                    x=0,
                    y=0,
                    width=0,
                    height=0,
                ),
            ),
            output=OutputSpec.from_profile("360x640"),
            paths=PathSpec(cache_dir=str(cache)),
        )
        job_path = write_render_job(job)
        session = RenderSession(
            epoch=1,
            track="loop",
            selection=selection,
            job_path=str(job_path),
            job_sha256=compute_job_sha256(job_path),
            runtime_fingerprint=fingerprint,
        )
        worker = SyncVSWorkerProcess(app_dir=app_dir)
        try:
            worker.start(timeout_ms=runtime.worker.startup_timeout_ms)
            metadata = worker.load(
                session, timeout_ms=runtime.worker.startup_timeout_ms
            )
            if metadata.mode != "compatible" or metadata.editor is None:
                raise RuntimeError("内置 pipeline 未返回 editor output 1 元数据")
            node = metadata.editor
            worker.shutdown(timeout_ms=runtime.worker.shutdown_timeout_ms)
        finally:
            worker.close()
    fps_num = node.fps_num
    fps_den = node.fps_den
    fps = fps_num / fps_den
    total_frames = node.num_frames
    duration = total_frames / fps if fps > 0 else 0.0
    return VideoInfo(
        width=node.width,
        height=node.height,
        duration=duration,
        fps=fps,
        total_frames=total_frames,
    )


class VideoProcessor:
    """通过独立 worker 探测视频元数据。"""

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
