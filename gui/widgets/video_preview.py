"""Video preview widget backed by mpv playback and metadata."""

from __future__ import annotations

import logging
import os
import json
import sys
import tempfile
import uuid
from typing import Optional, Tuple, TYPE_CHECKING

import numpy as np

try:
    import cv2

    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from PyQt6.QtCore import QCoreApplication, QPoint, QProcess, QSize, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import (
    QGuiApplication,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
)
from PyQt6.QtNetwork import QLocalSocket
from PyQt6.QtWidgets import QLabel, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, setCustomStyleSheet

from core.media_tools import MediaToolchain
from core.video_processor import MetadataProbeWorker

if TYPE_CHECKING:
    from config.epconfig import EPConfig

# mpv JSON-IPC connection retry budget. The connect is driven asynchronously on
# the GUI thread via QTimer, so these never block the UI.
_MPV_IPC_MAX_ATTEMPTS = 100
_MPV_IPC_RETRY_MS = 100

# QProcess instances detached during async teardown: they must outlive the
# widget until their `finished` signal releases them, otherwise C++-side
# destruction of a still-running QProcess kills the app.
_DYING_MPV_PROCESSES: list = []

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


class _MpvSurface(QWidget):
    """Bare native host window for mpv's --wid embedding.

    mpv creates its own child window filling this HWND (mpv manual, --wid):
    that child receives the paint and mouse traffic, so QPainter overlays
    drawn here are occluded and Qt mouse handlers never fire. Crop editing
    therefore happens on a frozen frame on the QLabel page (crop mode)
    instead of over the live video.
    """

    def __init__(self, owner: "VideoPreviewWidget"):
        super().__init__(owner)
        self._owner = owner
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)


