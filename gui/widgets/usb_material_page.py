'''USB素材管理页'''
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
    UsbListOperatorsWorker,
    UsbReloadAssetsWorker,
    UsbUploadAssetWorker,
)


class UsbAssetListItemWidget(QWidget):
    """USB remote asset list item with thumbnail and action buttons."""

    def __init__(self, asset_data: dict, parent_page: UsbMaterialPage = None):
        super().__init__()
        self.asset_data = asset_data
        self.parent_page = parent_page

        # Thumbnail — try to load the downloaded icon, fall back to placeholder
        self.thumbnail_label = QLabel()
        self.thumbnail_label.setFixedSize(64, 64)
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_label.setScaledContents(True)
        local_icon = asset_data.get("local_icon", "")
        if local_icon and os.path.isfile(local_icon):
            pixmap = QPixmap(local_icon)
            if not pixmap.isNull():
                self.thumbnail_label.setPixmap(
                    pixmap.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
                )
            else:
                self.thumbnail_label.setText("缺省素材")
        else:
            self.thumbnail_label.setText("缺省素材")
        self.thumbnail_label.setStyleSheet(
            "border: 1px solid #ccc; border-radius: 4px; color: #777;"
        )

        self.name_label = CaptionLabel(asset_data.get("name", "Unnamed"))
        uuid = asset_data.get("uuid", "")
        desc = asset_data.get("description", "")
        path = asset_data.get("path", "")
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
            lambda: self.parent_page._on_delete_for_asset(self.asset_data)
        )
        self.btn_download.clicked.connect(
            lambda: self.parent_page._on_download_for_asset(self.asset_data)
        )

    def set_buttons_enabled(self, enabled: bool):
        self.btn_delete.setEnabled(enabled)
        self.btn_download.setEnabled(enabled)


