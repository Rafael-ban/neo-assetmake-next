""" USB 管理器后台工作线程"""

from __future__ import annotations

import json
import logging
import os
from typing import Callable, Optional

from PyQt6.QtCore import QThread, pyqtSignal

from core.usb_control import UsbResponderClient

logger = logging.getLogger(__name__)


class UsbConnectWorker(QThread):
    """Create a UsbResponderClient and perform the devinfo handshake."""

    connect_succeeded = pyqtSignal(object, dict)  # usbRC, device_info dict
    connect_failed = pyqtSignal(object)  # exception

    def __init__(
        self,
        vid: int,
        pid: int,
        bus: int,
        address: int,
        interface: int = 0,
        timeout_ms: int = 3000,
        usb_exception_callback: Optional[Callable] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._vid = vid
        self._pid = pid
        self._bus = bus
        self._address = address
        self._interface = interface
        self._timeout_ms = timeout_ms
        self._usb_exception_callback = usb_exception_callback

    def run(self):
        try:
            usbRC = UsbResponderClient(
                vid=self._vid,
                pid=self._pid,
                bus=self._bus,
                address=self._address,
                interface=self._interface,
                timeout_ms=self._timeout_ms,
                disconnect_callback=self._usb_exception_callback,
            )
            kv = usbRC.hello()
            self.connect_succeeded.emit(usbRC, kv)
        except Exception as ex:
            logger.exception("USB connect failed")
            self.connect_failed.emit(ex)


class UsbListAssetsWorker(QThread):
    """List remote files and directories over USB."""

    list_completed = pyqtSignal(list, list)  # files, dirs
    list_failed = pyqtSignal(object)  # exception

    def __init__(self, usbRC: UsbResponderClient, path: str = ".", parent=None):
        super().__init__(parent)
        self._usbRC = usbRC
        self._path = path

    @staticmethod
    def _suppress_disconnect(usbRC, func, *args, **kwargs):
        """Temporarily suppress disconnect_callback so file_list errors
        only surface via list_failed, not as a duplicate control-page popup."""
        saved = usbRC._disconnect_callback
        usbRC._disconnect_callback = None
        try:
            return func(*args, **kwargs)
        finally:
            usbRC._disconnect_callback = saved

    def run(self):
        try:
            files, dirs = self._suppress_disconnect(
                self._usbRC, self._usbRC.file_list, self._path)
            self.list_completed.emit(files, dirs)
        except Exception as ex:
            logger.exception("USB list assets failed")
            self.list_failed.emit(ex)


class UsbListOperatorsWorker(QThread):
    """Walk /assets/ and /sd/assets/ subdirectories, parse epconfig.json, and cache preview icons."""

    _FALLBACK = "？？？（不合法缺省值）"

    # Base paths to scan for assets (internal storage + data disk)
    _ASSET_ROOTS = ["/assets", "/sd/assets"]

    list_completed = pyqtSignal(list)  # list of operator data dicts
    list_failed = pyqtSignal(object)  # exception
    progress_updated = pyqtSignal(int, str)

    def __init__(
        self,
        usbRC: UsbResponderClient,
        temp_dir: str,
        parent=None,
    ):
        super().__init__(parent)
        self._usbRC = usbRC
        self._temp_dir = temp_dir

    @staticmethod
    def _suppress_disconnect(usbRC, func, *args, **kwargs):
        """执行 func(*args, **kwargs)，期间暂时屏蔽 disconnect_callback。

        刷新列表中 file_get 缺失文件是正常现象（如某目录无 epconfig.json），
        不应触发 usbDisconnected → 断连整个 USB 会话。
        """
        saved = usbRC._disconnect_callback
        usbRC._disconnect_callback = None
        try:
            return func(*args, **kwargs)
        finally:
            usbRC._disconnect_callback = saved

    def _load_epconfig(self, base: str, dirname: str, idx: int) -> dict:
        """Download and parse {base}/{dirname}/epconfig.json.

        Returns the parsed dict on success, or an empty dict on any failure.
        """
        remote = f"{base}/{dirname}/epconfig.json"
        local = os.path.join(self._temp_dir, f"op_{idx}_epconfig.json")
        try:
            self._suppress_disconnect(
                self._usbRC, self._usbRC.file_get, remote, local)
            with open(local, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logger.warning("Failed to load epconfig: %s", remote)
            return {}

    def _download_icon(self, base: str, dirname: str, icon_filename: str, idx: int) -> str:
        """Download icon from {base}/{dirname}/{icon_filename} to temp dir.

        Falls back to /root/res/defaulticon.png when icon_filename is empty.
        Returns the local path on success, or empty string on failure.
        """
        if icon_filename:
            remote = f"{base}/{dirname}/{icon_filename}"
        else:
            remote = "/root/res/defaulticon.png"
        local = os.path.join(self._temp_dir, f"op_{idx}_icon.png")
        try:
            self._suppress_disconnect(
                self._usbRC, self._usbRC.file_get, remote, local)
            return local
        except Exception:
            logger.warning("Failed to download icon: %s", remote)
            return ""

    def _scan_root(self, base: str, operators: list, idx_offset: int) -> int:
        """Scan one asset root directory, append results to operators list.

        Returns the number of entries processed (for index tracking).
        """
        try:
            _, dirs = self._suppress_disconnect(
                self._usbRC, self._usbRC.file_list, base)
        except Exception:
            logger.warning("USB list %s failed (may not exist)", base)
            return 0

        total = len(dirs)
        count = 0

        for i, dirname in enumerate(dirs):
            # 跳过非素材目录（如隐藏文件、系统目录）
            if dirname.startswith("."):
                continue

            idx = idx_offset + count
            count += 1

            pct = int((i + 1) / max(total, 1) * 100)
            self.progress_updated.emit(pct, f"加载: {base}/{dirname}")

            info = self._load_epconfig(base, dirname, idx)
            if not info:
                # epconfig 不存在或无法解析 → 全部用缺省值填充
                operators.append({
                    "name": self._FALLBACK,
                    "uuid": self._FALLBACK,
                    "version": self._FALLBACK,
                    "screen": self._FALLBACK,
                    "description": "",
                    "path": f"{base}/{dirname}",
                    "delete_path": f"{base}/{dirname}",
                    "local_icon": "",
                    "is_dir": False,
                })
                continue

            name = info.get("name") or self._FALLBACK
            uuid = info.get("uuid") or self._FALLBACK
            version = info.get("version", self._FALLBACK)
            screen = info.get("screen") or self._FALLBACK
            icon_filename = info.get("icon", "")

            # 构建描述文本
            desc_parts = []
            if version != self._FALLBACK:
                desc_parts.append(f"版本: {version}")
            if screen != self._FALLBACK:
                desc_parts.append(f"分辨率: {screen}")
            description = "  |  ".join(desc_parts) if desc_parts else self._FALLBACK

            # 载入图标
            local_icon = self._download_icon(base, dirname, icon_filename, idx)

            operators.append({
                "name": name,
                "uuid": uuid,
                "version": version,
                "screen": screen,
                "description": description,
                "path": f"{base}/{dirname}",
                "delete_path": f"{base}/{dirname}",
                "local_icon": local_icon,
                "is_dir": False,
            })

        return count

    def run(self):
        operators: list = []

        # /assets is mandatory — fail early if it can't be listed
        try:
            self._usbRC.file_list("/assets")
        except Exception as ex:
            logger.exception("USB list /assets failed")
            self.list_failed.emit(ex)
            return

        idx_offset = 0
        for base in self._ASSET_ROOTS:
            processed = self._scan_root(base, operators, idx_offset)
            idx_offset += processed

        self.list_completed.emit(operators)


class UsbUploadAssetWorker(QThread):
    """Upload a local file or directory tree over USB."""

    progress_updated = pyqtSignal(int, str)
    upload_completed = pyqtSignal(str)
    upload_failed = pyqtSignal(object)

    def __init__(
        self,
        usbRC: UsbResponderClient,
        local_path: str,
        remote_path: str,
        chunk_size: int = 16 * 1024 - 4,
        parent=None,
    ):
        super().__init__(parent)
        self._usbRC = usbRC
        self._local_path = local_path
        self._remote_path = remote_path.rstrip("/")
        self._chunk_size = chunk_size

    def run(self):
        try:
            if os.path.isfile(self._local_path):
                self._upload_file(self._local_path, self._remote_path)
            else:
                self._upload_directory()
            self.upload_completed.emit(self._remote_path)
        except Exception as ex:
            logger.exception("USB upload failed")
            self.upload_failed.emit(ex)

    def _upload_file(self, local: str, remote: str):
        self._usbRC.file_put(local, remote, chunk_size=self._chunk_size)

    def _upload_directory(self):
        # 确保目标根目录存在（如 /assets/{uuid}）
        self._usbRC.dir_mkdir(self._remote_path, parents=True)

        # Collect all files and directories first so we can report progress
        entries: list[tuple[str, str, bool]] = []  # (local, remote, is_dir)
        local_base = os.path.normpath(self._local_path)
        for dirpath, dirnames, filenames in os.walk(local_base):
            rel = os.path.relpath(dirpath, local_base)
            if rel == ".":
                remote_dir = self._remote_path
            else:
                remote_dir = self._remote_path + "/" + rel.replace("\\", "/")
            for name in dirnames:
                entries.append((
                    os.path.join(dirpath, name),
                    remote_dir + "/" + name,
                    True,
                ))
            for name in filenames:
                entries.append((
                    os.path.join(dirpath, name),
                    remote_dir + "/" + name,
                    False,
                ))

        # epconfig.json 必须最后上传，避免设备提前开始处理素材
        entries.sort(key=lambda e: (
            os.path.basename(e[0]) == "epconfig.json", e[0]))

        total = len(entries)
        if total == 0:
            return

        failed: list[str] = []

        for idx, (local, remote, is_dir) in enumerate(entries):
            pct = int((idx + 1) / total * 100)
            if is_dir:
                self.progress_updated.emit(pct, f"创建目录: {remote}")
                try:
                    self._usbRC.dir_mkdir(remote, parents=True)
                except Exception as ex:
                    logger.warning("mkdir failed: %s — %s", remote, ex)
                    failed.append(remote)
            else:
                self.progress_updated.emit(
                    pct, f"上传: {os.path.basename(local)}")
                try:
                    self._usbRC.file_put(
                        local, remote, chunk_size=self._chunk_size)
                except Exception as ex:
                    logger.warning("upload failed: %s — %s", remote, ex)
                    failed.append(remote)

        if failed:
            raise RuntimeError(
                f"{len(failed)}/{total} 个文件上传失败: {', '.join(failed[:5])}"
                + ("..." if len(failed) > 5 else "")
            )


class UsbDownloadAssetWorker(QThread):
    """Download a remote directory tree over USB (single file also supported)."""

    progress_updated = pyqtSignal(int, str)
    download_completed = pyqtSignal(str)
    download_failed = pyqtSignal(object)

    def __init__(
        self,
        usbRC: UsbResponderClient,
        remote_path: str,
        local_path: str,
        parent=None,
    ):
        super().__init__(parent)
        self._usbRC = usbRC
        self._remote_path = remote_path.rstrip("/")
        self._local_path = local_path

    def run(self):
        try:
            self._download_tree(self._remote_path, self._local_path)
            self.download_completed.emit(self._local_path)
        except Exception as ex:
            logger.exception("USB download failed")
            self.download_failed.emit(ex)

    def _collect_files(self, remote_dir: str) -> list[tuple[str, str]]:
        """递归收集远程目录下所有文件，返回 [(remote_path, filename), ...]。
        子目录被展平 — 所有文件直接归入根目录，不保留子目录层级。
        """
        result: list[tuple[str, str]] = []
        try:
            files, dirs = self._usbRC.file_list(remote_dir)
        except Exception:
            return result
        for name in files:
            result.append((f"{remote_dir}/{name}", name))
        for name in dirs:
            result.extend(self._collect_files(f"{remote_dir}/{name}"))
        return result

    def _download_tree(self, remote_dir: str, local_dir: str):
        """下载远程目录树，所有文件展平到 local_dir。"""
        os.makedirs(local_dir, exist_ok=True)
        entries = self._collect_files(remote_dir)
        if not entries:
            # 可能是单文件或无文件
            return
        total = len(entries)
        for idx, (remote, filename) in enumerate(entries):
            pct = int((idx + 1) / total * 100)
            self.progress_updated.emit(pct, f"下载: {filename}")
            self._usbRC.file_get(remote, os.path.join(local_dir, filename))


class UsbDeleteAssetWorker(QThread):
    """Delete a remote file or directory over USB."""

    delete_completed = pyqtSignal(str)
    delete_failed = pyqtSignal(object)

    def __init__(
        self,
        usbRC: UsbResponderClient,
        remote_path: str,
        parent=None,
    ):
        super().__init__(parent)
        self._usbRC = usbRC
        self._remote_path = remote_path

    def run(self):
        try:
            self._usbRC.file_delete(self._remote_path)
            self.delete_completed.emit(self._remote_path)
        except Exception as ex:
            logger.exception("USB delete failed")
            self.delete_failed.emit(ex)


class UsbListAppsWorker(QThread):
    """Walk /app/ and /sd/app/ subdirectories, parse appconfig.json, and cache preview icons."""

    _FALLBACK = "？？？（不合法缺省值）"

    # Base paths to scan for apps (internal storage + data disk)
    _APP_ROOTS = ["/app", "/sd/app"]

    list_completed = pyqtSignal(list)  # list of app data dicts
    list_failed = pyqtSignal(object)  # exception
    progress_updated = pyqtSignal(int, str)

    def __init__(
        self,
        usbRC: UsbResponderClient,
        temp_dir: str,
        parent=None,
    ):
        super().__init__(parent)
        self._usbRC = usbRC
        self._temp_dir = temp_dir

    @staticmethod
    def _suppress_disconnect(usbRC, func, *args, **kwargs):
        """Execute func(*args, **kwargs), temporarily suppress disconnect_callback."""
        saved = usbRC._disconnect_callback
        usbRC._disconnect_callback = None
        try:
            return func(*args, **kwargs)
        finally:
            usbRC._disconnect_callback = saved

    def _load_epconfig(self, base: str, dirname: str, idx: int) -> dict:
        """Download and parse {base}/{dirname}/appconfig.json.

        Returns the parsed dict on success, or an empty dict on any failure.
        """
        remote = f"{base}/{dirname}/appconfig.json"
        local = os.path.join(self._temp_dir, f"app_{idx}_appconfig.json")
        try:
            self._suppress_disconnect(
                self._usbRC, self._usbRC.file_get, remote, local)
            with open(local, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logger.warning("Failed to load epconfig: %s", remote)
            return {}

    def _download_icon(self, base: str, dirname: str, icon_filename: str, idx: int) -> str:
        """Download icon from {base}/{dirname}/{icon_filename} to temp dir.

        Falls back to /root/res/defaulticon.png when icon_filename is empty.
        Returns the local path on success, or empty string on failure.
        """
        if icon_filename:
            remote = f"{base}/{dirname}/{icon_filename}"
        else:
            remote = "/root/res/defaulticon.png"
        local = os.path.join(self._temp_dir, f"app_{idx}_icon.png")
        try:
            self._suppress_disconnect(
                self._usbRC, self._usbRC.file_get, remote, local)
            return local
        except Exception:
            logger.warning("Failed to download icon: %s", remote)
            return ""

    def _scan_root(self, base: str, apps: list, idx_offset: int) -> int:
        """Scan one app root directory, append results to apps list.

        Returns the number of entries processed (for index tracking).
        """
        try:
            _, dirs = self._suppress_disconnect(
                self._usbRC, self._usbRC.file_list, base)
        except Exception:
            logger.warning("USB list %s failed (may not exist)", base)
            return 0

        total = len(dirs)
        count = 0

        for i, dirname in enumerate(dirs):
            if dirname.startswith("."):
                continue

            idx = idx_offset + count
            count += 1

            pct = int((i + 1) / max(total, 1) * 100)
            self.progress_updated.emit(pct, f"加载: {base}/{dirname}")

            info = self._load_epconfig(base, dirname, idx)
            if not info:
                apps.append({
                    "name": self._FALLBACK,
                    "uuid": self._FALLBACK,
                    "version": self._FALLBACK,
                    "screen": self._FALLBACK,
                    "description": "",
                    "path": f"{base}/{dirname}",
                    "delete_path": f"{base}/{dirname}",
                    "local_icon": "",
                    "is_dir": True,
                })
                continue

            name = info.get("name") or self._FALLBACK
            uuid = info.get("uuid") or self._FALLBACK
            version = info.get("version", self._FALLBACK)
            screen = info.get("screen") or self._FALLBACK
            icon_filename = info.get("icon", "")

            desc_parts = []
            if version != self._FALLBACK:
                desc_parts.append(f"版本: {version}")
            if screen != self._FALLBACK:
                desc_parts.append(f"分辨率: {screen}")
            description = "  |  ".join(desc_parts) if desc_parts else self._FALLBACK

            local_icon = self._download_icon(base, dirname, icon_filename, idx)

            apps.append({
                "name": name,
                "uuid": uuid,
                "version": version,
                "screen": screen,
                "description": description,
                "path": f"{base}/{dirname}",
                "delete_path": f"{base}/{dirname}",
                "local_icon": local_icon,
                "is_dir": True,
            })

        return count

    def run(self):
        apps: list = []

        # /app is mandatory — fail early if it can't be listed
        try:
            self._usbRC.file_list("/app")
        except Exception as ex:
            logger.exception("USB list /app failed")
            self.list_failed.emit(ex)
            return

        idx_offset = 0
        for base in self._APP_ROOTS:
            processed = self._scan_root(base, apps, idx_offset)
            idx_offset += processed

        self.list_completed.emit(apps)


class UsbRestartDrmWorker(QThread):
    """Restart DrmApp on the device over USB."""

    restart_succeeded = pyqtSignal()
    restart_failed = pyqtSignal(object)

    def __init__(
        self,
        usbRC: UsbResponderClient,
        command: str = "epassctl app exit 1",
        parent=None,
    ):
        super().__init__(parent)
        self._usbRC = usbRC
        self._command = command

    def run(self):
        try:
            result = self._usbRC.command_exec(self._command)
            stdout = result.stdout.decode(
                "utf-8", errors="replace").strip().lower()
            if "ok" not in stdout:
                raise RuntimeError(f"restart app 返回异常: {stdout}")
            self.restart_succeeded.emit()
        except Exception as ex:
            logger.exception("USB restart DRM failed")
            self.restart_failed.emit(ex)


class UsbReloadAssetsWorker(QThread):
    """Reload assets on the device after upload/delete over USB."""

    reload_succeeded = pyqtSignal()
    reload_failed = pyqtSignal(object)

    def __init__(self, usbRC: UsbResponderClient, parent=None):
        super().__init__(parent)
        self._usbRC = usbRC

    def run(self):
        try:
            result = self._usbRC.command_exec("epassctl prts reload_assets")
            stdout = result.stdout.decode(
                "utf-8", errors="replace").strip().lower()
            if "ok" not in stdout:
                raise RuntimeError(f"reload_assets 返回异常: {stdout}")
            self.reload_succeeded.emit()
        except Exception as ex:
            logger.exception("USB reload assets failed")
            self.reload_failed.emit(ex)


class UsbRebootWorker(QThread):
    """Reboot the device over USB. 不判定返回值，执行完毕即通知完成。"""

    reboot_completed = pyqtSignal()

    def __init__(self, usbRC: UsbResponderClient, parent=None):
        super().__init__(parent)
        self._usbRC = usbRC

    def run(self):
        try:
            self._usbRC.command_exec("reboot")
        except Exception:
            # 设备即将断开，异常属于预期行为
            pass
        self.reboot_completed.emit()


class UsbCopyWorker(QThread):
    """Copy a remote file or directory via cp -r."""

    copy_completed = pyqtSignal()
    copy_failed = pyqtSignal(object)

    def __init__(self, usbRC: UsbResponderClient, src: str, dst: str, parent=None):
        super().__init__(parent)
        self._usbRC = usbRC
        self._src = src
        self._dst = dst

    def run(self):
        try:
            result = self._usbRC.command_exec(f"cp -r {self._src} {self._dst}")
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            if stderr:
                raise RuntimeError(stderr)
            self.copy_completed.emit()
        except Exception as ex:
            logger.exception("USB copy failed")
            self.copy_failed.emit(ex)


class UsbMoveWorker(QThread):
    """Move/rename a remote file or directory via file_rename."""

    move_completed = pyqtSignal()
    move_failed = pyqtSignal(object)

    def __init__(self, usbRC: UsbResponderClient, src: str, dst: str, parent=None):
        super().__init__(parent)
        self._usbRC = usbRC
        self._src = src
        self._dst = dst

    def run(self):
        try:
            self._usbRC.file_rename(self._src, self._dst)
            self.move_completed.emit()
        except Exception as ex:
            logger.exception("USB move failed")
            self.move_failed.emit(ex)


class UsbStatWorker(QThread):
    """Get file/directory stat info."""

    stat_completed = pyqtSignal(dict)
    stat_failed = pyqtSignal(object)

    def __init__(self, usbRC: UsbResponderClient, path: str, parent=None):
        super().__init__(parent)
        self._usbRC = usbRC
        self._path = path

    def run(self):
        try:
            info = self._usbRC.file_stat(self._path)
            self.stat_completed.emit(info)
        except Exception as ex:
            logger.exception("USB stat failed")
            self.stat_failed.emit(ex)


class UsbMkdirWorker(QThread):
    """Create a remote directory."""

    mkdir_completed = pyqtSignal()
    mkdir_failed = pyqtSignal(object)

    def __init__(self, usbRC: UsbResponderClient, path: str, parent=None):
        super().__init__(parent)
        self._usbRC = usbRC
        self._path = path

    def run(self):
        try:
            self._usbRC.dir_mkdir(self._path, parents=True)
            self.mkdir_completed.emit()
        except Exception as ex:
            logger.exception("USB mkdir failed")
            self.mkdir_failed.emit(ex)
