"""Qt workers for EPass RNDIS HTTP remote management."""

from __future__ import annotations

import logging
from pathlib import Path
import threading

from PyQt6.QtCore import QThread, pyqtSignal

from core.rndis_device_service import DEFAULT_BASE_URL
from core.remote_asset_manager import RemoteAssetManager

logger = logging.getLogger(__name__)


class RndisConnectWorker(QThread):
    """Detect the RNDIS adapter and probe the HTTP API."""

    connect_succeeded = pyqtSignal(dict)
    connect_failed = pyqtSignal(str)
    log_message = pyqtSignal(str, str)

    def __init__(self, manager: RemoteAssetManager | None = None, parent=None):
        super().__init__(parent)
        self._manager = manager or RemoteAssetManager()

    def run(self):
        try:
            self.log_message.emit("INFO", "正在检测 EPass RNDIS 网卡...")
            session = self._manager.connect(force=True)
            self.log_message.emit(
                "INFO",
                f"已通过 {session.adapter_name} 连接到 {session.base_url}",
            )
            if not session.authenticated:
                self.log_message.emit("WARNING", "设备要求认证，当前未完成配对")
            else:
                self.log_message.emit("INFO", "设备时间已同步")
            self.connect_succeeded.emit(session.to_ui_dict())
        except Exception as exc:
            logger.exception("RNDIS connection failed")
            self.log_message.emit("ERROR", str(exc))
            self.connect_failed.emit(str(exc))


class HttpListAssetsWorker(QThread):
    list_completed = pyqtSignal(list)
    list_failed = pyqtSignal(str)
    log_message = pyqtSignal(str, str)

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        manager: RemoteAssetManager | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._base_url = base_url
        self._manager = manager or RemoteAssetManager(base_url=base_url)

    def run(self):
        try:
            self.log_message.emit("INFO", "正在检测 RNDIS 连接...")
            session = self._manager.connect()
            self._base_url = session.base_url
            self.log_message.emit("INFO", "正在读取远程素材列表...")
            items = [item.to_ui_dict() for item in self._manager.list_assets()]
            self.list_completed.emit(items)
        except Exception as exc:
            logger.exception("Failed to list remote assets")
            self.log_message.emit("ERROR", str(exc))
            self.list_failed.emit(str(exc))


class HttpUploadAssetWorker(QThread):
    progress_updated = pyqtSignal(int, str)
    upload_completed = pyqtSignal(str)
    upload_failed = pyqtSignal(str)
    log_message = pyqtSignal(str, str)

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        local_path: str = "",
        enable_restart: bool = False,
        manager: RemoteAssetManager | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._base_url = base_url
        self._local_path = local_path
        self._enable_restart = enable_restart
        self._manager = manager or RemoteAssetManager(base_url=base_url)
        self._cancelled = threading.Event()

    def setup(
        self,
        local_path: str,
        enable_restart: bool = False,
        base_url: str = DEFAULT_BASE_URL,
    ):
        self._local_path = local_path
        self._enable_restart = enable_restart
        self._base_url = base_url
        self._cancelled.clear()

    def cancel(self):
        self._cancelled.set()

    def run(self):
        if not self._local_path:
            self.upload_failed.emit("缺少本地素材目录")
            return
        try:
            self.log_message.emit("INFO", "正在检测 EPass RNDIS 连接...")
            session = self._manager.connect()
            self._base_url = session.base_url

            def report(percent: int, message: str):
                self.progress_updated.emit(percent, message)

            self.log_message.emit(
                "INFO", f"正在通过 RNDIS HTTP 上传 {Path(self._local_path).name}..."
            )
            if self._cancelled.is_set():
                self.upload_failed.emit("上传已取消")
                return
            payload = self._manager.upload_asset(
                self._local_path,
                restart=self._enable_restart,
                progress=report,
                cancel_event=self._cancelled,
            )
            if self._cancelled.is_set():
                self.upload_failed.emit("上传已取消")
                return
            uuid = payload.get("uuid", "")
            suffix = f" ({uuid})" if uuid else ""
            self.upload_completed.emit(f"上传完成{suffix}")
        except Exception as exc:
            logger.exception("HTTP asset upload failed")
            self.log_message.emit("ERROR", str(exc))
            self.upload_failed.emit(str(exc))


class HttpDownloadAssetWorker(QThread):
    progress_updated = pyqtSignal(int, str)
    download_completed = pyqtSignal(str)
    download_failed = pyqtSignal(str)
    log_message = pyqtSignal(str, str)

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        manager: RemoteAssetManager | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._base_url = base_url
        self._manager = manager or RemoteAssetManager(base_url=base_url)
        self._uuid = ""
        self._save_dir = ""

    def setup(self, uuid: str, save_dir: str, base_url: str = DEFAULT_BASE_URL):
        self._uuid = uuid
        self._save_dir = save_dir
        self._base_url = base_url

    def run(self):
        try:
            self.log_message.emit("INFO", "正在检测 RNDIS 连接...")
            session = self._manager.connect()
            self._base_url = session.base_url
            path = self._manager.download_asset(
                self._uuid,
                self._save_dir,
                progress=lambda p, m: self.progress_updated.emit(p, m),
            )
            self.download_completed.emit(str(path))
        except Exception as exc:
            logger.exception("HTTP asset download failed")
            self.log_message.emit("ERROR", str(exc))
            self.download_failed.emit(str(exc))


class HttpDeleteAssetWorker(QThread):
    delete_completed = pyqtSignal(str)
    delete_failed = pyqtSignal(str)
    log_message = pyqtSignal(str, str)

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        manager: RemoteAssetManager | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._base_url = base_url
        self._manager = manager or RemoteAssetManager(base_url=base_url)
        self._uuid = ""
        self._name = ""

    def setup(self, uuid: str, name: str, base_url: str = DEFAULT_BASE_URL):
        self._uuid = uuid
        self._name = name
        self._base_url = base_url

    def run(self):
        try:
            self.log_message.emit("INFO", "正在检测 RNDIS 连接...")
            session = self._manager.connect()
            self._base_url = session.base_url
            self._manager.delete_asset(self._uuid)
            self.delete_completed.emit(self._name or self._uuid)
        except Exception as exc:
            logger.exception("HTTP asset delete failed")
            self.log_message.emit("ERROR", str(exc))
            self.delete_failed.emit(str(exc))


class HttpRestartDrmWorker(QThread):
    restart_succeeded = pyqtSignal()
    restart_failed = pyqtSignal(str)
    log_message = pyqtSignal(str, str)

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        manager: RemoteAssetManager | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._base_url = base_url
        self._manager = manager or RemoteAssetManager(base_url=base_url)

    def run(self):
        try:
            self.log_message.emit("INFO", "正在检测 RNDIS 连接...")
            session = self._manager.connect()
            self._base_url = session.base_url
            self.log_message.emit("INFO", "正在重启 DrmApp...")
            self._manager.restart_drm()
            self.restart_succeeded.emit()
        except Exception as exc:
            logger.exception("HTTP DrmApp restart failed")
            self.log_message.emit("ERROR", str(exc))
            self.restart_failed.emit(str(exc))
