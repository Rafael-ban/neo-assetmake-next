"""Qt worker for receiving the device HTTP MJPEG stream."""

from __future__ import annotations

import logging
import threading
import time
import urllib.request
from typing import Optional

import cv2
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage

from core.device_stream_service import (
    CONNECT_TIMEOUT,
    DEFAULT_HOST,
    DEFAULT_STREAM_PORT,
    FPS_WINDOW_SIZE,
    MAX_RECONNECT_ATTEMPTS,
    RECONNECT_INTERVAL,
    build_stream_url,
)

logger = logging.getLogger(__name__)

MAX_MJPEG_FRAME_BYTES = 8 * 1024 * 1024


class DeviceStreamThread(QThread):
    """HTTP MJPEG receiver thread."""

    frame_ready = pyqtSignal(QImage)
    stream_started = pyqtSignal()
    stream_stopped = pyqtSignal()
    stream_error = pyqtSignal(str)
    fps_updated = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop_event = threading.Event()
        self._host = DEFAULT_HOST
        self._stream_port = DEFAULT_STREAM_PORT
        self._response: Optional[object] = None
        self._frame_times: list[float] = []

    def setup(
        self,
        host: str = DEFAULT_HOST,
        stream_port: int = DEFAULT_STREAM_PORT,
    ):
        self._host = host
        self._stream_port = stream_port

    def stop(self):
        self._stop_event.set()
        response = self._response
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    def run(self):
        self._stop_event.clear()
        stream_url = build_stream_url(self._host, self._stream_port)
        reconnect_count = 0

        while not self._stop_event.is_set():
            try:
                logger.info("Connecting MJPEG stream: %s", stream_url)
                self._response = urllib.request.urlopen(
                    stream_url,
                    timeout=CONNECT_TIMEOUT,
                )
                reconnect_count = 0
                self._frame_times.clear()
                self.stream_started.emit()
                self._read_stream_loop(self._response)
            except Exception as exc:
                if self._stop_event.is_set():
                    break
                reconnect_count += 1
                if reconnect_count > MAX_RECONNECT_ATTEMPTS:
                    self.stream_error.emit(
                        f"连接实时画面失败，已重试 {MAX_RECONNECT_ATTEMPTS} 次：{exc}"
                    )
                    break
                self.stream_error.emit(
                    f"实时画面连接异常：{exc}，{RECONNECT_INTERVAL}s 后重试 "
                    f"({reconnect_count}/{MAX_RECONNECT_ATTEMPTS})"
                )
                if self._stop_event.wait(RECONNECT_INTERVAL):
                    break
            finally:
                self._close_response()

        self._close_response()
        self.stream_stopped.emit()

    def _read_stream_loop(self, response):
        while not self._stop_event.is_set():
            jpeg_data = self._read_mjpeg_frame(response)
            if jpeg_data is None:
                break

            array = np.frombuffer(jpeg_data, dtype=np.uint8)
            frame = cv2.imdecode(array, cv2.IMREAD_COLOR)
            if frame is None:
                logger.debug("Skipping undecodable JPEG frame")
                continue

            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            height, width, channels = rgb_frame.shape
            image = QImage(
                rgb_frame.data,
                width,
                height,
                channels * width,
                QImage.Format.Format_RGB888,
            ).copy()

            self.frame_ready.emit(image)
            self._update_fps()

    def _read_mjpeg_frame(self, response) -> Optional[bytes]:
        content_length = -1
        while not self._stop_event.is_set():
            line = response.readline()
            if not line:
                return None

            line_text = line.decode("utf-8", errors="ignore").strip()
            if line_text.lower().startswith("content-length:"):
                try:
                    content_length = int(line_text.split(":", 1)[1].strip())
                except ValueError:
                    content_length = -1

            if line_text == "" and content_length > 0:
                if content_length > MAX_MJPEG_FRAME_BYTES:
                    raise ValueError(
                        f"MJPEG frame too large: {content_length} bytes"
                    )
                data = self._read_exact(response, content_length)
                if data is None or len(data) != content_length:
                    return None
                return data

        return None

    def _read_exact(self, response, length: int) -> Optional[bytes]:
        chunks: list[bytes] = []
        remaining = length
        while remaining > 0 and not self._stop_event.is_set():
            chunk = response.read(remaining)
            if not chunk:
                return None
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _update_fps(self):
        now = time.monotonic()
        self._frame_times.append(now)
        if len(self._frame_times) > FPS_WINDOW_SIZE:
            self._frame_times = self._frame_times[-FPS_WINDOW_SIZE:]
        if len(self._frame_times) >= 2:
            elapsed = self._frame_times[-1] - self._frame_times[0]
            if elapsed > 0:
                fps = (len(self._frame_times) - 1) / elapsed
                self.fps_updated.emit(round(fps, 1))

    def _close_response(self):
        response = self._response
        self._response = None
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
