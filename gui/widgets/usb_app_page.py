'''USB应用管理页'''
from __future__ import annotations
import json
import os
import tempfile
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QMessageBox,
    QRadioButton,
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
    PushButton,
    SimpleCardWidget,
    setCustomStyleSheet,
)

if TYPE_CHECKING:
    from gui.widgets.usb_control_page import UsbControlPage

from gui.workers.usb_workers import (
    UsbDeleteAssetWorker,
    UsbDownloadAssetWorker,
    UsbListAppsWorker,
    UsbReloadAssetsWorker,
    UsbUploadAssetWorker,
)


class UsbAppListItemWidget(QWidget):
    """USB remote app list item with thumbnail and action buttons."""

    def __init__(self, app_data: dict, parent_page: UsbAppPage = None):
        super().__init__()
        self.app_data = app_data
        self.parent_page = parent_page

        # Thumbnail — try to load the downloaded icon, fall back to placeholder
        self.thumbnail_label = QLabel()
        self.thumbnail_label.setFixedSize(64, 64)
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_label.setScaledContents(True)
        local_icon = app_data.get("local_icon", "")
        if local_icon and os.path.isfile(local_icon):
            pixmap = QPixmap(local_icon)
            if not pixmap.isNull():
                self.thumbnail_label.setPixmap(
                    pixmap.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
                )
            else:
                self.thumbnail_label.setText("缺省图标")
        else:
            self.thumbnail_label.setText("缺省图标")
        self.thumbnail_label.setStyleSheet(
            "border: 1px solid #ccc; border-radius: 4px; color: #777;"
        )

        self.name_label = CaptionLabel(app_data.get("name", "Unnamed"))
        uuid = app_data.get("uuid", "")
        desc = app_data.get("description", "")
        path = app_data.get("path", "")
        info_text = f"UUID: {uuid}" if uuid else ""
        if desc:
            info_text += f"\n{desc}" if info_text else desc
        if path:
            info_text += f"\nPath: {path}" if info_text else path
        self.info_label = CaptionLabel(info_text)
        self.info_label.setWordWrap(True)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(3)
        text_layout.addWidget(self.name_label)
        text_layout.addWidget(self.info_label)

        self.btn_delete = PushButton("删除")
        self.btn_delete.setIcon(FluentIcon.DELETE)
        self.btn_download = PushButton("下载")
        self.btn_download.setIcon(FluentIcon.DOWNLOAD)

        action_layout = QVBoxLayout()
        action_layout.setSpacing(4)
        action_layout.addWidget(self.btn_delete)
        action_layout.addWidget(self.btn_download)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)
        layout.addWidget(self.thumbnail_label,
                         alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(text_layout, stretch=1)
        layout.addLayout(action_layout)

        self.btn_delete.clicked.connect(
            lambda: self.parent_page._on_delete_for_app(self.app_data)
        )
        self.btn_download.clicked.connect(
            lambda: self.parent_page._on_download_for_app(self.app_data)
        )

    def set_buttons_enabled(self, enabled: bool):
        self.btn_delete.setEnabled(enabled)
        self.btn_download.setEnabled(enabled)


class UsbAppPage(QWidget):
    """应用管理页 — 包含操作按钮和远程应用列表"""

    def __init__(self, controller: UsbControlPage, parent=None):
        super().__init__(parent)
        self.controller = controller

        # Worker references
        self._list_worker = None
        self._upload_worker = None
        self._download_worker = None
        self._delete_worker = None
        self._reload_worker = None

        # Temp directory for app preview icons
        self._temp_dir = tempfile.mkdtemp(prefix="usb_app_icons_")

        # Full app list cache for client-side filtering
        self._full_app_list: list = []

        self._init_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _init_ui(self):
        innerSplitter = QSplitter(Qt.Orientation.Horizontal, self)
        innerSplitter.setContentsMargins(0, 0, 0, 0)

        # Left panel: action buttons
        actionPanel = SimpleCardWidget()
        actionPanel.setMinimumWidth(250)
        actionLayout = QVBoxLayout(actionPanel)
        actionLayout.setContentsMargins(15, 15, 15, 15)
        actionLayout.setSpacing(10)
        actionLayout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.btnRefreshList = PushButton("刷新应用")
        self.btnRefreshList.setIcon(FluentIcon.SYNC)
        actionLayout.addWidget(self.btnRefreshList)

        self.btnUploadLocal = PushButton("上传应用")
        self.btnUploadLocal.setIcon(FluentIcon.SEND)
        actionLayout.addWidget(self.btnUploadLocal)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        actionLayout.addWidget(line)

        self.networkHintLabel = CaptionLabel(
            "若要使用此功能，请确保：\n1、菜单 -> 设备 -> 版本号 a2.7及以上\n2、菜单 -> 设置 -> USB模式：管理器APP"
        )
        self.networkHintLabel.setWordWrap(True)
        actionLayout.addWidget(self.networkHintLabel)

        # Right panel: app list
        appPanel = SimpleCardWidget()
        appLayout = QVBoxLayout(appPanel)
        appLayout.setContentsMargins(10, 10, 10, 10)
        appLayout.setSpacing(8)

        self.middleTitleLabel = CaptionLabel("远程应用")
        appLayout.addWidget(self.middleTitleLabel)

        # Filter radio buttons
        filterLayout = QHBoxLayout()
        filterLayout.setContentsMargins(0, 0, 0, 0)
        filterLayout.setSpacing(12)

        self._filter_group = QButtonGroup(self)
        self._radio_all = QRadioButton("全部")
        self._radio_sys = QRadioButton("系统盘")
        self._radio_data = QRadioButton("数据盘")
        self._radio_all.setChecked(True)

        self._filter_group.addButton(self._radio_all, 0)
        self._filter_group.addButton(self._radio_sys, 1)
        self._filter_group.addButton(self._radio_data, 2)

        filterLayout.addWidget(self._radio_all)
        filterLayout.addWidget(self._radio_sys)
        filterLayout.addWidget(self._radio_data)
        filterLayout.addStretch()
        appLayout.addLayout(filterLayout)

        self.appDetailList = ListWidget()
        self.appDetailList.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        setCustomStyleSheet(
            self.appDetailList,
            "ListWidget { border: none; background: transparent; }",
            "ListWidget { border: none; background: transparent; }",
        )
        appLayout.addWidget(self.appDetailList, stretch=1)

        innerSplitter.addWidget(actionPanel)
        innerSplitter.addWidget(appPanel)
        innerSplitter.setSizes([220, 420])
        innerSplitter.setStretchFactor(1, 3)

        pageLayout = QVBoxLayout(self)
        pageLayout.setContentsMargins(0, 0, 0, 0)
        pageLayout.addWidget(innerSplitter)

    def _connect_signals(self):
        """Connect internal button signals"""
        self.btnRefreshList.clicked.connect(self._on_refresh_list)
        self.btnUploadLocal.clicked.connect(self._on_upload_local)
        self._filter_group.idClicked.connect(self._on_filter_changed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh_app_list(self):
        """Public entry point to refresh the app list"""
        self._on_refresh_list()

    def clear_app_list(self):
        """Clear the app list"""
        self._full_app_list = []
        self.appDetailList.clear()
        self.middleTitleLabel.setText("远程应用")

    def set_buttons_enabled(self, enabled: bool):
        """Batch set button enabled state"""
        self.btnRefreshList.setEnabled(enabled)
        self.btnUploadLocal.setEnabled(enabled)
        for i in range(self.appDetailList.count()):
            item = self.appDetailList.item(i)
            widget = self.appDetailList.itemWidget(item)
            if widget:
                widget.set_buttons_enabled(enabled)

    def _track_worker(self, worker):
        """Dispose a finished background worker so parented QThreads don't pile up.

        Qt only frees child QObjects when the parent (this page) is destroyed, so
        each finished per-operation worker would otherwise leak for the page's
        lifetime. Connect QThread.finished -> deleteLater and drop our reference.
        """
        def _cleanup():
            for attr in (
                "_list_worker", "_upload_worker", "_download_worker",
                "_delete_worker", "_reload_worker",
            ):
                if getattr(self, attr, None) is worker:
                    setattr(self, attr, None)
            worker.deleteLater()

        worker.finished.connect(_cleanup)
        return worker

    def shutdown(self):
        """Wait for all background workers and clean up temp directory"""
        for worker in [
            self._list_worker,
            self._upload_worker,
            self._download_worker,
            self._delete_worker,
            self._reload_worker,
        ]:
            if worker and worker.isRunning():
                worker.wait(3000)
        if self._temp_dir and os.path.isdir(self._temp_dir):
            try:
                import shutil
                shutil.rmtree(self._temp_dir)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Refresh app list
    # ------------------------------------------------------------------

    def _on_refresh_list(self):
        """Refresh list — get app info and preview icons from /app/ via USB"""
        ctrl = self.controller
        if ctrl._is_busy or not ctrl._is_connected:
            return
        ctrl.set_busy(True)

        self._list_worker = UsbListAppsWorker(
            ctrl.usbRC, self._temp_dir, parent=self
        )
        self._list_worker.progress_updated.connect(self._on_task_progress)
        self._list_worker.list_completed.connect(self._on_list_loaded)
        self._list_worker.list_failed.connect(self._on_list_failed)
        self._track_worker(self._list_worker)
        self._list_worker.start()

    def _on_list_loaded(self, apps: list):
        """App list loaded — cache full list and render with current filter"""
        self._full_app_list = apps
        self._render_filtered_list()
        self.controller.set_busy(False)

    # ------------------------------------------------------------------
    # Client-side filter
    # ------------------------------------------------------------------

    def _on_filter_changed(self, _id: int):
        """Filter radio changed — re-render from cached list without re-fetching"""
        if self.controller._is_busy:
            return
        self._render_filtered_list()

    def _filter_apps(self) -> list:
        """Return apps matching the currently selected filter."""
        filter_id = self._filter_group.checkedId()
        if filter_id == 1:
            # 系统盘 only
            return [a for a in self._full_app_list
                    if (a.get("path") or "").startswith("/app/")]
        elif filter_id == 2:
            # 数据盘 only
            return [a for a in self._full_app_list
                    if (a.get("path") or "").startswith("/sd/")]
        # 全部 (0 or fallback)
        return self._full_app_list

    def _render_filtered_list(self):
        """Render the filtered app list into the ListWidget."""
        apps = self._filter_apps()
        self.appDetailList.clear()
        if not self._full_app_list:
            self.middleTitleLabel.setText("远程应用")
            placeholder = QListWidgetItem("设备端暂未返回应用信息。")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.appDetailList.addItem(placeholder)
        elif not apps:
            self.middleTitleLabel.setText("远程应用（0 条匹配）")
            placeholder = QListWidgetItem("当前筛选项下无匹配应用。")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.appDetailList.addItem(placeholder)
        else:
            self.middleTitleLabel.setText(
                f"远程应用（总计：{len(self._full_app_list)}，显示：{len(apps)}）"
            )
            for app in apps:
                widget = UsbAppListItemWidget(app, parent_page=self)
                list_item = QListWidgetItem(self.appDetailList)
                list_item.setSizeHint(widget.sizeHint())
                self.appDetailList.addItem(list_item)
                self.appDetailList.setItemWidget(list_item, widget)

    def _on_list_failed(self, error):
        """App list load failed"""
        self.controller.set_busy(False)
        InfoBar.error(
            "刷新失败",
            str(error),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def _on_upload_local(self):
        """Local upload — read appconfig.json for uuid, ask disk target, upload."""
        ctrl = self.controller
        if ctrl._is_busy or not ctrl._is_connected:
            return
        path = QFileDialog.getExistingDirectory(self, "选择应用目录", "")
        if not path:
            return

        epconfig_path = os.path.join(path, "appconfig.json")
        try:
            with open(epconfig_path, "r", encoding="utf-8") as f:
                epconfig = json.load(f)
        except Exception as ex:
            InfoBar.error(
                "上传失败",
                f"无法读取 appconfig.json：{ex}",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=5000,
            )
            return

        uuid = (epconfig.get("uuid") or "").strip()
        if not uuid:
            InfoBar.error(
                "上传失败",
                "appconfig.json 中缺少 uuid 字段或为空",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=5000,
            )
            return

        name = epconfig.get("name", uuid)

        # Ask user which disk to upload to
        msg = QMessageBox(self)
        msg.setWindowTitle("选择目标磁盘")
        msg.setText(f"将应用「{name}」上传到：")
        msg.addButton("系统盘  (/app)", QMessageBox.ButtonRole.AcceptRole)
        btn_data = msg.addButton("数据盘  (/sd/app)", QMessageBox.ButtonRole.ApplyRole)
        btn_cancel = msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked == btn_cancel or clicked is None:
            return
        base = "/sd/app" if clicked == btn_data else "/app"

        ctrl.set_busy(True)
        ctrl.progressBar.setVisible(True)
        ctrl.progressBar.setValue(0)
        ctrl.progressLabel.setText("正在上传...")

        remote_path = f"{base}/{uuid}"
        self._upload_worker = UsbUploadAssetWorker(
            ctrl.usbRC, path, remote_path, parent=self
        )
        self._upload_worker.progress_updated.connect(self._on_task_progress)
        self._upload_worker.upload_completed.connect(self._on_upload_done)
        self._upload_worker.upload_failed.connect(self._on_upload_failed)
        self._track_worker(self._upload_worker)
        self._upload_worker.start()

    def _on_upload_done(self, remote_path: str):
        """Upload complete → reload → refresh list"""
        ctrl = self.controller
        ctrl.progressBar.setVisible(False)
        InfoBar.success(
            "上传完成",
            f"已上传至 {remote_path}",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=4000,
        )
        self._reload_apps_and_refresh()

    def _on_upload_failed(self, error):
        """Upload failed"""
        ctrl = self.controller
        ctrl.progressBar.setVisible(False)
        ctrl.progressLabel.setText("")
        ctrl.set_busy(False)
        InfoBar.error(
            "上传失败",
            str(error),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )

    def _on_task_progress(self, percent: int, message: str):
        """Update progress bar"""
        ctrl = self.controller
        ctrl.progressBar.setVisible(True)
        ctrl.progressBar.setValue(percent)
        ctrl.progressLabel.setText(message)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def _on_delete_for_app(self, app_data: dict):
        """Delete remote app"""
        ctrl = self.controller
        if ctrl._is_busy or not ctrl._is_connected:
            return
        name = app_data.get("name", "")
        path = app_data.get("path")
        reply = QMessageBox.question(
            self,
            "确认删除",
            f'删除远程应用"{name}"？\n路径: {path}\n此操作不可撤销。',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        ctrl.set_busy(True)
        self._delete_worker = UsbDeleteAssetWorker(
            ctrl.usbRC, path, parent=self
        )
        self._delete_worker.delete_completed.connect(self._on_delete_done)
        self._delete_worker.delete_failed.connect(self._on_delete_failed)
        self._track_worker(self._delete_worker)
        self._delete_worker.start()

    def _on_delete_done(self, path: str):
        """Delete complete → reload → refresh list"""
        self.controller.progressLabel.setText("正在重载应用...")
        InfoBar.success(
            "已删除",
            f"已删除 {path}",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=3000,
        )
        self._reload_apps_and_refresh()

    def _on_delete_failed(self, error):
        """Delete failed"""
        self.controller.set_busy(False)
        InfoBar.error(
            "删除失败",
            str(error),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )

    # ------------------------------------------------------------------
    # Reload apps (shared by upload & delete)
    # ------------------------------------------------------------------

    def _reload_apps_and_refresh(self):
        """Execute reload, refresh list on completion. Keep busy until done."""
        self._reload_worker = UsbReloadAssetsWorker(
            self.controller.usbRC, parent=self
        )
        self._reload_worker.reload_succeeded.connect(self._on_reload_done)
        self._reload_worker.reload_failed.connect(self._on_reload_failed)
        self._track_worker(self._reload_worker)
        self._reload_worker.start()

    def _on_reload_done(self):
        """Reload succeeded, release busy and refresh list"""
        self.controller.set_busy(False)
        self._on_refresh_list()

    def _on_reload_failed(self, error):
        """Reload failed, still refresh list (device may not support reload)"""
        self.controller.set_busy(False)
        InfoBar.warning(
            "重载失败",
            str(error),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )
        self._on_refresh_list()

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def _on_download_for_app(self, app_data: dict):
        """Download remote app"""
        ctrl = self.controller
        if ctrl._is_busy or not ctrl._is_connected:
            return
        name = app_data.get("name", "")
        uuid = app_data.get("uuid", "")
        path = app_data.get("path", "")
        if not path or not uuid:
            return
        save_dir = QFileDialog.getExistingDirectory(self, "选择保存目录")
        if not save_dir:
            return
        local_path = os.path.join(save_dir, uuid)
        ctrl.set_busy(True)
        ctrl.progressBar.setVisible(True)
        ctrl.progressBar.setValue(0)
        ctrl.progressLabel.setText(f"正在下载: {name}")
        self._download_worker = UsbDownloadAssetWorker(
            ctrl.usbRC, path, local_path, parent=self
        )
        self._download_worker.progress_updated.connect(self._on_task_progress)
        self._download_worker.download_completed.connect(
            self._on_download_done)
        self._download_worker.download_failed.connect(self._on_download_failed)
        self._track_worker(self._download_worker)
        self._download_worker.start()

    def _on_download_done(self, local_path: str):
        """Download complete"""
        ctrl = self.controller
        ctrl.progressBar.setVisible(False)
        ctrl.progressLabel.setText("")
        ctrl.set_busy(False)
        InfoBar.success(
            "下载完成",
            f"已保存到 {local_path}",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )

    def _on_download_failed(self, error):
        """Download failed"""
        ctrl = self.controller
        ctrl.progressBar.setVisible(False)
        ctrl.progressLabel.setText("")
        ctrl.set_busy(False)
        InfoBar.error(
            "下载失败",
            str(error),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )
