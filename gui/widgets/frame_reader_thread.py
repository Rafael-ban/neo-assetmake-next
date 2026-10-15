"""Compatibility layer for the retired background frame reader."""

from __future__ import annotations

import logging
import queue
from collections import deque
from dataclasses import dataclass
from threading import Condition, Lock
from typing import Optional

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage

logger = logging.getLogger(__name__)


@dataclass
class BufferedFrame:
    """One buffered preview frame."""

    frame_index: int
    yuv_data: Optional[tuple[bytes, bytes, bytes, int, int]] = None
    qimage: Optional[QImage] = None
    raw_frame: Optional[np.ndarray] = None
    params_version: int = 0


class FrameRingBuffer:
    """Small thread-safe ring buffer retained for compatibility."""

    def __init__(self, max_size: int = 5):
        self._buffer: deque[BufferedFrame] = deque(maxlen=max_size)
        self._lock = Lock()
        self._not_full = Condition(self._lock)
        self._max_size = max_size

    def put(self, frame: BufferedFrame) -> bool:
        with self._lock:
            if len(self._buffer) >= self._max_size:
                return False
            self._buffer.append(frame)
            return True

    def get(self) -> Optional[BufferedFrame]:
        with self._lock:
            if not self._buffer:
                return None
            frame = self._buffer.popleft()
            self._not_full.notify()
            return frame

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()
            self._not_full.notify_all()

    def wait_not_full(self, timeout=None) -> bool:
        with self._not_full:
            if len(self._buffer) < self._max_size:
                return True
            self._not_full.wait(timeout=timeout)
            return len(self._buffer) < self._max_size

    def size(self) -> int:
        with self._lock:
            return len(self._buffer)

    def is_full(self) -> bool:
        with self._lock:
            return len(self._buffer) >= self._max_size

    def is_empty(self) -> bool:
        with self._lock:
            return not self._buffer

    @property
    def max_size(self) -> int:
        return self._max_size

    @max_size.setter
    def max_size(self, value: int) -> None:
        with self._lock:
            self._max_size = value
            self._buffer = deque(self._buffer, maxlen=value)
            self._not_full.notify_all()


class FrameReaderThread(QThread):
    """Deprecated frame reader API.

    Video playback is now owned by the mpv preview backend. This class exists
    so older callers can still connect signals and receive a clear failure
    rather than importing a removed decoder stack.
    """

    video_opened = pyqtSignal(float, int, int, int)
    video_open_failed = pyqtSignal(str)
    frame_ready = pyqtSignal(int, QImage, object)
    yuv_frame_ready = pyqtSignal(int, bytes, bytes, bytes, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._command_queue: queue.Queue = queue.Queue()
        self._frame_buffer = FrameRingBuffer(max_size=5)
        self._params_version = 0
        self._stopping = False

    @property
    def frame_buffer(self) -> FrameRingBuffer:
        return self._frame_buffer

    @property
    def params_version(self) -> int:
        return self._params_version

    def start_prefetch(self) -> None:
        pass

    def stop_prefetch(self) -> None:
        pass

    def request_open(self, path: str) -> None:
        logger.warning("Deprecated frame reader cannot open video: %s", path)
        self.video_open_failed.emit("Video preview is handled by mpv")

    def request_read_next(self) -> None:
        pass

    def request_seek(self, frame_index: int) -> None:
        self._frame_buffer.clear()

    def request_set_rotation(self, degrees: int) -> None:
        self._params_version += 1

    def request_set_cropbox(self, cropbox: list) -> None:
        self._params_version += 1

    def request_set_preview_params(
        self,
        preview_mode: bool,
        target_width: int,
        target_height: int,
        epconfig=None,
        overlay_renderer=None,
    ) -> None:
        self._params_version += 1

    def request_set_gl_mode(self, enabled: bool) -> None:
        self._params_version += 1

    def request_stop(self) -> None:
        self._stopping = True

    def run(self) -> None:
        self._stopping = False
        while not self._stopping:
            self.msleep(50)
