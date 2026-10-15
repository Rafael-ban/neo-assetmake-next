"""RNDIS HTTP remote management page."""

from __future__ import annotations

from datetime import datetime
import logging
import os
import tempfile
from urllib.parse import urlsplit

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QMessageBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from qfluentwidgets import (
    CaptionLabel,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    ListWidget,
    PlainTextEdit,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    SimpleCardWidget,
    StrongBodyLabel,
    SubtitleLabel,
    setCustomStyleSheet,
)

from core.rndis_device_service import DEFAULT_BASE_URL, DEFAULT_DEVICE_HOST
from core.remote_asset_manager import RemoteAssetManager
from gui.texts import remote_user_error
from gui.workers.rndis_http_workers import (
    HttpDeleteAssetWorker,
    HttpDownloadAssetWorker,
    HttpListAssetsWorker,
    HttpRestartDrmWorker,
    HttpUploadAssetWorker,
    RndisConnectWorker,
)

logger = logging.getLogger(__name__)


class AssetListItemWidget(QWidget):
    """List item for a remote asset package."""

    def __init__(self, asset_data: dict, parent=None):
        super().__init__(parent)
        self.asset_data = asset_data
        self.parent_page = parent

        self.thumbnail_label = QLabel()
        self.thumbnail_label.setFixedSize(64, 64)
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_label.setText("素材")
        self.thumbnail_label.setStyleSheet(
            "border: 1px solid #ccc; border-radius: 4px; color: #777;"
        )

        self.name_label = CaptionLabel(asset_data.get("name", "Unnamed asset"))
        self.uuid_label = CaptionLabel(f"UUID: {asset_data.get('uuid', '')}")
        self.path_label = CaptionLabel(
            f"路径: {asset_data.get('path', '/assets')}")

        text_layout = QVBoxLayout()
        text_layout.setSpacing(3)
        text_layout.addWidget(self.name_label)
        text_layout.addWidget(self.uuid_label)
        text_layout.addWidget(self.path_label)

        self.btn_delete = PushButton("删除")
        self.btn_delete.setIcon(FluentIcon.DELETE)
        self.btn_download = PushButton("下载")
        self.btn_download.setIcon(FluentIcon.DOWNLOAD)
        self.btn_edit = PushButton("编辑")
        self.btn_edit.setIcon(FluentIcon.EDIT)

        action_layout = QVBoxLayout()
        action_layout.setSpacing(4)
        action_layout.addWidget(self.btn_delete)
        action_layout.addWidget(self.btn_download)
        action_layout.addWidget(self.btn_edit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)
        layout.addWidget(self.thumbnail_label,
                         alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(text_layout, stretch=1)
        layout.addLayout(action_layout)

        self.btn_delete.clicked.connect(
            lambda: self.parent_page._on_delete_for_asset(self.asset_data)
        )
        self.btn_download.clicked.connect(
            lambda: self.parent_page._on_download_for_asset(self.asset_data)
        )
        self.btn_edit.clicked.connect(
            lambda: self.parent_page._on_edit_for_asset(self.asset_data)
        )

    def set_buttons_enabled(self, enabled: bool):
        self.btn_delete.setEnabled(enabled)
        self.btn_download.setEnabled(enabled)
        self.btn_edit.setEnabled(enabled)


class RemotePage(QWidget):
    """Remote asset management over EPass RNDIS HTTP API."""

    setting_changed = pyqtSignal(str, object)
    upload_requested = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._parent = parent
        self._settings: dict = {}
        self._base_url = DEFAULT_BASE_URL
        self._remote_manager = RemoteAssetManager(base_url=self._base_url)
        self._is_connected = False
        self._is_busy = False
        self._stream_widget = None

        self._connect_worker = None
        self._list_worker = None
        self._upload_worker = None
        self._download_worker = None
        self._delete_worker = None
        self._restart_worker = None

        self._init_ui()
        self._connect_signals()
        self._update_connection_ui("disconnected")

    def _init_ui(self):
        self.mainLayout = QVBoxLayout(self)
        self.mainLayout.setContentsMargins(0, 15, 0, 0)
        self.mainLayout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.titleLabel = SubtitleLabel("EPass RNDIS 远程管理", self)
        self.titleLabel.setContentsMargins(30, 0, 0, 0)
        self.mainLayout.addWidget(self.titleLabel)
        self.mainLayout.addSpacing(10)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setContentsMargins(10, 0, 10, 10)
        self._build_left_panel()
        self._build_middle_panel()
        self._build_right_panel()
        self.splitter.addWidget(self.leftPanel)
        self.splitter.addWidget(self.middlePanel)
        self.splitter.addWidget(self.rightPanel)
        self.splitter.setSizes([250, 560, 300])
        self.splitter.setStretchFactor(1, 3)
        self.mainLayout.addWidget(self.splitter, 1)

        self.connectionStatusLabel = CaptionLabel("未连接")
        self.progressBar = ProgressBar()
        self.progressBar.setVisible(False)
        self.progressLabel = CaptionLabel(" ")
        self.progressLabel.setWordWrap(True)

        wrapper = QVBoxLayout()
        wrapper.setContentsMargins(10, 0, 10, 0)
        wrapper.addWidget(self.connectionStatusLabel)
        wrapper.addWidget(self.progressBar)
        wrapper.addWidget(self.progressLabel)
        self.mainLayout.addLayout(wrapper)

    def _build_left_panel(self):
        self.leftPanel = SimpleCardWidget()
        self.leftPanel.setMinimumWidth(230)
        self.leftPanel.setMaximumWidth(300)

        layout = QVBoxLayout(self.leftPanel)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.btnConnect = PrimaryPushButton("连接设备")
        self.btnConnect.setIcon(FluentIcon.WIFI)
        layout.addWidget(self.btnConnect)

        self.btnRefreshList = PushButton("刷新素材")
        self.btnRefreshList.setIcon(FluentIcon.SYNC)
        layout.addWidget(self.btnRefreshList)

        self.btnUploadLocal = PushButton("上传素材")
        self.btnUploadLocal.setIcon(FluentIcon.SEND)
        layout.addWidget(self.btnUploadLocal)

        self.btnStream = PushButton("实时画面")
        self.btnStream.setIcon(FluentIcon.PLAY)
        layout.addWidget(self.btnStream)

        self.btnRestartDrm = PushButton("重启 DrmApp")
        self.btnRestartDrm.setIcon(FluentIcon.UPDATE)
        layout.addWidget(self.btnRestartDrm)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        self.networkHintLabel = CaptionLabel(
            "目标：EPass RNDIS 网卡，固定访问 http://192.168.137.2/"
        )
        self.networkHintLabel.setWordWrap(True)
        layout.addWidget(self.networkHintLabel)

    def _build_middle_panel(self):
        self.middlePanel = SimpleCardWidget()
        layout = QVBoxLayout(self.middlePanel)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.middleTitleLabel = CaptionLabel("远程素材")
        layout.addWidget(self.middleTitleLabel)

        self.assetDetailList = ListWidget()
        self.assetDetailList.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        setCustomStyleSheet(
            self.assetDetailList,
            "ListWidget { border: none; background: transparent; }",
            "ListWidget { border: none; background: transparent; }",
        )
        layout.addWidget(self.assetDetailList, stretch=1)

    def _build_right_panel(self):
        self.rightPanel = SimpleCardWidget()
        layout = QVBoxLayout(self.rightPanel)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.logTitleLabel = StrongBodyLabel("操作日志")
        layout.addWidget(self.logTitleLabel)

        self.logTextEdit = PlainTextEdit()
        self.logTextEdit.setReadOnly(True)
        self.logTextEdit.setMaximumBlockCount(1000)
        self.logTextEdit.setFont(QFont("Consolas", 10))
        setCustomStyleSheet(
            self.logTextEdit,
            "PlainTextEdit { border: none; padding: 8px; }",
            "PlainTextEdit { border: none; padding: 8px; }",
        )
        layout.addWidget(self.logTextEdit, stretch=1)

        self.btnClearLog = PushButton("清空日志")
        self.btnClearLog.setIcon(FluentIcon.DELETE)
        layout.addWidget(self.btnClearLog)

    def _connect_signals(self):
        self.btnConnect.clicked.connect(self._on_connect)
        self.btnRefreshList.clicked.connect(self._on_refresh_list)
        self.btnUploadLocal.clicked.connect(self._on_upload_local)
        self.btnStream.clicked.connect(self._on_device_stream)
        self.btnRestartDrm.clicked.connect(self._on_restart_drm)
        self.btnClearLog.clicked.connect(self.logTextEdit.clear)

    def _log(self, level: str, msg: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.logTextEdit.appendPlainText(f"[{timestamp}] [{level}] {msg}")
        self.logTextEdit.verticalScrollBar().setValue(
            self.logTextEdit.verticalScrollBar().maximum()
        )
        getattr(logger, level.lower(), logger.info)(msg)

    def _worker_log(self, level: str, msg: str):
        self._log(level, msg)

    def _show_placeholder(self, message: str):
        self.assetDetailList.clear()
        placeholder = QListWidgetItem(message)
        placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
        self.assetDetailList.addItem(placeholder)

    def _set_busy(self, busy: bool):
        self._is_busy = busy
        self.btnConnect.setEnabled(not busy)
        self.btnRefreshList.setEnabled(not busy and self._is_connected)
        self.btnUploadLocal.setEnabled(not busy and self._is_connected)
        self.btnStream.setEnabled(not busy and self._is_connected)
        self.btnRestartDrm.setEnabled(not busy and self._is_connected)
        for i in range(self.assetDetailList.count()):
            item = self.assetDetailList.item(i)
            widget = self.assetDetailList.itemWidget(item)
            if widget:
                widget.set_buttons_enabled(not busy)

    def _update_connection_ui(self, state: str):
        if state == "connected":
            self._is_connected = True
            self.connectionStatusLabel.setText(f"已连接：{self._base_url}")
            self.btnConnect.setText("断开连接")
        elif state in {"detecting_adapter", "probing_device"}:
            self.connectionStatusLabel.setText("正在检测 EPass RNDIS 网卡...")
            self.btnConnect.setText("连接中...")
        elif state == "failed":
            self._is_connected = False
            self.connectionStatusLabel.setText("连接失败")
            self.btnConnect.setText("连接设备")
        else:
            self._is_connected = False
            self.connectionStatusLabel.setText("未连接")
            self.btnConnect.setText("连接设备")
            self._show_placeholder(
                "请先连接设备：1. 插入通行证设备  2. 确认 Windows 出现 EPass RNDIS 网卡  "
                "3. 点击左侧“连接设备”"
            )
            self._stop_stream()
        self._set_busy(self._is_busy)

    def _on_connect(self):
        if self._is_busy:
            return
        if self._is_connected:
            self._log("INFO", "已断开连接")
            self._remote_manager.disconnect()
            self._update_connection_ui("disconnected")
            return

        self._set_busy(True)
        self._update_connection_ui("detecting_adapter")
        self._connect_worker = RndisConnectWorker(
            self._remote_manager, parent=self)
        self._connect_worker.log_message.connect(self._worker_log)
        self._connect_worker.connect_succeeded.connect(
            self._on_connect_success)
        self._connect_worker.connect_failed.connect(self._on_connect_fail)
        self._connect_worker.start()

    def _on_connect_success(self, data: dict):
        self._base_url = data.get("base_url", DEFAULT_BASE_URL)
        self._set_busy(False)
        self._update_connection_ui("connected")
        InfoBar.success(
            "已连接",
            f"EPass HTTP API 可用：{self._base_url}",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=3000,
        )
        if data.get("auth_required") and not data.get("authenticated", True):
            InfoBar.warning(
                "需要认证",
                "设备要求配对或认证。请在设置中配置设备 token 后重试。",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=6000,
            )
            return
        self._on_refresh_list()

    def _on_connect_fail(self, error: str):
        self._set_busy(False)
        self._update_connection_ui("failed")
        InfoBar.error(
            "连接失败",
            _user_error(error),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=6000,
        )

    def _on_refresh_list(self):
        if self._is_busy or not self._is_connected:
            return
        self._set_busy(True)
        self._list_worker = HttpListAssetsWorker(
            self._base_url, manager=self._remote_manager, parent=self
        )
        self._list_worker.log_message.connect(self._worker_log)
        self._list_worker.list_completed.connect(self._on_list_loaded)
        self._list_worker.list_failed.connect(self._on_list_failed)
        self._list_worker.start()

    def _on_list_loaded(self, items: list):
        self.assetDetailList.clear()
        if not items:
            self._show_placeholder("设备端暂未返回素材。")
        for item in items:
            widget = AssetListItemWidget(item, self)
            list_item = QListWidgetItem(self.assetDetailList)
            list_item.setSizeHint(widget.sizeHint())
            self.assetDetailList.setItemWidget(list_item, widget)
        self._set_busy(False)

    def _on_list_failed(self, error: str):
        self._set_busy(False)
        InfoBar.error(
            "刷新失败",
            _user_error(error),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )

    def _on_upload_local(self):
        if self._is_busy or not self._is_connected:
            return
        path = QFileDialog.getExistingDirectory(self, "选择素材目录", "")
        if not path:
            return
        enable_restart = self._settings.get(
            "remote_auto_restart_program",
            self._settings.get("ssh_auto_restart_program", True),
        )
        self._set_busy(True)
        self.progressBar.setVisible(True)
        self.progressBar.setValue(0)
        self._upload_worker = HttpUploadAssetWorker(
            self._base_url, path, enable_restart, manager=self._remote_manager, parent=self
        )
        self._upload_worker.progress_updated.connect(self._on_task_progress)
        self._upload_worker.log_message.connect(self._worker_log)
        self._upload_worker.upload_completed.connect(self._on_upload_done)
        self._upload_worker.upload_failed.connect(self._on_upload_failed)
        self._upload_worker.start()

    def _on_task_progress(self, percent: int, message: str):
        self.progressBar.setVisible(True)
        self.progressBar.setValue(percent)
        self.progressLabel.setText(message)

    def _on_upload_done(self, message: str):
        self.progressBar.setVisible(False)
        self.progressLabel.setText("")
        self._set_busy(False)
        self._log("INFO", message)
        InfoBar.success(
            "上传完成",
            message,
            parent=self,
            position=InfoBarPosition.TOP,
            duration=4000,
        )
        self._on_refresh_list()

    def _on_upload_failed(self, error: str):
        self.progressBar.setVisible(False)
        self.progressLabel.setText("")
        self._set_busy(False)
        self._log("ERROR", f"上传失败：{error}")
        InfoBar.error(
            "上传失败",
            _user_error(error),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )

    def _on_restart_drm(self):
        if self._is_busy or not self._is_connected:
            return
        self._set_busy(True)
        self._restart_worker = HttpRestartDrmWorker(
            self._base_url, manager=self._remote_manager, parent=self
        )
        self._restart_worker.log_message.connect(self._worker_log)
        self._restart_worker.restart_succeeded.connect(self._on_restart_done)
        self._restart_worker.restart_failed.connect(self._on_restart_failed)
        self._restart_worker.start()

    def _on_restart_done(self):
        self._set_busy(False)
        InfoBar.success(
            "已请求重启",
            "已向设备发送 DrmApp 重启请求。",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=3000,
        )

    def _on_restart_failed(self, error: str):
        self._set_busy(False)
        InfoBar.error(
            "重启失败",
            _user_error(error),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )

    def _on_delete_for_asset(self, asset_data: dict):
        if self._is_busy or not self._is_connected:
            return
        uuid = asset_data.get("uuid", "")
        name = asset_data.get("name", uuid)
        reply = QMessageBox.question(
            self,
            "确认删除",
            f'删除远程素材“{name}”？\nUUID: {uuid}\n此操作不可撤销。',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True)
        self._delete_worker = HttpDeleteAssetWorker(
            self._base_url, manager=self._remote_manager, parent=self
        )
        self._delete_worker.setup(uuid, name, self._base_url)
        self._delete_worker.log_message.connect(self._worker_log)
        self._delete_worker.delete_completed.connect(self._on_delete_done)
        self._delete_worker.delete_failed.connect(self._on_delete_failed)
        self._delete_worker.start()

    def _on_delete_done(self, name: str):
        self._set_busy(False)
        InfoBar.success(
            "已删除",
            f"已删除 {name}",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=3000,
        )
        self._on_refresh_list()

    def _on_delete_failed(self, error: str):
        self._set_busy(False)
        InfoBar.error(
            "删除失败",
            _user_error(error),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )

    def _on_download_for_asset(self, asset_data: dict):
        if self._is_busy or not self._is_connected:
            return
        uuid = asset_data.get("uuid", "")
        if not uuid:
            InfoBar.warning(
                "无法下载",
                "所选素材没有 UUID。",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=4000,
            )
            return
        save_dir = QFileDialog.getExistingDirectory(self, "选择保存目录")
        if not save_dir:
            return
        self._download_asset(uuid, save_dir, self._on_download_done)

    def _on_edit_for_asset(self, asset_data: dict):
        if self._is_busy or not self._is_connected:
            return
        uuid = asset_data.get("uuid", "")
        if not uuid:
            return
        temp_dir = tempfile.mkdtemp(prefix="neo_asset_edit_")
        self._download_asset(uuid, temp_dir, self._on_edit_download_done)

    def _download_asset(self, uuid: str, save_dir: str, completed_slot):
        self._set_busy(True)
        self.progressBar.setVisible(True)
        self.progressBar.setValue(0)
        self._download_worker = HttpDownloadAssetWorker(
            self._base_url, manager=self._remote_manager, parent=self
        )
        self._download_worker.setup(uuid, save_dir, self._base_url)
        self._download_worker.log_message.connect(self._worker_log)
        self._download_worker.progress_updated.connect(self._on_task_progress)
        self._download_worker.download_completed.connect(completed_slot)
        self._download_worker.download_failed.connect(self._on_download_failed)
        self._download_worker.start()

    def _on_download_done(self, local_path: str):
        self.progressBar.setVisible(False)
        self.progressLabel.setText("")
        self._set_busy(False)
        InfoBar.success(
            "下载完成",
            f"已保存到 {local_path}",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )

    def _on_download_failed(self, error: str):
        self.progressBar.setVisible(False)
        self.progressLabel.setText("")
        self._set_busy(False)
        InfoBar.error(
            "下载失败",
            _user_error(error),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )

    def _on_edit_download_done(self, local_path: str):
        self._on_download_done(local_path)
        json_path = self._find_epconfig(local_path)
        if not json_path:
            InfoBar.warning(
                "打开失败",
                "下载的素材中没有 epconfig.json。",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=5000,
            )
            return
        main_window = self.window()
        if hasattr(main_window, "ReadProjectFromJson") and hasattr(
            main_window, "_on_sidebar_material"
        ):
            main_window.ReadProjectFromJson(json_path)
            main_window._on_sidebar_material()

    def _find_epconfig(self, root: str) -> str:
        for dirpath, _, filenames in os.walk(root):
            if "epconfig.json" in filenames:
                return os.path.join(dirpath, "epconfig.json")
        return ""

    def _on_device_stream(self):
        if not self._is_connected:
            return
        from gui.widgets.device_stream_widget import DeviceStreamWidget

        if self._stream_widget is None or not self._stream_widget.isVisible():
            try:
                self._remote_manager.probe_stream()
            except Exception as exc:
                InfoBar.warning(
                    "实时画面不可用",
                    _user_error(str(exc)),
                    parent=self,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                )
                return

        if self._stream_widget is None:
            self._stream_widget = DeviceStreamWidget(self.middlePanel)
            self.middlePanel.layout().addWidget(self._stream_widget)

        if self._stream_widget.isVisible():
            self._stop_stream()
            return

        host = urlsplit(self._base_url).hostname or DEFAULT_DEVICE_HOST
        self._stream_widget.set_device_host(host)
        self.assetDetailList.hide()
        self._stream_widget.show()
        self.middleTitleLabel.setText("实时画面")
        self.btnStream.setText("返回素材列表")

    def _stop_stream(self):
        if self._stream_widget is not None:
            self._stream_widget.shutdown()
            self._stream_widget.hide()
        if hasattr(self, "assetDetailList"):
            self.assetDetailList.show()
        if hasattr(self, "middleTitleLabel"):
            self.middleTitleLabel.setText("远程素材")
        if hasattr(self, "btnStream"):
            self.btnStream.setText("实时画面")

    def load_settings(self, settings: dict):
        self._settings = settings.copy()
        self._remote_manager.set_device_token(
            str(self._settings.get("remote_device_token") or "")
        )

    def shutdown(self):
        self._stop_stream()
        for worker in [
            self._connect_worker,
            self._list_worker,
            self._upload_worker,
            self._download_worker,
            self._delete_worker,
            self._restart_worker,
        ]:
            if worker and worker.isRunning():
                if hasattr(worker, "cancel"):
                    worker.cancel()
                worker.wait(3000)


def _user_error(error: str) -> str:
    return remote_user_error(error)
