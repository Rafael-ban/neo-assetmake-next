"""Asset export service."""

from __future__ import annotations

import logging
import os
import struct
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Tuple

import numpy as np

try:
    import cv2

    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from config.epconfig import EPConfig
from core.file_utils import atomic_write_json
from core.media_pipeline import MediaEncoder, write_vpy_script
from core.media_tools import MediaToolchain

logger = logging.getLogger(__name__)


class ExportType(Enum):
    """Export task type."""

    LOGO = "logo"
    OVERLAY = "overlay"
    LOOP_VIDEO = "loop"
    INTRO_VIDEO = "intro"
    ICON = "icon"


@dataclass
class VideoExportParams:
    """Video export parameters."""

    video_path: str
    cropbox: Tuple[int, int, int, int]
    start_frame: int
    end_frame: int
    fps: float
    resolution: str = "360x640"
    is_image: bool = False
    rotation: int = 0


@dataclass
class ExportTask:
    """One export task."""

    export_type: ExportType
    output_path: str
    data: Any


class ExportWorker(QThread):
    """Background export worker."""

    progress_updated = pyqtSignal(int, str)
    export_completed = pyqtSignal(str)
    export_failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tasks: list[ExportTask] = []
        self._output_dir = ""
        self._cancelled = False
        self._epconfig: Optional[EPConfig] = None
        self._resolution = "360x640"
        self._media_toolchain = MediaToolchain.discover()
        self._media_encoder: Optional[MediaEncoder] = None

    def setup(
        self,
        tasks: list[ExportTask],
        output_dir: str,
        media_toolchain: Optional[MediaToolchain] = None,
        epconfig: Optional[EPConfig] = None,
        resolution: str = "360x640",
    ) -> None:
        self._tasks = tasks
        self._output_dir = output_dir
        self._media_toolchain = media_toolchain or MediaToolchain.discover()
        self._epconfig = epconfig
        self._resolution = resolution
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        if self._media_encoder is not None:
            self._media_encoder.terminate_active_processes()

    def run(self) -> None:
        try:
            total_tasks = len(self._tasks)
            if total_tasks == 0 and not self._epconfig:
                self.export_completed.emit("No export tasks")
                return

            os.makedirs(self._output_dir, exist_ok=True)

            for index, task in enumerate(self._tasks):
                if self._cancelled:
                    self.export_failed.emit("Export cancelled")
                    return
                base_progress = int((index / (total_tasks + 1)) * 100)
                try:
                    self._execute_task(task, base_progress, total_tasks)
                except Exception as exc:
                    logger.exception("Export task %s failed", task.export_type.value)
                    self.export_failed.emit(f"Export {task.export_type.value} failed: {exc}")
                    return

            if self._epconfig:
                self.progress_updated.emit(95, "Generating epconfig.json...")
                self._generate_epconfig()

            self.progress_updated.emit(100, "Export completed")
            self.export_completed.emit(f"Exported to {self._output_dir}")
        except Exception as exc:
            logger.exception("Export failed")
            self.export_failed.emit(f"Export failed: {exc}")

    def _execute_task(self, task: ExportTask, base_progress: int, total_tasks: int) -> None:
        output_path = os.path.join(self._output_dir, task.output_path)

        if task.export_type == ExportType.LOGO:
            self.progress_updated.emit(base_progress, f"Exporting {task.output_path}...")
            self._export_argb(output_path, task.data)
        elif task.export_type == ExportType.OVERLAY:
            self.progress_updated.emit(base_progress, f"Exporting {task.output_path}...")
            self._export_argb(output_path, task.data)
        elif task.export_type == ExportType.ICON:
            self.progress_updated.emit(base_progress, f"Exporting {task.output_path}...")
            self._export_icon(output_path, task.data)
        elif task.export_type in (ExportType.LOOP_VIDEO, ExportType.INTRO_VIDEO):
            self.progress_updated.emit(base_progress, f"Exporting {task.output_path}...")
            self._export_video(output_path, task.data, base_progress)

    def _export_icon(self, output_path: str, mat: np.ndarray) -> None:
        if not HAS_CV2:
            raise RuntimeError("opencv-python is required to export PNG icons")
        success, encoded = cv2.imencode(".png", mat)
        if not success:
            raise RuntimeError("Failed to encode icon PNG")
        with open(output_path, "wb") as fh:
            fh.write(encoded.tobytes())

    def _export_argb(self, output_path: str, mat: np.ndarray) -> None:
        if HAS_CV2:
            mat = cv2.rotate(mat, cv2.ROTATE_180)
        else:
            mat = np.rot90(mat, 2)
        mat = mat.astype(np.uint8)
        height, width = mat.shape[:2]
        channels = mat.shape[-1] if len(mat.shape) == 3 else 1

        with open(output_path, "wb") as fh:
            for y in range(height):
                if self._cancelled:
                    raise InterruptedError("Export cancelled")
                for x in range(width):
                    if channels == 4:
                        b, g, r, a = mat[y, x]
                    elif channels == 3:
                        b, g, r = mat[y, x]
                        a = 255
                    else:
                        b = g = r = mat[y, x]
                        a = 255
                    fh.write(struct.pack("BBBB", int(b), int(g), int(r), int(a)))

    def _export_video(
        self,
        output_path: str,
        params: VideoExportParams,
        base_progress: int,
    ) -> None:
        missing = self._media_toolchain.missing_for_export()
        if missing:
            raise RuntimeError("Missing media tools: " + ", ".join(missing))

        script_path = os.path.join(
            self._output_dir,
            f"_{os.path.splitext(os.path.basename(output_path))[0]}.vpy",
        )
        try:
            self.progress_updated.emit(base_progress + 10, "Generating VapourSynth script...")
            write_vpy_script(script_path, params)

            self.progress_updated.emit(base_progress + 50, "Encoding video...")
            self._media_encoder = MediaEncoder(self._media_toolchain)
            self._media_encoder.encode_vpy_to_mp4(
                script_path,
                output_path.replace("\\", "/"),
                params.fps,
                is_cancelled=lambda: self._cancelled,
            )
        finally:
            self._media_encoder = None
            if os.path.exists(script_path):
                os.remove(script_path)

    def _generate_epconfig(self) -> None:
        if not self._epconfig:
            return
        config_path = os.path.join(self._output_dir, "epconfig.json")
        atomic_write_json(config_path, self._epconfig.to_dict(normalize_paths=True), indent=4)
        logger.info("Generated config: %s", config_path)


class ExportService(QObject):
    """High-level export service."""

    progress_updated = pyqtSignal(int, str)
    export_completed = pyqtSignal(str)
    export_failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: Optional[ExportWorker] = None
        self._media_toolchain = MediaToolchain.discover()

    @property
    def is_exporting(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    @property
    def media_pipeline_available(self) -> bool:
        if not self._media_toolchain:
            self._media_toolchain = MediaToolchain.discover()
        return not self._media_toolchain.missing_for_export()

    def _missing_media_tools_message(self) -> str:
        missing = self._media_toolchain.missing_for_export()
        return "Missing media tools: " + ", ".join(missing)

    def export_all(
        self,
        output_dir: str,
        epconfig: EPConfig,
        logo_mat: Optional[np.ndarray] = None,
        overlay_mat: Optional[np.ndarray] = None,
        loop_video_params: Optional[VideoExportParams] = None,
        intro_video_params: Optional[VideoExportParams] = None,
        loop_image_path: Optional[str] = None,
    ) -> None:
        if self.is_exporting:
            self.export_failed.emit("An export task is already running")
            return

        tasks: list[ExportTask] = []
        resolution = epconfig.screen.value

        if logo_mat is not None:
            tasks.append(ExportTask(ExportType.ICON, "icon.png", logo_mat))

        if overlay_mat is not None:
            tasks.append(ExportTask(ExportType.OVERLAY, "overlay.argb", overlay_mat))

        if loop_image_path is not None:
            if not self.media_pipeline_available:
                self.export_failed.emit(self._missing_media_tools_message())
                return
            image_params = VideoExportParams(
                video_path=loop_image_path,
                cropbox=(0, 0, 0, 0),
                start_frame=0,
                end_frame=30,
                fps=30.0,
                resolution=resolution,
                is_image=True,
            )
            tasks.append(ExportTask(ExportType.LOOP_VIDEO, "loop.mp4", image_params))
        elif loop_video_params is not None:
            if not self.media_pipeline_available:
                self.export_failed.emit(self._missing_media_tools_message())
                return
            loop_video_params.resolution = resolution
            tasks.append(ExportTask(ExportType.LOOP_VIDEO, "loop.mp4", loop_video_params))

        if intro_video_params is not None:
            if not self.media_pipeline_available:
                self.export_failed.emit(self._missing_media_tools_message())
                return
            intro_video_params.resolution = resolution
            tasks.append(ExportTask(ExportType.INTRO_VIDEO, "intro.mp4", intro_video_params))

        if not tasks:
            self.export_failed.emit("No content to export")
            return

        self._worker = ExportWorker(self)
        self._worker.setup(
            tasks=tasks,
            output_dir=output_dir,
            media_toolchain=self._media_toolchain,
            epconfig=epconfig,
            resolution=resolution,
        )
        self._worker.progress_updated.connect(self.progress_updated.emit)
        self._worker.export_completed.connect(self._on_completed)
        self._worker.export_failed.connect(self._on_failed)
        self._worker.start()

    def cancel(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()

    def _on_completed(self, message: str) -> None:
        self.export_completed.emit(message)
        self._cleanup()

    def _on_failed(self, message: str) -> None:
        self.export_failed.emit(message)
        self._cleanup()

    def _cleanup(self) -> None:
        if self._worker:
            self._worker.deleteLater()
            self._worker = None
