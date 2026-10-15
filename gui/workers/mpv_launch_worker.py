"""Qt worker for launching mpv and connecting IPC in background thread."""

from __future__ import annotations

import logging
import time
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal, QProcess
from PyQt6.QtNetwork import QLocalSocket

logger = logging.getLogger(__name__)


class MpvLaunchWorker(QThread):
    """Background worker for mpv process startup and IPC connection.

    Signals:
        launched: Emitted when mpv successfully starts and IPC connects
        failed: Emitted when startup or IPC connection fails (with error message)
    """

    launched = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, mpv_path: str, args: list[str], ipc_server: str, parent=None):
        super().__init__(parent)
        self.mpv_path = mpv_path
        self.args = args
        self.ipc_server = ipc_server
        self.process: Optional[QProcess] = None
        self.socket: Optional[QLocalSocket] = None

    def run(self):
        """执行 mpv 启动和 IPC 连接（在工作线程中，不阻塞 UI）"""
        try:
            # 创建进程对象
            self.process = QProcess()
            self.process.start(self.mpv_path, self.args)

            # 等待进程启动（这里的阻塞在工作线程中，不影响 UI）
            if not self.process.waitForStarted(3000):
                error_msg = f"mpv process failed to start: {self.process.errorString()}"
                logger.error(error_msg)
                self.failed.emit(error_msg)
                return

            logger.debug("mpv process started, attempting IPC connection...")

            # 尝试 IPC 连接
            if self._connect_ipc():
                logger.info("mpv IPC connection established")
                self.launched.emit()
            else:
                error_msg = "mpv IPC connection failed after multiple attempts"
                logger.error(error_msg)
                # 连接失败，终止进程
                if self.process and self.process.state() == QProcess.ProcessState.Running:
                    self.process.terminate()
                    self.process.waitForFinished(500)
                self.failed.emit(error_msg)

        except Exception as e:
            error_msg = f"mpv launch worker exception: {e}"
            logger.exception(error_msg)
            self.failed.emit(error_msg)

    def _connect_ipc(self) -> bool:
        """尝试连接 mpv JSON IPC（在工作线程中）"""
        deadline = time.monotonic() + 10.0
        attempts = 0
        last_error = ""

        while time.monotonic() < deadline:
            attempts += 1

            if self.socket is None:
                self.socket = QLocalSocket()

            self.socket.connectToServer(self.ipc_server)

            # 等待连接（阻塞在工作线程中）
            if self.socket.waitForConnected(100):
                logger.debug(f"mpv JSON IPC connected after {attempts} attempts")
                return True

            last_error = self.socket.errorString()
            self.socket.abort()
            self.socket.deleteLater()
            self.socket = None

            # 在工作线程中 sleep 不影响 UI
            time.sleep(0.01)

        logger.warning(
            f"Failed to connect to mpv IPC after {attempts} attempts. "
            f"Last error: {last_error}"
        )
        return False

    def cleanup(self):
        """清理资源"""
        if self.socket:
            self.socket.abort()
            self.socket.deleteLater()
            self.socket = None

        if self.process and self.process.state() == QProcess.ProcessState.Running:
            self.process.terminate()
            self.process.waitForFinished(500)