class VideoPreviewWidget(QWidget):
    """Preview media, expose crop/trim state, and keep the legacy public API."""

    cropbox_changed = pyqtSignal(int, int, int, int)
    frame_changed = pyqtSignal(int)
    playback_state_changed = pyqtSignal(bool)
    video_loaded = pyqtSignal(int, float)
    load_failed = pyqtSignal(str)  # async metadata probe / mpv launch failure
    rotation_changed = pyqtSignal(int)
    crop_mode_changed = pyqtSignal(bool)  # still-frame crop mode entered/left

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

        self._reader_thread = None
        self._mpv_process: Optional[QProcess] = None
        self._mpv_socket: Optional[QLocalSocket] = None
        self._mpv_ipc_server = ""
        self._mpv_ipc_attempts = 0  # async IPC-connect retry counter
        self._mpv_ipc_connected = False  # True once the IPC socket has connected
        self._pending_mpv_cmds: list = []  # (command, on_reply) queued before connect
        self._mpv_read_buf = b""  # partial JSON-IPC line buffer
        self._mpv_request_id = 0  # monotonically increasing JSON-IPC request_id
        self._mpv_reply_callbacks: dict = {}  # request_id -> on_reply callable
        self._screenshot_refresh_timer: Optional[QTimer] = None
        # Load-generation token: bumped by every new load/clear. Deferred
        # continuations (probe results, IPC connect retries, screenshot
        # retries) capture it at schedule time and no-op when stale, so a
        # slow continuation from load A can never corrupt load B's session.
        self._load_epoch = 0
        self._probe_worker: Optional[MetadataProbeWorker] = None
        # IPC connect retry runs on a child QTimer + bound-method slot, so the
        # timer dies with the widget and can never fire into a deleted object.
        self._ipc_retry_timer: Optional[QTimer] = None
        self._ipc_retry_epoch = 0
        self._crop_mode = False  # editing the crop on a frozen frame (page 0)
        self._media_toolchain = MediaToolchain.discover()
        self._has_video = False
        self._loop_frame: Optional[np.ndarray] = None
        self._preview_mode = False
        self._epconfig: Optional["EPConfig"] = None
        self._overlay_renderer = None
        self._use_gl = False
        self._gl_renderer = None
        self._rotation = 0

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

        self._mpv_widget = _MpvSurface(self)
        self._mpv_widget.setMouseTracking(True)
        self._mpv_widget.setStyleSheet("background-color: #000; border: none;")
        self._mpv_page_index = self._display_stack.addWidget(self._mpv_widget)

        layout.addWidget(self._display_stack)
        self.info_label = CaptionLabel("Frame 0/0 | Crop: (0, 0, 0, 0)")
        setCustomStyleSheet(
            self.info_label,
            "color: #999; padding: 4px 10px; background-color: transparent; border: none;",
            "color: #777; padding: 4px 10px; background-color: transparent; border: none;",
        )
        layout.addWidget(self.info_label)

    def _stop_reader_thread(self, sync_shutdown: bool = False):
        self._stop_mpv_process(sync=sync_shutdown)
        if self._reader_thread is not None:
            try:
                self._reader_thread.request_stop()
                self._reader_thread.wait(1000)
            except Exception:
                pass
            self._reader_thread = None
        self._has_video = False

    def _stop_mpv_process(self, sync: bool = False):
        """Tear down the mpv session.

        Reload/clear paths run ASYNCHRONOUSLY: `quit` is sent, the process is
        detached, and kill-escalation runs on timers — the GUI thread never
        calls the blocking `QProcess.waitForFinished` (QtCore.pyi:6985), which
        used to freeze the UI up to ~6.1s on every video switch. ``sync=True``
        is reserved for window-close/app-quit, where the event loop is about
        to stop and timers would never fire.
        """
        socket = self._mpv_socket
        process = self._mpv_process
        # Best-effort graceful quit while the socket is still connected.
        if socket is not None and socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
            try:
                self._send_mpv_command(["quit"])
            except Exception:
                pass
        self._mpv_socket = None
        self._mpv_process = None
        # Pending replies can never arrive once the socket is gone.
        self._mpv_reply_callbacks = {}
        if self._screenshot_refresh_timer is not None:
            self._screenshot_refresh_timer.stop()
        if self._ipc_retry_timer is not None:
            self._ipc_retry_timer.stop()
        try:
            if socket is not None:
                socket.disconnectFromServer()
                socket.abort()
                socket.deleteLater()
        except Exception as exc:
            logger.debug("mpv socket shutdown failed: %s", exc)
        if process is None:
            return
        try:
            if process.state() == QProcess.ProcessState.NotRunning:
                process.deleteLater()
                return
            if sync:
                if process.state() != QProcess.ProcessState.NotRunning:
                    process.waitForFinished(3000)
                if process.state() != QProcess.ProcessState.NotRunning:
                    process.kill()
                    process.waitForFinished(3000)
                process.deleteLater()
                return
        except Exception as exc:
            logger.debug("mpv shutdown failed: %s", exc)
            return
        # Async detach: `quit` was already sent; release on `finished`, and
        # escalate to kill if mpv lingers. No GUI-thread blocking involved.
        # Un-parent the process first — a dying process must outlive this
        # widget, and cascade-deleting a running QProcess with the widget
        # would fire Qt warnings / kill semantics at an uncontrolled time.
        process.setParent(None)
        _DYING_MPV_PROCESSES.append(process)

        def _release(*_args):
            try:
                _DYING_MPV_PROCESSES.remove(process)
            except ValueError:
                return  # already released
            process.deleteLater()

        process.finished.connect(_release)

        def _kill_if_running():
            try:
                if process in _DYING_MPV_PROCESSES and \
                        process.state() != QProcess.ProcessState.NotRunning:
                    process.kill()
            except RuntimeError:
                pass  # C++ object already gone

        QTimer.singleShot(1500, _kill_if_running)
        QTimer.singleShot(4000, _kill_if_running)

    def _send_mpv_command(self, command: list, on_reply=None):
        if self._mpv_socket is None or \
                self._mpv_socket.state() != QLocalSocket.LocalSocketState.ConnectedState:
            # Not connected yet: queue so an early seek/pause/rotate issued during the
            # async connect window isn't silently lost. Bound the queue so a mpv that
            # never connects can't grow it without limit.
            if self._mpv_process is not None:
                self._pending_mpv_cmds.append((command, on_reply))
                del self._pending_mpv_cmds[:-32]
            return
        # mpv echoes request_id in the reply together with an "error" field, so
        # replies can be correlated to callers and failures logged instead of
        # being silently dropped.
        self._mpv_request_id += 1
        request_id = self._mpv_request_id
        if on_reply is not None:
            self._mpv_reply_callbacks[request_id] = on_reply
        payload = json.dumps(
            {"command": command, "request_id": request_id}, separators=(",", ":")
        )
        self._mpv_socket.write((payload + "\n").encode("utf-8"))
        self._mpv_socket.waitForBytesWritten(100)

    def _make_mpv_ipc_server(self) -> str:
        name = f"neo_assetmaker_mpv_{os.getpid()}_{uuid.uuid4().hex}"
        if sys.platform == "win32":
            return name
        return os.path.join(tempfile.gettempdir(), name)

    def _start_mpv_preview(self, path: str) -> bool:
        """启动 mpv 预览（异步方式，不阻塞 UI）"""
        self._stop_mpv_process()

        # 准备 IPC 服务器名称
        self._mpv_ipc_server = self._make_mpv_ipc_server()

        # 切换到 mpv 页面
        self._display_stack.setCurrentIndex(self._mpv_page_index)
        QCoreApplication.processEvents()

        # 构建 mpv 参数
        platform_name = (QGuiApplication.platformName() or "").lower()
        is_headless = platform_name == "offscreen"
        args = [
            "--no-config",
            "--keep-open=yes",
            "--pause=yes",
            f"--input-ipc-server={self._mpv_ipc_server}",
            "--osc=no",
            # Software screenshot rendering: works VO-independently, so
            # screenshot-to-file succeeds both under --wid embedding and the
            # headless --vo=null branch (mpv manual, --screenshot-sw).
            "--screenshot-sw=yes",
        ]
        if is_headless:
            args.extend(["--force-window=no", "--ao=null", "--vo=null"])
        else:
            mpv_window_id = int(self._mpv_widget.winId())
            args.extend(["--force-window=yes", f"--wid={mpv_window_id}"])
        args.append(path)

        # 在 GUI 线程创建 QProcess（正确的线程亲和），用信号异步驱动启动。
        # 不再放到工作线程里：QProcess/QLocalSocket 只能在其所属线程使用，且
        # 其事件驱动 I/O 与 deleteLater 依赖所属线程的事件循环。
        self._mpv_process = QProcess(self)
        self._mpv_process.errorOccurred.connect(self._on_mpv_process_error)
        self._mpv_process.started.connect(self._on_mpv_process_started)
        self._mpv_process.start(self._media_toolchain.mpv_path, args)

        # 立即返回 True，实际结果通过 started / IPC 连接信号异步通知
        return True

    def _on_mpv_process_started(self):
        """mpv 进程已启动 → 开始异步连接其 JSON IPC 服务器。"""
        logger.debug("mpv process started, connecting IPC...")
        self._mpv_ipc_attempts = 0
        self._mpv_ipc_connected = False
        self._pending_mpv_cmds = []
        self._mpv_read_buf = b""
        self._mpv_reply_callbacks = {}
        self._try_mpv_ipc_connect()

    def _try_mpv_ipc_connect(self):
        """尝试连接 mpv 的 IPC 服务器；失败则用 QTimer 异步重试（不阻塞 UI）。"""
        if self._mpv_process is None:
            return  # 已被 _stop_mpv_process 取消
        self._mpv_ipc_attempts += 1
        socket = QLocalSocket(self)
        self._mpv_socket = socket
        socket.connected.connect(self._on_mpv_ipc_connected)
        socket.errorOccurred.connect(self._on_mpv_ipc_error)
        socket.connectToServer(self._mpv_ipc_server)

    def _on_mpv_ipc_connected(self):
        """IPC 连接成功：开始观察播放位置、补发排队命令、应用旋转。"""
        logger.info("mpv IPC connected after %d attempt(s)", self._mpv_ipc_attempts)
        self._mpv_ipc_connected = True
        # Read mpv's replies/events so we can track the real playback position.
        if self._mpv_socket is not None:
            self._mpv_socket.readyRead.connect(self._on_mpv_readable)
        self._mpv_read_buf = b""
        self._send_mpv_command(["observe_property", 1, "time-pos"])
        if self._rotation:
            self._send_mpv_command(["set_property", "video-rotate", self._rotation])
        # Flush commands that were queued before the socket connected (early seek/pause).
        pending, self._pending_mpv_cmds = self._pending_mpv_cmds, []
        for cmd, on_reply in pending:
            self._send_mpv_command(cmd, on_reply)

    def _on_mpv_readable(self):
        """Drain mpv JSON-IPC lines and dispatch them (Pc: time-pos observation)."""
        if self._mpv_socket is None:
            return
        self._mpv_read_buf += bytes(self._mpv_socket.readAll())
        while b"\n" in self._mpv_read_buf:
            raw, self._mpv_read_buf = self._mpv_read_buf.split(b"\n", 1)
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw.decode("utf-8", "replace"))
            except (ValueError, TypeError):
                continue
            self._handle_mpv_message(msg)

    def _handle_mpv_message(self, msg: dict):
        """Handle one parsed mpv IPC message; drive the frame counter from time-pos."""
        if not isinstance(msg, dict):
            return
        request_id = msg.get("request_id")
        if request_id is not None:
            callback = self._mpv_reply_callbacks.pop(request_id, None)
            error = msg.get("error")
            if error not in (None, "success"):
                logger.warning(
                    "mpv command failed (request_id=%s): %s", request_id, error
                )
            if callback is not None:
                try:
                    callback(msg)
                except Exception:
                    logger.exception("mpv reply callback raised")
            return
        if msg.get("event") == "property-change" and msg.get("name") == "time-pos":
            data = msg.get("data")
            if isinstance(data, (int, float)) and self.video_fps > 0:
                idx = int(round(data * self.video_fps))
                idx = max(0, min(idx, max(0, self.total_frames - 1)))
                if idx != self.current_frame_index:
                    self.current_frame_index = idx
                    self.frame_changed.emit(idx)
                    self._update_info_label()

    def _on_mpv_ipc_error(self, _err):
        """IPC socket 出错：区分"从未连上(重试)"与"连上后断开(mpv 正常退出，勿重试)"。"""
        socket = self._mpv_socket
        if socket is not None:
            socket.abort()
            socket.deleteLater()
            self._mpv_socket = None
        # A post-connect error means mpv closed the pipe (e.g. a normal quit), not a
        # failed initial connect — do NOT re-enter the retry loop or show a spurious
        # "mpv IPC connection failed".
        if self._mpv_ipc_connected:
            logger.debug("mpv IPC closed after a successful connection")
            return
        if self._mpv_process is None:
            return  # 已停止
        if self._mpv_ipc_attempts >= _MPV_IPC_MAX_ATTEMPTS:
            self._on_mpv_launch_failed("mpv IPC connection failed after multiple attempts")
            return
        # Capture the load epoch: if the user loads another video during the
        # retry window, the stale timer must not connect to (and hijack) the
        # NEW session's socket — `_mpv_process is not None` alone passes
        # wrongly in that case because the new load already created a process.
        self._ipc_retry_epoch = self._load_epoch
        if self._ipc_retry_timer is None:
            self._ipc_retry_timer = QTimer(self)
            self._ipc_retry_timer.setSingleShot(True)
            self._ipc_retry_timer.timeout.connect(self._on_ipc_retry_due)
        self._ipc_retry_timer.start(_MPV_IPC_RETRY_MS)

    def _on_ipc_retry_due(self):
        if self._ipc_retry_epoch != self._load_epoch:
            return  # a newer load owns the mpv session now
        self._try_mpv_ipc_connect()

    def _on_mpv_process_error(self, error):
        """QProcess 层错误。只有启动失败才当作 launch 失败，避免停止时的误报。"""
        if self._mpv_process is None:
            return  # 停止过程中，忽略
        if error == QProcess.ProcessError.FailedToStart:
            self._on_mpv_launch_failed(f"mpv failed to start: {self._mpv_process.errorString()}")

    def _on_mpv_launch_failed(self, error_msg: str):
        """mpv 启动/连接失败：清理并显示错误。"""
        logger.error("mpv launch failed: %s", error_msg)
        self._stop_mpv_process()
        self._display_stack.setCurrentIndex(0)
        self.video_label.setText(f"mpv launch failed: {error_msg}")

        # 标记加载失败并对外可见(此时 video_loaded 可能已发出)
        self._has_video = False
        self.load_failed.emit(error_msg)

    def set_target_resolution(self, width: int, height: int):
        if self.target_width == width and self.target_height == height:
            return
        self.target_width = width
        self.target_height = height
        self.target_aspect_ratio = width / height
        if self.video_width > 0 and self.video_height > 0:
            self._init_cropbox()
            self._refresh_display()

    def load_video(self, path: str) -> bool:
        """Begin loading a video (asynchronous).

        Returns True when the load was *accepted*; the metadata probe then runs
        on a worker thread and the outcome arrives via ``video_loaded`` /
        ``load_failed``. Returns False only for synchronous rejections (file or
        mpv missing). The probe's blocking ``waitFor*`` chain used to run right
        here on the GUI thread, freezing the UI for up to tens of seconds per
        load (QtNetwork.pyi:202-205, QtCore.pyi:6985-6988 — blocking calls).
        """
        logger.info("Loading video with mpv: %s", path)
        if not os.path.exists(path):
            self.video_label.setText(f"File not found: {path}")
            return False

        self._media_toolchain = MediaToolchain.discover()
        if not self._media_toolchain.mpv_path:
            self.video_label.setText("mpv not found")
            return False

        self._load_epoch += 1
        epoch = self._load_epoch
        self._reset_crop_mode()
        self._stop_reader_thread()
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
        worker = MetadataProbeWorker(self._media_toolchain.mpv_path, path)
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
        self.current_frame = np.zeros(
            (self.video_height, self.video_width, 3), dtype=np.uint8
        )
        self._has_video = True
        self._init_cropbox()
        self._update_info_label()
        self._start_mpv_preview(path)
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
        self._reset_crop_mode()
        self.pause()
        self._stop_reader_thread()
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

    def _init_cropbox(self):
        rotated_w, rotated_h = self._get_rotated_video_size()
        if rotated_w <= 0 or rotated_h <= 0:
            self.cropbox = [0, 0, self.target_width, self.target_height]
            return
        if rotated_w / rotated_h > self.target_aspect_ratio:
            max_h = rotated_h
            max_w = int(max_h * self.target_aspect_ratio)
        else:
            max_w = rotated_w
            max_h = int(max_w / self.target_aspect_ratio)
        crop_w = max(1, int(max_w * 0.75))
        crop_h = max(1, int(crop_w / self.target_aspect_ratio))
        self.cropbox = [
            max(0, (rotated_w - crop_w) // 2),
            max(0, (rotated_h - crop_h) // 2),
            crop_w,
            crop_h,
        ]
        self._emit_cropbox_changed()

    def _bound_cropbox(self):
        rotated_w, rotated_h = self._get_rotated_video_size()
        if rotated_w <= 0 or rotated_h <= 0:
            return
        x, y, w, h = [int(v) for v in self.cropbox]
        w = max(1, min(w, rotated_w))
        h = max(1, min(h, rotated_h))
        x = max(0, min(x, max(0, rotated_w - w)))
        y = max(0, min(y, max(0, rotated_h - h)))
        self.cropbox = [x, y, w, h]

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
        if self.current_frame is not None and self._display_stack.currentIndex() == 0:
            self._display_frame(self.current_frame)
        else:
            self._update_display_geometry(
                self._mpv_widget,
                *self._get_rotated_video_size(),
            )
            self._mpv_widget.update()
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
        # When mpv is playing, the frame index is driven by observed time-pos
        # (_handle_mpv_message), so don't also free-run the counter here (drift).
        if self._mpv_process is not None:
            return
        self.current_frame_index += 1
        if self.current_frame_index >= max(1, self.total_frames):
            self.current_frame_index = 0
        self.frame_changed.emit(self.current_frame_index)
        self._update_info_label()

    def play(self):
        if self._crop_mode:
            self.exit_crop_mode()  # back to the live video before playback
        if self.is_playing or not (self._has_video or self._loop_frame is not None):
            return
        if self._mpv_process is not None:
            self._send_mpv_command(["set_property", "pause", False])
        interval = max(1, round(1000 / max(self.video_fps, 1.0)))
        self.timer.start(interval)
        self.is_playing = True
        self.playback_state_changed.emit(True)

    def pause(self):
        self.timer.stop()
        if self._mpv_process is not None:
            self._send_mpv_command(["set_property", "pause", True])
            # Refresh current_frame shortly after pausing so capture/crop tools
            # see the real frame instead of the load-time placeholder.
            self._schedule_screenshot_refresh()
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
        if self._crop_mode:
            self.exit_crop_mode()
        if not (self._has_video or self._loop_frame is not None):
            return
        self.pause()
        self.current_frame_index = min(
            self.current_frame_index + 1, max(0, self.total_frames - 1)
        )
        self._seek_mpv_to_current_frame()
        self.frame_changed.emit(self.current_frame_index)
        self._update_info_label()

    def prev_frame(self):
        if self._crop_mode:
            self.exit_crop_mode()
        if not (self._has_video or self._loop_frame is not None):
            return
        self.pause()
        self.current_frame_index = max(self.current_frame_index - 1, 0)
        self._seek_mpv_to_current_frame()
        self.frame_changed.emit(self.current_frame_index)
        self._update_info_label()

    def seek_to_frame(self, index: int):
        if self._crop_mode:
            self.exit_crop_mode()
        if not (self._has_video or self._loop_frame is not None):
            return
        self.pause()
        self.current_frame_index = max(0, min(index, max(0, self.total_frames - 1)))
        self._seek_mpv_to_current_frame()
        self.frame_changed.emit(self.current_frame_index)
        self._update_info_label()

    def _seek_mpv_to_current_frame(self):
        if self._mpv_process is None or self.video_fps <= 0:
            return
        seconds = self.current_frame_index / self.video_fps
        self._send_mpv_command(["seek", seconds, "absolute+exact"])
        self._schedule_screenshot_refresh()

    def request_screenshot(
        self, callback=None, _attempts_left: int = 5, _epoch: Optional[int] = None
    ) -> bool:
        """Read the current mpv frame back into ``current_frame`` via a temp PNG.

        mpv renders into its own child window, so the widget never sees pixels
        unless it asks mpv to write them out (`screenshot-to-file <file> video`
        = the video frame without OSD/subtitles). ``callback(frame | None)``
        fires once the reply arrives. Returns False when no mpv IPC session is
        available (callback still fires with None).
        """
        if _epoch is None:
            _epoch = self._load_epoch
        if (
            not HAS_CV2
            or self._mpv_process is None
            or not self._mpv_ipc_connected
            or _epoch != self._load_epoch
        ):
            if callback is not None:
                callback(None)
            return False
        shot_path = os.path.join(
            tempfile.gettempdir(), f"neo_mpv_shot_{uuid.uuid4().hex}.png"
        )

        def _on_reply(msg: dict):
            frame = None
            try:
                if msg.get("error") == "success" and os.path.exists(shot_path):
                    data = np.fromfile(shot_path, dtype=np.uint8)
                    decoded = cv2.imdecode(data, cv2.IMREAD_COLOR)
                    if decoded is not None:
                        # mpv bakes video-rotate into the screenshot (verified
                        # against the bundled mpv v0.41: 240x360 becomes 360x240
                        # after video-rotate=90). current_frame must stay in
                        # SOURCE orientation like the static-image path, so
                        # undo the rotation here.
                        if self._rotation:
                            decoded = self.apply_rotation_to_frame(
                                decoded, (360 - self._rotation) % 360
                            )
                        frame = decoded
            finally:
                try:
                    if os.path.exists(shot_path):
                        os.remove(shot_path)
                except OSError:
                    pass
            if frame is None and _attempts_left > 0:
                # mpv rejects screenshot commands with "error running command"
                # until the file has finished loading (observed right after
                # IPC connect on the bundled mpv v0.41) — retry briefly
                # instead of failing the capture. The retry carries the load
                # epoch so it can never capture a *different* video's frame.
                def _retry():
                    try:
                        self.request_screenshot(callback, _attempts_left - 1, _epoch)
                    except RuntimeError:
                        pass  # widget C++ side destroyed while the timer was pending

                QTimer.singleShot(200, _retry)
                return
            if frame is not None:
                self.current_frame = frame
            if callback is not None:
                callback(frame)

        self._send_mpv_command(["screenshot-to-file", shot_path, "video"], _on_reply)
        return True

    def _schedule_screenshot_refresh(self, delay_ms: int = 150):
        """Debounced current_frame refresh after pause/seek (mpv sessions only)."""
        if self._mpv_process is None:
            return
        if self._screenshot_refresh_timer is None:
            self._screenshot_refresh_timer = QTimer(self)
            self._screenshot_refresh_timer.setSingleShot(True)
            self._screenshot_refresh_timer.timeout.connect(
                self._on_screenshot_refresh_due
            )
        self._screenshot_refresh_timer.start(delay_ms)

    def _on_screenshot_refresh_due(self):
        if self._mpv_process is None or not self._mpv_ipc_connected:
            return
        self.request_screenshot(None)

    def capture_frame_async(self, callback):
        """Deliver the current frame (source orientation) to ``callback``.

        mpv-backed video pauses and reads the frame back over IPC; static
        images / image loops already hold the frame in memory.
        """
        if self._mpv_process is not None and self._mpv_ipc_connected:
            self.pause()
            if self._screenshot_refresh_timer is not None:
                # The explicit capture below supersedes the debounced refresh.
                self._screenshot_refresh_timer.stop()
            self.request_screenshot(callback)
            return
        callback(self.current_frame)

    def is_crop_mode(self) -> bool:
        return self._crop_mode

    def enter_crop_mode(self) -> bool:
        """裁剪模式：冻结当前帧到 QLabel 页并在其上编辑裁剪框。

        mpv 经 ``--wid`` 嵌入时在本控件内创建自己的子窗口(mpv 手册,--wid)：
        QPainter 覆盖层被其遮挡、鼠标事件也进不了 Qt——活视频上的裁剪交互
        整体失效,因此裁剪在暂停后的截图帧上进行(复用静态图页已工作的
        绘制/拖拽路径)。返回 True 表示已进入或已受理(mpv 截图为异步)。
        """
        if self._crop_mode:
            return True
        if self._mpv_process is None or not self._mpv_ipc_connected:
            # 静态图 / 图片循环本就显示在 QLabel 页,裁剪路径已经可用。
            if self.current_frame is not None:
                self._crop_mode = True
                self.crop_mode_changed.emit(True)
                self._refresh_display()
                return True
            return False
        self.pause()
        epoch = self._load_epoch

        def _on_shot(frame):
            if frame is None or epoch != self._load_epoch or self._crop_mode:
                return
            self._crop_mode = True
            self._display_stack.setCurrentIndex(0)
            self._display_frame(frame)
            self.crop_mode_changed.emit(True)

        self.request_screenshot(_on_shot)
        return True

    def exit_crop_mode(self):
        if not self._crop_mode:
            return
        self._crop_mode = False
        if self._mpv_process is not None:
            self._display_stack.setCurrentIndex(self._mpv_page_index)
            self._refresh_display()
        self.crop_mode_changed.emit(False)

    def _reset_crop_mode(self):
        """New media/clear invalidates any frozen-frame crop session."""
        if self._crop_mode:
            self._crop_mode = False
            self.crop_mode_changed.emit(False)

    def get_current_frame(self) -> int:
        return self.current_frame_index

    def get_cropbox(self) -> Tuple[int, int, int, int]:
        return tuple(self.cropbox)

    def get_cropbox_in_rotated_space(self) -> Tuple[int, int, int, int]:
        return tuple(self.cropbox)

    def get_cropbox_for_export(self) -> Tuple[int, int, int, int]:
        return self._cropbox_to_original_coords(*self.cropbox)

    def set_cropbox(self, x: int, y: int, w: int, h: int):
        self.cropbox = [x, y, w, h]
        self._bound_cropbox()
        self._emit_cropbox_changed()
        self._refresh_display()

    def get_video_info(self) -> Tuple[float, int, int, int]:
        return self.video_fps, self.total_frames, self.video_width, self.video_height

    def set_preview_mode(self, enabled: bool):
        self._preview_mode = enabled
        self._refresh_display()

    def is_preview_mode(self) -> bool:
        return self._preview_mode

    def set_use_gl(self, enabled: bool):
        self._use_gl = False
        if enabled:
            logger.debug("OpenGL preview path is retired for mpv playback")

    def set_rotation(self, degrees: int):
        # Snap to a cardinal angle: mpv video-rotate and the VapourSynth export only
        # support 0/90/180/270, so keep preview and export in lockstep and never let an
        # arbitrary angle through the UI (timeline SpinBox also steps by 90).
        degrees = (round(int(degrees) / 90) * 90) % 360
        if self._rotation == degrees:
            return
        self._rotation = degrees
        if self._mpv_process is not None:
            self._send_mpv_command(["set_property", "video-rotate", degrees])
        if self.video_width > 0 and self.video_height > 0:
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
        if self._rotation == 0:
            return x, y, w, h
        if self._rotation == 90:
            return y, self.video_height - x - w, h, w
        if self._rotation == 180:
            return self.video_width - x - w, self.video_height - y - h, w, h
        if self._rotation == 270:
            return self.video_width - y - h, x, h, w
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
        if event.button() != Qt.MouseButton.LeftButton:
            return
        vx, vy = self._display_to_rotated_coords(widget, event.pos())
        self.drag_mode = self._get_drag_mode(vx, vy)
        if self.drag_mode != self.DRAG_NONE:
            self.drag_start_pos = event.pos()
            self.drag_start_cropbox = self.cropbox.copy()
            self.setFocus()

    def _handle_mouse_move(self, widget: QWidget, event: QMouseEvent):
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

    def clear(self, sync_shutdown: bool = False):
        self._load_epoch += 1  # invalidate pending probe/retry continuations
        self._reset_crop_mode()
        self.pause()
        self._stop_reader_thread(sync_shutdown=sync_shutdown)
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
