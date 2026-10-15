"""Widget for displaying the device HTTP MJPEG live view."""

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QLabel, QHBoxLayout, QVBoxLayout, QWidget

from qfluentwidgets import (
    CaptionLabel,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    setCustomStyleSheet,
)

from gui.styles import COLOR_ERROR, COLOR_SUCCESS, COLOR_TEXT_MUTED, COLOR_TEXT_SECONDARY
from gui.workers.device_stream_worker import DeviceStreamThread

logger = logging.getLogger(__name__)


class DeviceStreamWidget(QWidget):
    """Display and control the RNDIS HTTP MJPEG stream."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stream_thread: DeviceStreamThread | None = None
        self._is_streaming = False
        self._host = "192.168.137.2"

        self._init_ui()
        self._connect_signals()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        control_layout = QHBoxLayout()
        control_layout.setSpacing(8)

        self.btnStart = PrimaryPushButton("开始预览")
        self.btnStart.setIcon(FluentIcon.PLAY)
        self.btnStop = PushButton("停止")
        self.btnStop.setIcon(FluentIcon.CLOSE)
        self.btnStop.setEnabled(False)

        control_layout.addWidget(self.btnStart)
        control_layout.addWidget(self.btnStop)
        control_layout.addStretch()
        layout.addLayout(control_layout)

        self.displayLabel = QLabel()
        self.displayLabel.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.displayLabel.setMinimumSize(320, 240)
        self.displayLabel.setStyleSheet(
            "QLabel { background-color: #1a1a1a; border-radius: 4px; }"
        )
        self.displayLabel.setText("未连接")
        setCustomStyleSheet(
            self.displayLabel,
            f"QLabel {{ color: {COLOR_TEXT_SECONDARY[0]}; font-size: 14px; }}",
            f"QLabel {{ color: {COLOR_TEXT_SECONDARY[1]}; font-size: 14px; }}",
        )
        layout.addWidget(self.displayLabel, stretch=1)

        self.statusLabel = CaptionLabel("未连接")
        setCustomStyleSheet(
            self.statusLabel,
            f"CaptionLabel {{ color: {COLOR_TEXT_MUTED[0]}; }}",
            f"CaptionLabel {{ color: {COLOR_TEXT_MUTED[1]}; }}",
        )
        layout.addWidget(self.statusLabel)

    def _connect_signals(self):
        self.btnStart.clicked.connect(self._on_start_stream)
        self.btnStop.clicked.connect(self._on_stop_stream)

    def _on_start_stream(self):
        if self._is_streaming:
            return

        self._stream_thread = DeviceStreamThread(parent=self)
        self._stream_thread.setup(host=self._host)
        self._stream_thread.frame_ready.connect(self._on_frame_ready)
        self._stream_thread.stream_started.connect(self._on_stream_started)
        self._stream_thread.stream_stopped.connect(self._on_stream_stopped)
        self._stream_thread.stream_error.connect(self._on_stream_error)
        self._stream_thread.fps_updated.connect(self._on_fps_updated)

        self._stream_thread.start()
        self._is_streaming = True
        self.btnStart.setEnabled(False)
        self.btnStop.setEnabled(True)
        self.statusLabel.setText("连接中...")
        self.displayLabel.setText("连接中...")

    def _on_stop_stream(self):
        if self._is_streaming:
            self._stop_thread()

    def _stop_thread(self):
        if self._stream_thread is not None:
            self._stream_thread.stop()
            self._stream_thread.wait(3000)
            self._stream_thread.deleteLater()
            self._stream_thread = None

        self._is_streaming = False
        self.btnStart.setEnabled(True)
        self.btnStop.setEnabled(False)

    def _on_frame_ready(self, qimage: QImage):
        pixmap = QPixmap.fromImage(qimage)
        scaled = pixmap.scaled(
            self.displayLabel.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.displayLabel.setPixmap(scaled)

    def _on_stream_started(self):
        self.statusLabel.setText("已连接")
        setCustomStyleSheet(
            self.statusLabel,
            f"CaptionLabel {{ color: {COLOR_SUCCESS[0]}; }}",
            f"CaptionLabel {{ color: {COLOR_SUCCESS[1]}; }}",
        )

    def _on_stream_stopped(self):
        self._is_streaming = False
        self.btnStart.setEnabled(True)
        self.btnStop.setEnabled(False)
        self.statusLabel.setText("已断开")
        setCustomStyleSheet(
            self.statusLabel,
            f"CaptionLabel {{ color: {COLOR_TEXT_MUTED[0]}; }}",
            f"CaptionLabel {{ color: {COLOR_TEXT_MUTED[1]}; }}",
        )

    def _on_stream_error(self, msg: str):
        logger.warning("Stream error: %s", msg)
        self.statusLabel.setText(f"错误：{msg[:60]}")
        setCustomStyleSheet(
            self.statusLabel,
            f"CaptionLabel {{ color: {COLOR_ERROR[0]}; }}",
            f"CaptionLabel {{ color: {COLOR_ERROR[1]}; }}",
        )
        InfoBar.warning(
            "实时画面异常",
            msg,
            parent=self,
            position=InfoBarPosition.TOP,
            duration=4000,
        )

    def _on_fps_updated(self, fps: float):
        pixmap = self.displayLabel.pixmap()
        if pixmap and not pixmap.isNull():
            width, height = pixmap.width(), pixmap.height()
            self.statusLabel.setText(f"已连接 | FPS: {fps} | {width}x{height}")
        else:
            self.statusLabel.setText(f"已连接 | FPS: {fps}")

    def set_device_host(self, host: str):
        self._host = host

    def shutdown(self):
        self._stop_thread()