class UsbMaterialPage(QWidget):
    """素材管理页 — 包含操作按钮和远程素材列表"""

    def __init__(self, controller: UsbControlPage, parent=None):
        super().__init__(parent)
        self.controller = controller

        # Worker references
        self._list_worker = None
        self._upload_worker = None
        self._download_worker = None
        self._delete_worker = None
        self._reload_worker = None

        # Temp directory for operator preview icons
        self._temp_dir = tempfile.mkdtemp(prefix="usb_op_icons_")

        # Full asset list cache for client-side filtering
        self._full_asset_list: list = []

        self._init_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _init_ui(self):
        innerSplitter = QSplitter(Qt.Orientation.Horizontal, self)
        innerSplitter.setContentsMargins(0, 0, 0, 0)

        # 左子面板：操作按钮
        actionPanel = SimpleCardWidget()
        actionPanel.setMinimumWidth(250)
        actionLayout = QVBoxLayout(actionPanel)
        actionLayout.setContentsMargins(15, 15, 15, 15)
        actionLayout.setSpacing(10)
        actionLayout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.btnRefreshList = PushButton("刷新素材")
        self.btnRefreshList.setIcon(FluentIcon.SYNC)
        actionLayout.addWidget(self.btnRefreshList)

        self.btnUploadLocal = PushButton("上传素材")
        self.btnUploadLocal.setIcon(FluentIcon.SEND)
        actionLayout.addWidget(self.btnUploadLocal)

        self.btnForceReload = PushButton("强制DRM重载")
        self.btnForceReload.setIcon(FluentIcon.SYNC)
        actionLayout.addWidget(self.btnForceReload)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        actionLayout.addWidget(line)

        self.networkHintLabel = CaptionLabel(
            "若要使用此功能，请确保：\n1、菜单 -> 设备 -> 版本号 a2.7及以上\n2、菜单 -> 设置 -> USB模式：管理器APP"
        )
        self.networkHintLabel.setWordWrap(True)
        actionLayout.addWidget(self.networkHintLabel)

        # 右子面板：素材列表
        assetPanel = SimpleCardWidget()
        assetLayout = QVBoxLayout(assetPanel)
        assetLayout.setContentsMargins(10, 10, 10, 10)
        assetLayout.setSpacing(8)

        self.middleTitleLabel = CaptionLabel("远程素材")
        assetLayout.addWidget(self.middleTitleLabel)

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
        assetLayout.addLayout(filterLayout)

        self.assetDetailList = ListWidget()
        self.assetDetailList.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        setCustomStyleSheet(
            self.assetDetailList,
            "ListWidget { border: none; background: transparent; }",
            "ListWidget { border: none; background: transparent; }",
        )
        assetLayout.addWidget(self.assetDetailList, stretch=1)

        innerSplitter.addWidget(actionPanel)
        innerSplitter.addWidget(assetPanel)
        innerSplitter.setSizes([220, 420])
        innerSplitter.setStretchFactor(1, 3)

        pageLayout = QVBoxLayout(self)
        pageLayout.setContentsMargins(0, 0, 0, 0)
        pageLayout.addWidget(innerSplitter)

    def _connect_signals(self):
        """连接内部按钮信号"""
        self.btnRefreshList.clicked.connect(self._on_refresh_list)
        self.btnUploadLocal.clicked.connect(self._on_upload_local)
        self.btnForceReload.clicked.connect(self._on_force_reload)
        self._filter_group.idClicked.connect(self._on_filter_changed)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh_asset_list(self):
        """公开的刷新素材列表入口"""
        self._on_refresh_list()

    def clear_asset_list(self):
        """清空素材列表"""
        self._full_asset_list = []
        self.assetDetailList.clear()
        self.middleTitleLabel.setText("远程素材")

    def set_buttons_enabled(self, enabled: bool):
        """批量设置按钮启用状态"""
        self.btnRefreshList.setEnabled(enabled)
        self.btnUploadLocal.setEnabled(enabled)
        self.btnForceReload.setEnabled(enabled)
        for i in range(self.assetDetailList.count()):
            item = self.assetDetailList.item(i)
            widget = self.assetDetailList.itemWidget(item)
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
        """等待所有后台工作线程结束并清理临时目录"""
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
    # Refresh asset list
    # ------------------------------------------------------------------

    def _on_refresh_list(self):
        '''刷新列表 — 通过 epassctl 获取干员信息及预览图标'''
        ctrl = self.controller
        if ctrl._is_busy or not ctrl._is_connected:
            return
        ctrl.set_busy(True)

        self._list_worker = UsbListOperatorsWorker(
            ctrl.usbRC, self._temp_dir, parent=self
        )
        self._list_worker.progress_updated.connect(self._on_task_progress)
        self._list_worker.list_completed.connect(self._on_list_loaded)
        self._list_worker.list_failed.connect(self._on_list_failed)
        self._track_worker(self._list_worker)
        self._list_worker.start()

    def _on_list_loaded(self, operators: list):
        """素材列表加载完成 — 缓存全量并应用当前筛选"""
        self._full_asset_list = operators
        self._render_filtered_list()
        self.controller.set_busy(False)

    # ------------------------------------------------------------------
    # Client-side filter
    # ------------------------------------------------------------------

    def _on_filter_changed(self, _id: int):
        """筛选切换 — 从缓存列表客户端过滤，无需重新请求"""
        if self.controller._is_busy:
            return
        self._render_filtered_list()

    def _filter_assets(self) -> list:
        """按当前选中的筛选项返回素材列表。"""
        filter_id = self._filter_group.checkedId()
        if filter_id == 1:
            # 系统盘 only
            return [a for a in self._full_asset_list
                    if (a.get("path") or "").startswith("/assets/")]
        elif filter_id == 2:
            # 数据盘 only
            return [a for a in self._full_asset_list
                    if (a.get("path") or "").startswith("/sd/")]
        # 全部 (0 or fallback)
        return self._full_asset_list

    def _render_filtered_list(self):
        """按当前筛选条件渲染素材列表。"""
        assets = self._filter_assets()
        self.assetDetailList.clear()
        if not self._full_asset_list:
            self.middleTitleLabel.setText("远程素材")
            placeholder = QListWidgetItem("设备端暂未返回干员信息。")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.assetDetailList.addItem(placeholder)
        elif not assets:
            self.middleTitleLabel.setText("远程素材（0 条匹配）")
            placeholder = QListWidgetItem("当前筛选项下无匹配素材。")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.assetDetailList.addItem(placeholder)
        else:
            self.middleTitleLabel.setText(
                f"远程素材（总计：{len(self._full_asset_list)}，显示：{len(assets)}）"
            )
            for op in assets:
                widget = UsbAssetListItemWidget(op, parent_page=self)
                list_item = QListWidgetItem(self.assetDetailList)
                list_item.setSizeHint(widget.sizeHint())
                self.assetDetailList.addItem(list_item)
                self.assetDetailList.setItemWidget(list_item, widget)

    def _on_list_failed(self, error):
        """素材列表加载失败"""
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
        '''本地上传 — 读取 epconfig.json 获取 uuid，询问磁盘目标后上传'''
        ctrl = self.controller
        if ctrl._is_busy or not ctrl._is_connected:
            return
        path = QFileDialog.getExistingDirectory(self, "选择素材目录", "")
        if not path:
            return

        epconfig_path = os.path.join(path, "epconfig.json")
        try:
            with open(epconfig_path, "r", encoding="utf-8") as f:
                epconfig = json.load(f)
        except Exception as ex:
            InfoBar.error(
                "上传失败",
                f"无法读取 epconfig.json：{ex}",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=5000,
            )
            return

        uuid = (epconfig.get("uuid") or "").strip()
        if not uuid:
            InfoBar.error(
                "上传失败",
                "epconfig.json 中缺少 uuid 字段或为空",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=5000,
            )
            return

        name = epconfig.get("name", uuid)

        # Ask user which disk to upload to
        msg = QMessageBox(self)
        msg.setWindowTitle("选择目标磁盘")
        msg.setText(f"将素材「{name}」上传到：")
        msg.addButton("系统盘  (/assets)", QMessageBox.ButtonRole.AcceptRole)
        btn_data = msg.addButton("数据盘  (/sd/assets)", QMessageBox.ButtonRole.ApplyRole)
        btn_cancel = msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked == btn_cancel or clicked is None:
            return
        base = "/sd/assets" if clicked == btn_data else "/assets"

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
        """上传完成 → reload_assets → 刷新列表"""
        ctrl = self.controller
        ctrl.progressBar.setVisible(False)
        ctrl.progressLabel.setText("正在重载资产...")
        InfoBar.success(
            "上传完成",
            f"已上传至 {remote_path}",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=4000,
        )
        self._reload_assets_and_refresh()

    def _on_upload_failed(self, error):
        """上传失败"""
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
        """更新进度条"""
        ctrl = self.controller
        ctrl.progressBar.setVisible(True)
        ctrl.progressBar.setValue(percent)
        ctrl.progressLabel.setText(message)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def _on_delete_for_asset(self, asset_data: dict):
        """删除远程素材"""
        ctrl = self.controller
        if ctrl._is_busy or not ctrl._is_connected:
            return
        name = asset_data.get("name", "")
        path = asset_data.get("path")
        reply = QMessageBox.question(
            self,
            "确认删除",
            f'删除远程素材"{name}"？\n路径: {path}\n此操作不可撤销。',
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
        """删除完成 → reload_assets → 刷新列表"""
        self.controller.progressLabel.setText("正在重载资产...")
        InfoBar.success(
            "已删除",
            f"已删除 {path}",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=3000,
        )
        self._reload_assets_and_refresh()

    def _on_delete_failed(self, error):
        """删除失败"""
        self.controller.set_busy(False)
        InfoBar.error(
            "删除失败",
            str(error),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )

    # ------------------------------------------------------------------
    # Reload assets (shared by upload & delete)
    # ------------------------------------------------------------------

    def _reload_assets_and_refresh(self):
        """执行 reload_assets，完成后刷新列表。保持 busy 状态直到刷新结束。"""
        self._reload_worker = UsbReloadAssetsWorker(
            self.controller.usbRC, parent=self
        )
        self._reload_worker.reload_succeeded.connect(self._on_reload_done)
        self._reload_worker.reload_failed.connect(self._on_reload_failed)
        self._track_worker(self._reload_worker)
        self._reload_worker.start()

    def _on_reload_done(self):
        """reload 成功，释放 busy 并刷新列表"""
        self.controller.set_busy(False)
        self._on_refresh_list()

    def _on_reload_failed(self, error):
        """reload 失败，仍然刷新列表（设备可能不支持 reload）"""
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

    def _on_download_for_asset(self, asset_data: dict):
        """下载远程素材"""
        ctrl = self.controller
        if ctrl._is_busy or not ctrl._is_connected:
            return
        name = asset_data.get("name", "")
        uuid = asset_data.get("uuid", "")
        path = asset_data.get("path", "")
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
        """下载完成"""
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
        """下载失败"""
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

    # ------------------------------------------------------------------
    # 强制刷新远端素材
    # ------------------------------------------------------------------

    def _on_force_reload(self):
        '''强制DRM重载 — 手动触发 epassctl prts reload_assets，不刷新本地列表'''
        ctrl = self.controller
        if ctrl._is_busy or not ctrl._is_connected:
            return
        ctrl.set_busy(True)
        ctrl.progressLabel.setText("正在强制重载DRM资产...")

        self._reload_worker = UsbReloadAssetsWorker(ctrl.usbRC, parent=self)
        self._reload_worker.reload_succeeded.connect(
            self._on_force_reload_done)
        self._reload_worker.reload_failed.connect(self._on_force_reload_failed)
        self._track_worker(self._reload_worker)
        self._reload_worker.start()

    def _on_force_reload_done(self):
        """强制重载完成"""
        self.controller.set_busy(False)
        self.controller.progressLabel.setText("")
        InfoBar.success(
            "强制重载完成",
            "已向设备发送 reload_assets 命令。",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=3000,
        )

    def _on_force_reload_failed(self, error):
        """强制重载失败"""
        self.controller.set_busy(False)
        self.controller.progressLabel.setText("")
        InfoBar.error(
            "强制重载失败",
            str(error),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )
