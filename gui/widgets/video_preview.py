"""Video preview widget backed by the in-process VapourSynth render graph."""

from __future__ import annotations

import logging
import os
from typing import Optional, Tuple, TYPE_CHECKING

import numpy as np

try:
    import cv2

    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from PyQt6.QtCore import QPoint, QSize, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import (
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import CaptionLabel, PushButton, Slider, setCustomStyleSheet

from core.media_tools import MediaToolchain
from core.video_processor import MetadataProbeWorker

if TYPE_CHECKING:
    from config.epconfig import EPConfig

# In-flight metadata probe workers. A superseded load drops the widget's own
# reference to its worker; without this keep-alive the unparented QThread's
# wrapper gets garbage-collected while the thread is still running, which
# destroys the C++ QThread mid-run and aborts the process.
_ACTIVE_PROBE_WORKERS: list = []


def _release_probe_worker(worker):
    try:
        _ACTIVE_PROBE_WORKERS.remove(worker)
    except ValueError:
        return  # already released
    worker.deleteLater()

logger = logging.getLogger(__name__)

DEFAULT_TARGET_WIDTH = 360
DEFAULT_TARGET_HEIGHT = 640

# Smallest crop side (px) the ratio lock will shrink to. Below ~64px the
# integer rounding of w/h can no longer express a ratio like 0.5625 or 0.6667
# accurately (w=2 -> 2/4 = 0.5, i.e. 11% off), so the lock would appear to
# "break" at extreme zoom-out. 64px keeps the error well under 1%.
_MIN_CROP_SIDE = 64


class _PreviewLabel(QLabel):
    def __init__(self, owner: "VideoPreviewWidget"):
        super().__init__(owner)
        self._owner = owner

    def paintEvent(self, event):
        super().paintEvent(event)
        self._owner._paint_cropbox(self)

    def mousePressEvent(self, event: QMouseEvent):
        self._owner._handle_mouse_press(self, event)

    def mouseMoveEvent(self, event: QMouseEvent):
        self._owner._handle_mouse_move(self, event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._owner._handle_mouse_release(event)


class VideoPreviewWidget(QWidget):
    """Preview media, expose crop/trim state, and keep the legacy public API."""

    cropbox_changed = pyqtSignal(int, int, int, int)
    frame_changed = pyqtSignal(int)
    playback_state_changed = pyqtSignal(bool)
    video_loaded = pyqtSignal(int, float)
    load_failed = pyqtSignal(str)  # async metadata probe failure
    rotation_changed = pyqtSignal(int)

    DRAG_NONE = 0
    DRAG_MOVE = 1
    DRAG_RESIZE_TL = 2
    DRAG_RESIZE_TR = 3
    DRAG_RESIZE_BL = 4
    DRAG_RESIZE_BR = 5

    def __init__(self, parent=None):
        super().__init__(parent)
        self.video_path = ""
        self.video_fps = 30.0
        self.video_width = 0
        self.video_height = 0
        self.total_frames = 0
        self.current_frame_index = 0
        self.current_frame: Optional[np.ndarray] = None

        # Load-generation token: bumped by every new load/clear. Deferred
        # continuations (probe results, frame deliveries) capture it at
        # schedule time and no-op when stale, so a slow continuation from
        # load A can never corrupt load B's session. Still load-bearing:
        # lsmas index building is slow and asynchronous.
        self._load_epoch = 0
        self._probe_worker: Optional[MetadataProbeWorker] = None
        # In-process VapourSynth preview: frames are pulled on a VS worker
        # thread and delivered here by Qt signals.
        self._frame_requester = None
        self._vs_active = False
        self._media_toolchain = MediaToolchain.discover()
        self._has_video = False
        self._loop_frame: Optional[np.ndarray] = None
        self._preview_mode = False
        self._epconfig: Optional["EPConfig"] = None
        self._overlay_renderer = None
        self._rotation = 0
        self._zoom_factor = 1.0
        # Point keeps pixel edges hard, so single source pixels stay countable
        # at 10000% — that is what the zoom is for (checking crop boundaries).
        self._zoom_kernel = "Point"
        self._zoom_pan = (0.5, 0.5)  # normalised centre of the magnified window

        self.is_playing = False
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._on_timer_tick)

        self.target_width = DEFAULT_TARGET_WIDTH
        self.target_height = DEFAULT_TARGET_HEIGHT
        self.target_aspect_ratio = self.target_width / self.target_height
        self.cropbox = [0, 0, self.target_width, self.target_height]

        self.display_scale = 1.0
        self.display_offset_x = 0
        self.display_offset_y = 0
        self.drag_mode = self.DRAG_NONE
        self.drag_start_pos: Optional[QPoint] = None
        self.drag_start_cropbox: list[int] = []
        self.handle_size = 15

        self._setup_ui()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self._display_stack = QStackedWidget()
        self._display_stack.setMinimumSize(320, 180)
        self._display_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        self.video_label = _PreviewLabel(self)
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setText("No media loaded")
        self.video_label.setMouseTracking(True)
        setCustomStyleSheet(
            self.video_label,
            "background-color: #1a1a1a; border: none; border-radius: 8px; "
            "color: #888; font-size: 14px; font-weight: 500;",
            "background-color: #0a0a0a; border: none; border-radius: 8px; "
            "color: #666; font-size: 14px; font-weight: 500;",
        )
        self._display_stack.addWidget(self.video_label)

        layout.addWidget(self._display_stack)
        self.info_label = CaptionLabel("Frame 0/0 | Crop: (0, 0, 0, 0)")
        setCustomStyleSheet(
            self.info_label,
            "color: #999; padding: 4px 10px; background-color: transparent; border: none;",
            "color: #777; padding: 4px 10px; background-color: transparent; border: none;",
        )
        layout.addWidget(self.info_label)

        # Zoom controls
        zoom_container = QWidget()
        zoom_layout = QHBoxLayout(zoom_container)
        zoom_layout.setContentsMargins(10, 0, 10, 0)
        zoom_layout.setSpacing(10)

        # 对数刻度:slider 0..200 → 10^(v/100) → 1.0x..100.0x(100%..10000%)
        self.zoom_slider = Slider(Qt.Orientation.Horizontal)
        self.zoom_slider.setMinimum(0)
        self.zoom_slider.setMaximum(200)
        self.zoom_slider.setValue(0)
        self.zoom_slider.valueChanged.connect(self._on_zoom_slider_changed)
        zoom_layout.addWidget(CaptionLabel("缩放:"))
        zoom_layout.addWidget(self.zoom_slider, 1)

        self.zoom_label = CaptionLabel("100%")
        self.zoom_label.setMinimumWidth(60)
        zoom_layout.addWidget(self.zoom_label)

        for percent in (100, 1000, 10000):
            btn = PushButton(f"{percent}%")
            btn.setMaximumWidth(70)
            btn.clicked.connect(lambda checked, p=percent: self._set_zoom_percent(p))
            zoom_layout.addWidget(btn)

        layout.addWidget(zoom_container)

    def _teardown_media(self, sync_shutdown: bool = False):
        """Release the preview backend. No child process, so nothing to wait on.

        This used to tear down an mpv QProcess + QLocalSocket: a `quit`
        handshake, detaching the process into a module-level keep-alive list,
        and two escalating kill timers — all to dodge the blocking
        QProcess.waitForFinished (PyQt6 QtCore.pyi:6985) that froze the UI for
        seconds on every video switch. Dropping the VS graph is synchronous and
        cheap, so the whole apparatus is gone.
        """
        self._stop_vs_preview(permanent=sync_shutdown)
        self._has_video = False

    # ------------------------------------------------------------------
    # VapourSynth frame-request preview (replaces the mpv IPC player)
    # ------------------------------------------------------------------

    def _is_image_source(self) -> bool:
        ext = os.path.splitext(self.video_path)[1].lower()
        return ext in (".png", ".jpg", ".jpeg", ".bmp", ".webp")

    def _build_export_params(self):
        """The same VideoExportParams the export would use for this media.

        Reusing the export's parameter object (rather than a preview-only
        approximation) is what keeps 预览 and 导出 on one code path.
        """
        from core.export_service import VideoExportParams

        return VideoExportParams(
            video_path=self.video_path,
            cropbox=tuple(self.cropbox),
            rotation=self._rotation,
            start_frame=0,
            end_frame=max(1, self.total_frames),
            resolution=f"{self.target_width}x{self.target_height}",
            fps=self.video_fps,
            is_image=self._is_image_source(),
        )

    def _use_vs_preview(self) -> bool:
        """True when the in-process VapourSynth core is usable for preview.

        The core must have been warmed BEFORE PyQt6 loaded (main.py /
        tests.qt_harness call vs_engine.prewarm()); initializing it after Qt
        segfaults on this bundle, so we never try to build it lazily here.
        """
        try:
            from core import vs_engine

            if vs_engine._core is None:      # not prewarmed -> do not risk it
                return False
            return not vs_engine.missing_plugins()
        except Exception:
            return False

    def _ensure_requester(self):
        from core.vs_player import FrameRequester

        if self._frame_requester is None:
            self._frame_requester = FrameRequester(self)
            self._frame_requester.frame_ready.connect(self._on_vs_frame_ready)
            self._frame_requester.frame_failed.connect(self._on_vs_frame_failed)
        return self._frame_requester

    def _start_vs_preview(self, path: str) -> bool:
        """Point the requester at a source graph and pull the first frame."""
        if not self._use_vs_preview():
            return False
        try:
            clip = self._build_preview_clip()
            requester = self._ensure_requester()
            requester.set_clip(clip, self._load_epoch)
            self._vs_active = True
            # Display page 0 (the QLabel) — with no mpv child window there is
            # nothing to occlude the painted cropbox, so cropping is live.
            self._display_stack.setCurrentIndex(0)
            requester.request(self.current_frame_index)
            return True
        except Exception as exc:
            logger.warning("VapourSynth preview unavailable for %s: %s", path, exc)
            self._vs_active = False
            return False

    def _build_preview_clip(self):
        """The clip the preview should show for the current mode.

        Preview mode ("导出预览") renders the REAL export graph converted back
        to RGB, so what is on screen is the pixels the device gets — including
        the export's own resizer, padding and final 180° turn. Edit mode shows
        the whole (rotated) source with the crop rectangle painted over it.

        Zoom is a final viewport magnifier stage (see
        ``vs_graph.apply_preview_zoom``) — it crops the visible window first, so
        cost stays flat instead of scaling with the factor.
        """
        from core.vs_graph import (apply_preview_zoom, build_display_graph,
                                   build_source_graph)

        if not self._preview_mode:
            clip = build_source_graph(self.video_path, is_image=self._is_image_source(),
                                      rotation=self._rotation)
        else:
            clip = build_display_graph(self._build_export_params())

        if self._zoom_factor > 1.0:
            clip = apply_preview_zoom(
                clip,
                zoom_factor=self._zoom_factor,
                viewport=self._viewport_size(),
                pan=self._zoom_pan,
                kernel=self._zoom_kernel,
            )
        return clip

    def _viewport_size(self) -> Tuple[int, int]:
        """Visible preview area in device pixels (what zoom renders into)."""
        size = self.video_label.size()
        dpr = self.video_label.devicePixelRatioF()
        return (max(2, round(size.width() * dpr)),
                max(2, round(size.height() * dpr)))

    def _rebuild_vs_graph(self) -> None:
        """Re-derive the graph (rotation / crop / preview-mode change)."""
        if not self._vs_active or not self.video_path:
            return
        try:
            self._ensure_requester().set_clip(self._build_preview_clip(),
                                              self._load_epoch)
            self._request_current_frame()
        except Exception as exc:
            logger.warning("VapourSynth graph rebuild failed: %s", exc)

    def _request_current_frame(self, *, coalesce: bool = False) -> None:
        if not self._vs_active or self._frame_requester is None:
            return
        self._frame_requester.request(self.current_frame_index, coalesce=coalesce)

    def _on_vs_frame_ready(self, epoch: int, index: int, array) -> None:
        """A decoded frame arrived (queued from a VS worker thread)."""
        if epoch != self._load_epoch or array is None:
            return  # superseded load: drop the late frame
        self.current_frame = array
        if self.video_width <= 0 or self.video_height <= 0:
            self.video_height, self.video_width = array.shape[:2]
        self._display_frame(array)

    def _on_vs_frame_failed(self, epoch: int, index: int, message: str) -> None:
        if epoch != self._load_epoch:
            return
        logger.warning("VapourSynth frame %s failed: %s", index, message)

    def _stop_vs_preview(self, *, permanent: bool = False) -> None:
        """Drop the graph. ``permanent`` = the widget itself is going away.

        A reload only needs ``clear()`` (the requester is reused for the next
        media). Window teardown must ``close()`` it: frames already handed to
        VapourSynth still call back, and by then Qt may have deleted this
        widget's children.
        """
        self._vs_active = False
        if self._frame_requester is not None:
            if permanent:
                self._frame_requester.close()
            else:
                self._frame_requester.clear()

    def set_target_resolution(self, width: int, height: int):
        if self.target_width == width and self.target_height == height:
            return
        self.target_width = width
        self.target_height = height
        self.target_aspect_ratio = width / height
        if self.video_width > 0 and self.video_height > 0:
            self._init_cropbox()
            self._refresh_display()
        else:
            # No media yet: re-fit the standing box to the NEW ratio anyway.
            # Previously this branch did nothing, so the aspect ratio changed
            # while the box kept the OLD ratio — and _apply_project_config
            # calls clear() (zeroing video_width) BEFORE set_target_resolution,
            # so this was the common path on every project open.
            self.cropbox = self._fit_cropbox_to_ratio(*self.cropbox) if (
                self.video_width > 0
            ) else [0, 0, width, height]

    def load_video(self, path: str) -> bool:
        """Begin loading a video (asynchronous).

        Returns True when the load was *accepted*; the metadata probe then runs
        on a worker thread and the outcome arrives via ``video_loaded`` /
        ``load_failed``. Returns False only for synchronous rejections (missing
        file, or no usable VapourSynth core). The probe used to be a blocking
        mpv JSON-IPC ``waitFor*`` chain running right here on the GUI thread,
        freezing the UI for up to tens of seconds per load (PyQt6
        QtNetwork.pyi:202-205, QtCore.pyi:6985-6988 — blocking calls).
        """
        logger.info("Loading video: %s", path)
        if not os.path.exists(path):
            self.video_label.setText(f"File not found: {path}")
            return False

        if not self._use_vs_preview():
            self.video_label.setText("VapourSynth 不可用,无法预览")
            return False

        self._load_epoch += 1
        epoch = self._load_epoch
        self._teardown_media()
        self.pause()
        self._loop_frame = None
        self._has_video = False
        self.current_frame = None
        self._display_stack.setCurrentIndex(0)
        self.video_label.setText("正在加载视频元数据…")

        # No parent: a parented QThread would be cascade-deleted with the
        # widget while possibly still running (fatal). The worker keeps itself
        # alive until `finished` -> deleteLater; the bound-method connections
        # below auto-disconnect if this widget's C++ side goes away first.
        worker = MetadataProbeWorker(path)
        worker.epoch = epoch
        self._probe_worker = worker
        worker.result.connect(self._on_probe_worker_result)
        worker.failed.connect(self._on_probe_worker_failed)
        # Keep-alive until the thread actually finishes: `self._probe_worker`
        # alone is not enough, a superseding load overwrites it immediately.
        _ACTIVE_PROBE_WORKERS.append(worker)
        worker.finished.connect(lambda w=worker: _release_probe_worker(w))
        worker.start()
        return True

    def _on_probe_worker_result(self, info):
        worker = self.sender()
        self._on_probe_finished(
            getattr(worker, "epoch", -1), getattr(worker, "input_path", ""), info
        )

    def _on_probe_worker_failed(self, message: str):
        worker = self.sender()
        self._on_probe_failed(getattr(worker, "epoch", -1), message)

    def _on_probe_finished(self, epoch: int, path: str, info):
        if epoch != self._load_epoch:
            return  # superseded by a newer load/clear
        self._probe_worker = None
        self.video_path = path
        self.video_fps = info.fps or 30.0
        self.video_width = max(1, info.width)
        self.video_height = max(1, info.height)
        self.total_frames = max(1, info.total_frames)
        self.current_frame_index = 0
        # current_frame stays None until the first real frame arrives. Never
        # seed a black placeholder: a consumer (截取帧 / crop) reading it early
        # would silently get black pixels, which is exactly the defect the old
        # mpv screenshot round trip produced.
        self.current_frame = None
        self._has_video = True
        self._init_cropbox()
        self._update_info_label()
        if not self._start_vs_preview(path):
            self._has_video = False
            self.video_label.setText("无法建立 VapourSynth 预览")
            self.load_failed.emit("无法建立 VapourSynth 预览")
            return
        self.video_loaded.emit(self.total_frames, self.video_fps)

    def _on_probe_failed(self, epoch: int, message: str):
        if epoch != self._load_epoch:
            return
        self._probe_worker = None
        self._has_video = False
        self.current_frame = None
        self._display_stack.setCurrentIndex(0)
        self.video_label.setText("无法加载视频元数据")
        self.load_failed.emit(message or "无法获取视频元数据")

    def load_static_image_from_file(self, image_path: str) -> bool:
        if not HAS_CV2:
            logger.error("OpenCV is required to load images")
            return False
        if not os.path.exists(image_path):
            logger.error("Image file does not exist: %s", image_path)
            return False
        with open(image_path, "rb") as fh:
            data = np.frombuffer(fh.read(), dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
        if img is None:
            logger.error("Unable to read image: %s", image_path)
            return False
        if len(img.shape) == 3 and img.shape[2] == 4:
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        return self._load_static_frame(img)

    def load_static_image_from_array(self, frame: np.ndarray) -> bool:
        if frame is None:
            return False
        return self._load_static_frame(frame.copy())

    def update_static_frame(self, frame: np.ndarray) -> bool:
        if frame is None:
            return False
        self.current_frame = frame.copy()
        self.video_width = frame.shape[1]
        self.video_height = frame.shape[0]
        self._bound_cropbox()
        self._display_frame(self.current_frame)
        return True

    def load_image_as_loop(
        self, path: str, fps: float = 30.0, duration: float = 5.0
    ) -> bool:
        if not self.load_static_image_from_file(path):
            return False
        self.video_path = path
        self._loop_frame = self.current_frame.copy()
        self.video_fps = fps
        self.total_frames = max(1, int(fps * duration))
        self._has_video = True
        self.video_loaded.emit(self.total_frames, self.video_fps)
        self._update_info_label()
        return True

    def _load_static_frame(self, frame: np.ndarray) -> bool:
        self._load_epoch += 1  # invalidate pending probe/retry continuations
        self.pause()
        self._teardown_media()
        self._loop_frame = None
        self._display_stack.setCurrentIndex(0)
        self.video_width = frame.shape[1]
        self.video_height = frame.shape[0]
        self.current_frame = frame
        self.total_frames = 1
        self.current_frame_index = 0
        self._has_video = False
        self._init_cropbox()
        self._display_frame(frame)
        return True

    def _fit_cropbox_to_ratio(self, x, y, w, h) -> list:
        """Regularize any box to the TARGET aspect ratio inside the frame.

        Single ratio guard for every crop-box write. The export resizes the
        cropped region straight to the target with `core.resize.<kernel>(clip,
        width=..., height=...)` — the VS R73 API has no aspect-preserving
        parameter (stub `resize.Bicubic` takes independent width/height; no
        keep_aspect/pad), so ANY box whose ratio != target is scaled
        anisotropically, i.e. geometrically distorted, in the exported mp4.

        The old `_bound_cropbox` clamped w and h INDEPENDENTLY against the
        frame edges, which silently destroyed the ratio the drag handles had
        just enforced (measured: a landscape source drifted 7.8% -> 40.7% off
        target in a single drag, yielding a 1.41x anisotropic stretch). Here
        both axes are derived from ONE scale factor, so the ratio survives:
        the box shrinks proportionally instead of collapsing on one axis.

        Keeps the box centred on its original centre, as large as fits, and
        even-sized-safe (>= 2px) for the YUV420 crop downstream.
        """
        rotated_w, rotated_h = self._get_rotated_video_size()
        ar = self.target_aspect_ratio if self.target_aspect_ratio > 0 else 1.0
        if rotated_w <= 0 or rotated_h <= 0:
            return [int(x), int(y), int(w), int(h)]

        # Largest target-ratio box that fits the frame.
        if rotated_w / rotated_h > ar:
            max_h = float(rotated_h)
            max_w = max_h * ar
        else:
            max_w = float(rotated_w)
            max_h = max_w / ar

        # A box must be big enough for the ratio to be expressible in integers:
        # at w=2 a 0.5625 target can only round to 2/4 = 0.5 (11% off), and at
        # w=11 a 0.6667 target rounds to 11/16 = 0.6875 (3.1% off). Require at
        # least MIN_CROP_SIDE px on BOTH axes so the rounding error stays well
        # under 1%, while never exceeding what the frame can hold.
        min_w = float(_MIN_CROP_SIDE if ar >= 1.0 else _MIN_CROP_SIDE * ar)
        min_w = max(min_w, float(_MIN_CROP_SIDE) * ar)
        min_w = min(max(2.0, min_w), max_w)

        # Requested size, ratio-locked: honour the larger requested axis but
        # never exceed the fitting box (one shared scale -> ratio preserved).
        req_w = max(2.0, float(w))
        req_h = max(2.0, float(h))
        want_w = max(min_w, min(max_w, max(req_w, req_h * ar)))
        new_w = max(2, int(round(want_w)))
        new_h = max(2, int(round(new_w / ar)))
        # Rounding can push one axis 1px past the frame; step down together
        # while the box is still large enough to express the ratio.
        while (new_w > rotated_w or new_h > rotated_h) and new_w > int(min_w):
            new_w -= 1
            new_h = max(2, int(round(new_w / ar)))
        new_w = min(new_w, rotated_w)
        new_h = min(new_h, rotated_h)

        # Preserve the requested centre, then clamp translation only.
        cx = float(x) + float(w) / 2.0
        cy = float(y) + float(h) / 2.0
        new_x = int(round(cx - new_w / 2.0))
        new_y = int(round(cy - new_h / 2.0))
        new_x = max(0, min(new_x, max(0, rotated_w - new_w)))
        new_y = max(0, min(new_y, max(0, rotated_h - new_h)))
        return [new_x, new_y, new_w, new_h]

    def _init_cropbox(self):
        rotated_w, rotated_h = self._get_rotated_video_size()
        if rotated_w <= 0 or rotated_h <= 0:
            self.cropbox = [0, 0, self.target_width, self.target_height]
            return
        # 75% of the largest fitting target-ratio box, centred. Sizing/centring
        # and the ratio itself are delegated to the single guard so the initial
        # box can no longer differ from every other write path.
        if rotated_w / rotated_h > self.target_aspect_ratio:
            max_w = rotated_h * self.target_aspect_ratio
        else:
            max_w = float(rotated_w)
        want_w = max(2.0, max_w * 0.75)
        self.cropbox = self._fit_cropbox_to_ratio(
            (rotated_w - want_w) / 2.0,
            (rotated_h - want_w / self.target_aspect_ratio) / 2.0,
            want_w,
            want_w / self.target_aspect_ratio,
        )
        self._emit_cropbox_changed()

    def _bound_cropbox(self):
        """Clamp + re-lock the crop box (delegates to the single ratio guard)."""
        rotated_w, rotated_h = self._get_rotated_video_size()
        if rotated_w <= 0 or rotated_h <= 0:
            return
        self.cropbox = self._fit_cropbox_to_ratio(*self.cropbox)

    def _emit_cropbox_changed(self):
        x, y, w, h = self.cropbox
        self.cropbox_changed.emit(x, y, w, h)
        self._update_info_label()

    def _update_info_label(self):
        x, y, w, h = self.cropbox
        rotation = f" | Rotation: {self._rotation}" if self._rotation else ""
        self.info_label.setText(
            f"Frame {self.current_frame_index}/{self.total_frames} | "
            f"Crop: ({x}, {y}, {w}, {h}){rotation}"
        )

    def _display_frame(self, frame: np.ndarray):
        if frame is None:
            return
        display_frame = self._make_display_frame(frame)
        rgb = self._to_rgb(display_frame)
        rgb = np.ascontiguousarray(rgb)
        h, w = rgb.shape[:2]
        qimage = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format.Format_RGB888)
        # Build the pixmap at the device pixel ratio so the preview is sharp on HiDPI
        # displays (scaling only to the logical size renders at half resolution).
        dpr = self.video_label.devicePixelRatioF()
        logical = self.video_label.size()
        physical = QSize(round(logical.width() * dpr), round(logical.height() * dpr))
        pixmap = QPixmap.fromImage(qimage.copy()).scaled(
            physical,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        pixmap.setDevicePixelRatio(dpr)
        self.video_label.setPixmap(pixmap)
        self._update_display_geometry(self.video_label, w, h)
        self.frame_changed.emit(self.current_frame_index)
        self._update_info_label()

    def _make_display_frame(self, frame: np.ndarray) -> np.ndarray:
        if self._vs_active:
            # The VapourSynth graph already applied rotation, and in preview mode
            # the crop/resize/padding too — re-doing either here (with cv2's own
            # resampler) is what used to make 预览 differ from 导出.
            return frame
        rotated = self._apply_rotation(frame)
        if not self._preview_mode:
            return rotated
        x, y, w, h = self.cropbox
        y2 = min(rotated.shape[0], y + h)
        x2 = min(rotated.shape[1], x + w)
        cropped = rotated[max(0, y):y2, max(0, x):x2]
        if cropped.size == 0:
            return rotated
        return cv2.resize(cropped, (self.target_width, self.target_height))

    def _to_rgb(self, frame: np.ndarray) -> np.ndarray:
        if len(frame.shape) == 2:
            return cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB)
        if frame.shape[2] == 4:
            return cv2.cvtColor(frame, cv2.COLOR_BGRA2RGB)
        return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    def _refresh_display(self):
        if self._vs_active and self._preview_mode and self.video_path:
            # 预览模式显示的是导出图,裁剪框一变缓存帧就过期了 —— 必须重新构图取帧,
            # 而不是重画上一帧(那会让"导出预览"落后于实际参数)。
            self._rebuild_vs_graph()
            self._update_info_label()
            return
        if self.current_frame is not None:
            self._display_frame(self.current_frame)
        self._update_info_label()

    def _update_display_geometry(self, widget: QWidget, media_w: int, media_h: int):
        if media_w <= 0 or media_h <= 0:
            self.display_scale = 1.0
            self.display_offset_x = 0
            self.display_offset_y = 0
            return
        area = widget.size()
        scale = min(area.width() / media_w, area.height() / media_h)
        shown_w = int(media_w * scale)
        shown_h = int(media_h * scale)
        self.display_scale = scale if scale > 0 else 1.0
        self.display_offset_x = (area.width() - shown_w) // 2
        self.display_offset_y = (area.height() - shown_h) // 2

    def _paint_cropbox(self, widget: QWidget):
        if self._preview_mode or self.video_width <= 0 or self.video_height <= 0:
            return
        # Zoomed frames are a magnified VIEWPORT WINDOW of the source, not the
        # whole (scaled) source, so display_scale/offset no longer map source
        # coordinates onto the label — the rectangle would be drawn in the wrong
        # place. Zoom is an inspection mode; the box is hidden and locked.
        if self._zoom_factor > 1.0:
            return
        rotated_w, rotated_h = self._get_rotated_video_size()
        self._update_display_geometry(widget, rotated_w, rotated_h)
        x, y, w, h = self.cropbox
        painter = QPainter(widget)
        pen = QPen(Qt.GlobalColor.cyan, 2)
        painter.setPen(pen)
        painter.drawRect(
            int(self.display_offset_x + x * self.display_scale),
            int(self.display_offset_y + y * self.display_scale),
            int(w * self.display_scale),
            int(h * self.display_scale),
        )

    def _on_timer_tick(self):
        if not (self._has_video or self._loop_frame is not None):
            return
        self.current_frame_index += 1
        if self.current_frame_index >= max(1, self.total_frames):
            self.current_frame_index = 0
        # THIS timer is the playback clock. Ask for the frame we just advanced
        # to; coalesce so a slow decode drops frames instead of queueing a
        # backlog that is already stale by the time it lands.
        self._request_current_frame(coalesce=True)
        self.frame_changed.emit(self.current_frame_index)
        self._update_info_label()

    def play(self):
        if self.is_playing or not (self._has_video or self._loop_frame is not None):
            return
        # `self.timer` IS the playback clock (_on_timer_tick advances the index
        # and requests that frame).
        interval = max(1, round(1000 / max(self.video_fps, 1.0)))
        self.timer.start(interval)
        self.is_playing = True
        self.playback_state_changed.emit(True)

    def pause(self):
        self.timer.stop()
        was_playing = self.is_playing
        self.is_playing = False
        if was_playing:
            self.playback_state_changed.emit(False)

    def toggle_play(self):
        if self.is_playing:
            self.pause()
        else:
            self.play()

    def next_frame(self):
        if not (self._has_video or self._loop_frame is not None):
            return
        self.pause()
        self.current_frame_index = min(
            self.current_frame_index + 1, max(0, self.total_frames - 1)
        )
        self._seek_backend_to_current_frame()
        self.frame_changed.emit(self.current_frame_index)
        self._update_info_label()

    def prev_frame(self):
        if not (self._has_video or self._loop_frame is not None):
            return
        self.pause()
        self.current_frame_index = max(self.current_frame_index - 1, 0)
        self._seek_backend_to_current_frame()
        self.frame_changed.emit(self.current_frame_index)
        self._update_info_label()

    def seek_to_frame(self, index: int):
        if not (self._has_video or self._loop_frame is not None):
            return
        self.pause()
        self.current_frame_index = max(0, min(index, max(0, self.total_frames - 1)))
        self._seek_backend_to_current_frame()
        self.frame_changed.emit(self.current_frame_index)
        self._update_info_label()

    def _seek_backend_to_current_frame(self):
        """Seek to ``current_frame_index`` — one frame request.

        VapourSynth is frame-indexed, so a seek needs no fps→seconds→re-quantize
        round trip and carries no ±1 drift on VFR sources, which is what mpv's
        ``round(time_pos * fps)`` could produce. This is why preview indices and
        the export's ``clip[start:end]`` now agree exactly.
        """
        if self._vs_active:
            self._request_current_frame(coalesce=True)

    def _capture_frame_vs(self, callback) -> bool:
        """Deliver the current frame from the VS graph. No PNG round trip.

        The mpv path had to ask the player to write a temp PNG, decode it, undo
        the rotation mpv had baked in, and retry while mpv refused the request
        during loading. With VapourSynth the frame is already in memory and
        ``current_frame`` is kept in source orientation, so this is a plain
        hand-off.
        """
        if not self._vs_active:
            return False
        if self.current_frame is not None:
            callback(self.current_frame)
            return True
        requester = self._frame_requester
        if requester is None:
            return False
        epoch = self._load_epoch
        index = self.current_frame_index

        def _once(got_epoch: int, got_index: int, array) -> None:
            if got_epoch != epoch:
                return
            try:
                requester.frame_ready.disconnect(_once)
            except Exception:
                pass
            callback(array)

        requester.frame_ready.connect(_once)
        if not requester.request(index):
            try:
                requester.frame_ready.disconnect(_once)
            except Exception:
                pass
            callback(self.current_frame)
        return True

    def capture_frame_async(self, callback):
        """Deliver the current frame (source orientation) to ``callback``.

        VapourSynth hands the frame over directly; static images / image loops
        already hold it. The mpv path had to pause, round-trip a temp PNG through
        the player, undo the rotation it had baked in, and retry while mpv
        refused the request during loading — that whole detour (and the black
        frames it produced when it failed) is gone.
        """
        if self._vs_active:
            self.pause()
            if self._capture_frame_vs(callback):
                return
        callback(self.current_frame)

    def get_current_frame(self) -> int:
        return self.current_frame_index

    def get_cropbox(self) -> Tuple[int, int, int, int]:
        return tuple(self.cropbox)

    def get_cropbox_in_rotated_space(self) -> Tuple[int, int, int, int]:
        return tuple(self.cropbox)

    def set_cropbox(self, x: int, y: int, w: int, h: int):
        self.cropbox = [x, y, w, h]
        self._bound_cropbox()
        self._emit_cropbox_changed()
        self._refresh_display()

    def get_video_info(self) -> Tuple[float, int, int, int]:
        return self.video_fps, self.total_frames, self.video_width, self.video_height

    def set_preview_mode(self, enabled: bool):
        if self._preview_mode == bool(enabled):
            return
        self._preview_mode = bool(enabled)
        if self._vs_active:
            # Swap between the source graph and the real export graph; the frame
            # itself changes, so rebuild rather than re-render the cached one.
            self._rebuild_vs_graph()
        else:
            self._refresh_display()

    def is_preview_mode(self) -> bool:
        return self._preview_mode

    def set_rotation(self, degrees: int):
        # Snap to a cardinal angle: the VapourSynth export graph only
        # support 0/90/180/270, so keep preview and export in lockstep and never let an
        # arbitrary angle through the UI (timeline SpinBox also steps by 90).
        degrees = (round(int(degrees) / 90) * 90) % 360
        if self._rotation == degrees:
            return
        has_video = self.video_width > 0 and self.video_height > 0
        # Remap the crop box through the rotation change instead of resetting it
        # to a default centred rectangle (which silently discarded the user's
        # crop on every rotate). Map current rotated-space box -> original
        # coords (using the OLD angle) -> new rotated-space (using the NEW angle).
        original_box = None
        if has_video:
            original_box = self._cropbox_to_original_coords(*self.cropbox)
        self._rotation = degrees
        if self._vs_active:
            # Rotation is part of the graph, so re-derive it (and refresh the
            # displayed frame) instead of asking a player to rotate for us.
            self._rebuild_vs_graph()
        if has_video:
            if original_box is not None:
                self.cropbox = list(self._original_to_rotated_coords(*original_box))
                self._bound_cropbox()
                self._emit_cropbox_changed()
            else:
                self._init_cropbox()
        self.rotation_changed.emit(degrees)
        self._refresh_display()

    def get_rotation(self) -> int:
        return self._rotation

    def set_epconfig(self, config: "EPConfig"):
        self._epconfig = config

    def _get_rotated_video_size(self) -> Tuple[int, int]:
        if self._rotation in (90, 270):
            return self.video_height, self.video_width
        if self._rotation in (0, 180):
            return self.video_width, self.video_height
        import math

        rad = math.radians(self._rotation)
        cos_a = abs(math.cos(rad))
        sin_a = abs(math.sin(rad))
        return (
            int(self.video_width * cos_a + self.video_height * sin_a),
            int(self.video_width * sin_a + self.video_height * cos_a),
        )

    def _apply_rotation(self, frame: np.ndarray) -> np.ndarray:
        return self.apply_rotation_to_frame(frame, self._rotation)

    @staticmethod
    def apply_rotation_to_frame(frame: np.ndarray, rotation: int) -> np.ndarray:
        if rotation == 0:
            return frame
        if not HAS_CV2:
            return frame
        rotation = rotation % 360
        if rotation == 90:
            return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        if rotation == 180:
            return cv2.rotate(frame, cv2.ROTATE_180)
        if rotation == 270:
            return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        h, w = frame.shape[:2]
        matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), -rotation, 1.0)
        cos_a = abs(matrix[0, 0])
        sin_a = abs(matrix[0, 1])
        new_w = int(w * cos_a + h * sin_a)
        new_h = int(w * sin_a + h * cos_a)
        matrix[0, 2] += (new_w - w) / 2.0
        matrix[1, 2] += (new_h - h) / 2.0
        return cv2.warpAffine(
            frame,
            matrix,
            (new_w, new_h),
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0),
        )

    def _cropbox_to_original_coords(
        self, x: int, y: int, w: int, h: int
    ) -> Tuple[int, int, int, int]:
        """Map a crop box from the CURRENT rotated display space to source coords."""
        if self._rotation == 90:
            return y, self.video_height - x - w, h, w
        if self._rotation == 180:
            return self.video_width - x - w, self.video_height - y - h, w, h
        if self._rotation == 270:
            return self.video_width - y - h, x, h, w
        return x, y, w, h

    def _original_to_rotated_coords(
        self, x: int, y: int, w: int, h: int
    ) -> Tuple[int, int, int, int]:
        """Inverse of _cropbox_to_original_coords for the CURRENT rotation."""
        if self._rotation == 90:
            return self.video_height - h - y, x, h, w
        if self._rotation == 180:
            return self.video_width - x - w, self.video_height - y - h, w, h
        if self._rotation == 270:
            return y, self.video_width - x - w, h, w
        return x, y, w, h

    def _display_to_rotated_coords(self, widget: QWidget, pos: QPoint) -> Tuple[int, int]:
        rotated_w, rotated_h = self._get_rotated_video_size()
        self._update_display_geometry(widget, rotated_w, rotated_h)
        x = int((pos.x() - self.display_offset_x) / max(self.display_scale, 1e-6))
        y = int((pos.y() - self.display_offset_y) / max(self.display_scale, 1e-6))
        return max(0, x), max(0, y)

    def _get_drag_mode(self, vx: int, vy: int) -> int:
        x, y, w, h = self.cropbox
        hs = self.handle_size
        if abs(vx - x) < hs and abs(vy - y) < hs:
            return self.DRAG_RESIZE_TL
        if abs(vx - (x + w)) < hs and abs(vy - y) < hs:
            return self.DRAG_RESIZE_TR
        if abs(vx - x) < hs and abs(vy - (y + h)) < hs:
            return self.DRAG_RESIZE_BL
        if abs(vx - (x + w)) < hs and abs(vy - (y + h)) < hs:
            return self.DRAG_RESIZE_BR
        if x <= vx <= x + w and y <= vy <= y + h:
            return self.DRAG_MOVE
        return self.DRAG_NONE

    def _handle_mouse_press(self, widget: QWidget, event: QMouseEvent):
        if (event.button() != Qt.MouseButton.LeftButton or self._preview_mode
                or self._zoom_factor > 1.0):
            # 预览模式下画面是导出结果(已裁剪/缩放/补边),裁剪框不绘制,
            # 此时的拖拽会按导出几何去改框——无反馈且坐标系不对。
            # 放大后画面是源的一个视口窗口,display_scale 不再对应源坐标,
            # 同理拖拽会错位——放大是查看模式,裁剪框锁定(见 _paint_cropbox)。
            return
        vx, vy = self._display_to_rotated_coords(widget, event.pos())
        self.drag_mode = self._get_drag_mode(vx, vy)
        if self.drag_mode != self.DRAG_NONE:
            self.drag_start_pos = event.pos()
            self.drag_start_cropbox = self.cropbox.copy()
            self.setFocus()

    def _handle_mouse_move(self, widget: QWidget, event: QMouseEvent):
        if self._preview_mode:
            widget.setCursor(Qt.CursorShape.ArrowCursor)
            return
        if self.drag_mode == self.DRAG_NONE or self.drag_start_pos is None:
            vx, vy = self._display_to_rotated_coords(widget, event.pos())
            mode = self._get_drag_mode(vx, vy)
            cursors = {
                self.DRAG_RESIZE_TL: Qt.CursorShape.SizeFDiagCursor,
                self.DRAG_RESIZE_BR: Qt.CursorShape.SizeFDiagCursor,
                self.DRAG_RESIZE_TR: Qt.CursorShape.SizeBDiagCursor,
                self.DRAG_RESIZE_BL: Qt.CursorShape.SizeBDiagCursor,
                self.DRAG_MOVE: Qt.CursorShape.SizeAllCursor,
            }
            widget.setCursor(cursors.get(mode, Qt.CursorShape.ArrowCursor))
            return

        crx, cry = self._display_to_rotated_coords(widget, event.pos())
        srx, sry = self._display_to_rotated_coords(widget, self.drag_start_pos)
        dx, dy = crx - srx, cry - sry
        sx, sy, sw, sh = self.drag_start_cropbox
        if self.drag_mode == self.DRAG_MOVE:
            self.cropbox = [sx + dx, sy + dy, sw, sh]
        elif self.drag_mode == self.DRAG_RESIZE_BR:
            new_w = max(1, sw + dx)
            self.cropbox = [sx, sy, new_w, int(new_w / self.target_aspect_ratio)]
        elif self.drag_mode == self.DRAG_RESIZE_TL:
            new_w = max(1, sw - dx)
            new_h = int(new_w / self.target_aspect_ratio)
            self.cropbox = [sx + sw - new_w, sy + sh - new_h, new_w, new_h]
        elif self.drag_mode == self.DRAG_RESIZE_TR:
            new_w = max(1, sw + dx)
            new_h = int(new_w / self.target_aspect_ratio)
            self.cropbox = [sx, sy + sh - new_h, new_w, new_h]
        elif self.drag_mode == self.DRAG_RESIZE_BL:
            new_w = max(1, sw - dx)
            new_h = int(new_w / self.target_aspect_ratio)
            self.cropbox = [sx + sw - new_w, sy, new_w, new_h]
        self._bound_cropbox()
        self._emit_cropbox_changed()
        self._refresh_display()

    def _handle_mouse_release(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_mode = self.DRAG_NONE
            self.drag_start_pos = None

    def keyPressEvent(self, event: QKeyEvent):
        if not (self._has_video or self.current_frame is not None):
            super().keyPressEvent(event)
            return
        has_modifier = event.modifiers() != Qt.KeyboardModifier.NoModifier
        key = event.key()
        if key == Qt.Key.Key_Space and not has_modifier and self._has_video:
            self.toggle_play()
        elif key == Qt.Key.Key_Left and not has_modifier and self._has_video:
            self.prev_frame()
        elif key == Qt.Key.Key_Right and not has_modifier and self._has_video:
            self.next_frame()
        elif key == Qt.Key.Key_W and not has_modifier:
            self.cropbox[1] -= 10
        elif key == Qt.Key.Key_S and not has_modifier:
            self.cropbox[1] += 10
        elif key == Qt.Key.Key_A and not has_modifier:
            self.cropbox[0] -= 10
        elif key == Qt.Key.Key_D and not has_modifier:
            self.cropbox[0] += 10
        elif key == Qt.Key.Key_Equal and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            # Ctrl+= (zoom in)
            self.zoom_slider.setValue(self.zoom_slider.value() + 10)
        elif key == Qt.Key.Key_Minus and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            # Ctrl+- (zoom out)
            self.zoom_slider.setValue(self.zoom_slider.value() - 10)
        elif key == Qt.Key.Key_0 and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            # Ctrl+0 (reset zoom to 100%)
            self.zoom_slider.setValue(0)
        else:
            super().keyPressEvent(event)
            return
        self._bound_cropbox()
        self._emit_cropbox_changed()
        self._refresh_display()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_display()

    def closeEvent(self, event):
        # Window teardown: the event loop is going away, so use the blocking
        # (bounded) shutdown — async kill-escalation timers would never fire.
        self.clear(sync_shutdown=True)
        super().closeEvent(event)

    def _on_zoom_slider_changed(self, value: int):
        """Slider position → zoom factor (logarithmic 1% ~ 10000%)."""
        import math
        # value 0 → 1.0x, value 100 → 10.0x, value 200 → 100.0x
        factor = 10 ** (value / 100.0)
        if abs(factor - self._zoom_factor) < 0.001:
            return
        self._zoom_factor = factor
        self.zoom_label.setText(f"{int(factor * 100)}%")
        self._rebuild_vs_graph()

    def _set_zoom_percent(self, percent: int):
        """Quick-zoom button: set zoom to an exact percentage."""
        import math
        factor = percent / 100.0
        # Convert back to slider position
        slider_val = int(100.0 * math.log10(factor))
        self.zoom_slider.setValue(slider_val)

    def set_zoom_factor(self, factor: float):
        """Public API: set zoom programmatically (factor = 1.0 means 100%)."""
        import math
        if factor < 0.01 or factor > 100.0:
            raise ValueError(f"zoom factor {factor} out of range [0.01, 100.0]")
        slider_val = int(100.0 * math.log10(factor))
        self.zoom_slider.setValue(slider_val)

    def get_zoom_factor(self) -> float:
        """Public API: current zoom factor."""
        return self._zoom_factor

    def clear(self, sync_shutdown: bool = False):
        self._load_epoch += 1  # invalidate pending probe/retry continuations
        self.pause()
        self._teardown_media(sync_shutdown=sync_shutdown)
        self._loop_frame = None
        self.video_path = ""
        self.total_frames = 0
        self.current_frame_index = 0
        self.video_width = 0
        self.video_height = 0
        self.current_frame = None
        self._display_stack.setCurrentIndex(0)
        self.video_label.clear()
        self.video_label.setText("No media loaded")
        self.cropbox = [0, 0, 0, 0]
        self._update_info_label()
