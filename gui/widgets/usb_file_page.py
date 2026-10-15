'''USB文件管理页'''
from __future__ import annotations
import os
from typing import TYPE_CHECKING

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
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
    PushButton,
    ToolButton,
    SimpleCardWidget,
    StrongBodyLabel,
    setCustomStyleSheet,
)

if TYPE_CHECKING:
    from gui.widgets.usb_control_page import UsbControlPage

from gui.workers.usb_workers import (
    UsbCopyWorker,
    UsbDeleteAssetWorker,
    UsbDownloadAssetWorker,
    UsbListAssetsWorker,
    UsbMkdirWorker,
    UsbMoveWorker,
    UsbStatWorker,
    UsbUploadAssetWorker,
)


class UsbFileListItemWidget(QWidget):
    """USB remote file list item — file or directory."""

    def __init__(self, name: str, is_dir: bool, parent_page: UsbFilePage = None):
        super().__init__()
        self.name = name
        self.is_dir = is_dir
        self.parent_page = parent_page

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(32, 32)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = FluentIcon.FOLDER if is_dir else FluentIcon.DOCUMENT
        self.icon_label.setPixmap(
            icon.icon(icon.path()).pixmap(24, 24)
        )

        self.name_label = CaptionLabel(name)
        type_text = "目录" if is_dir else "文件"
        self.type_label = CaptionLabel(f"[{type_text}]")
        self.type_label.setStyleSheet("color: #888;")

        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        text_layout.addWidget(self.name_label)
        text_layout.addWidget(self.type_label)

        self.checkbox = QCheckBox()
        self.checkbox.setToolTip("勾选以执行下载/删除操作" if is_dir else "勾选以执行下载/删除操作")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(10)
        layout.addWidget(
            self.checkbox, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(
            self.icon_label, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(text_layout, stretch=1)


class UsbFilePage(QWidget):
    """文件管理页 — 浏览远程文件系统，上传/下载/删除文件"""

    def __init__(self, controller: UsbControlPage, parent=None):
        super().__init__(parent)
        self.controller = controller

        # Current remote path for navigation
        self._current_path = "/"

        # Worker references
        self._list_worker = None
        self._upload_worker = None
        self._download_worker = None
        self._delete_worker = None
        self._copy_worker = None
        self._move_worker = None
        self._stat_worker = None
        self._mkdir_worker = None

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

        self.btnRefresh = PushButton("刷新")
        self.btnRefresh.setIcon(FluentIcon.SYNC)
        actionLayout.addWidget(self.btnRefresh)

        self.btnUploadFile = PushButton("上传文件")
        self.btnUploadFile.setIcon(FluentIcon.SEND)
        actionLayout.addWidget(self.btnUploadFile)

        self.btnUploadFolder = PushButton("上传文件夹")
        self.btnUploadFolder.setIcon(FluentIcon.SEND_FILL)
        actionLayout.addWidget(self.btnUploadFolder)

        self.btnMkdir = PushButton("新建文件夹")
        self.btnMkdir.setIcon(FluentIcon.FOLDER_ADD)
        actionLayout.addWidget(self.btnMkdir)

        self.btnDownload = PushButton("下载")
        self.btnDownload.setIcon(FluentIcon.DOWNLOAD)
        actionLayout.addWidget(self.btnDownload)

        self.btnDelete = PushButton("删除")
        self.btnDelete.setIcon(FluentIcon.DELETE)
        actionLayout.addWidget(self.btnDelete)

        self.btnCopy = PushButton("复制")
        self.btnCopy.setIcon(FluentIcon.COPY)
        actionLayout.addWidget(self.btnCopy)

        self.btnMove = PushButton("移动")
        self.btnMove.setIcon(FluentIcon.SEND)
        actionLayout.addWidget(self.btnMove)

        self.btnStat = PushButton("属性")
        self.btnStat.setIcon(FluentIcon.INFO)
        actionLayout.addWidget(self.btnStat)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        actionLayout.addWidget(line)

        self.hintLabel = CaptionLabel(
            "选择文件/文件夹后可使用下载或删除。\n双击目录进入，使用导航栏返回上级。"
        )
        self.hintLabel.setWordWrap(True)
        actionLayout.addWidget(self.hintLabel)

        # Right panel: path bar + file list
        filePanel = SimpleCardWidget()
        fileLayout = QVBoxLayout(filePanel)
        fileLayout.setContentsMargins(10, 10, 10, 10)
        fileLayout.setSpacing(8)

        # Path navigation bar
        pathBar = QHBoxLayout()
        pathBar.setSpacing(8)

        self.btnGoUp = PushButton("向上一级")
        self.btnGoUp.setIcon(FluentIcon.UP)
        self.btnGoUp.setEnabled(False)  # disabled at root
        pathBar.addWidget(self.btnGoUp)

        self.btnInvertSel = PushButton("反选")
        self.btnInvertSel.setIcon(FluentIcon.CHECKBOX)
        pathBar.addWidget(self.btnInvertSel)

        self.pathLabel = StrongBodyLabel("/")
        pathBar.addWidget(self.pathLabel, stretch=1)

        self.btnCopyPath = ToolButton(FluentIcon.COPY)
        self.btnCopyPath.setCheckable(True)
        self.btnCopyPath.setToolTip("复制当前路径")
        pathBar.addWidget(self.btnCopyPath)

        fileLayout.addLayout(pathBar)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        fileLayout.addWidget(sep)

        # File list
        self.fileList = ListWidget()
        self.fileList.setAcceptDrops(True)
        self.fileList.installEventFilter(self)
        self.fileList.setSelectionMode(
            ListWidget.SelectionMode.SingleSelection
        )
        self.fileList.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        setCustomStyleSheet(
            self.fileList,
            "ListWidget { border: none; background: transparent; }",
            "ListWidget { border: none; background: transparent; }",
        )
        fileLayout.addWidget(self.fileList, stretch=1)

        innerSplitter.addWidget(actionPanel)
        innerSplitter.addWidget(filePanel)
        innerSplitter.setSizes([220, 420])
        innerSplitter.setStretchFactor(1, 3)

        pageLayout = QVBoxLayout(self)
        pageLayout.setContentsMargins(0, 0, 0, 0)
        pageLayout.addWidget(innerSplitter)

    def _connect_signals(self):
        """Connect internal button signals"""
        self.btnRefresh.clicked.connect(self._list_current_path)
        self.btnUploadFile.clicked.connect(self._on_upload_file)
        self.btnUploadFolder.clicked.connect(self._on_upload_folder)
        self.btnDownload.clicked.connect(self._on_download)
        self.btnDelete.clicked.connect(self._on_delete)
        self.btnCopy.clicked.connect(self._on_copy)
        self.btnMove.clicked.connect(self._on_move)
        self.btnStat.clicked.connect(self._on_stat)
        self.btnMkdir.clicked.connect(self._on_mkdir)
        self.btnGoUp.clicked.connect(self._on_go_up)
        self.btnInvertSel.clicked.connect(self._on_invert_selection)
        self.btnCopyPath.clicked.connect(self._on_copy_path)
        self.fileList.itemDoubleClicked.connect(self._on_item_double_clicked)

    # ------------------------------------------------------------------
    # Drag & drop
    # ------------------------------------------------------------------

    def eventFilter(self, obj, event):
        """Intercept drag-and-drop events on the file list."""
        if obj == self.fileList:
            if event.type() == QEvent.Type.DragEnter:
                if event.mimeData().hasUrls():
                    event.acceptProposedAction()
                    return True
            elif event.type() == QEvent.Type.Drop:
                paths = [url.toLocalFile() for url in event.mimeData().urls()]
                if paths and not self.controller._is_busy and self.controller._is_connected:
                    self._upload_dropped(paths)
                return True
        return super().eventFilter(obj, event)

    def _upload_dropped(self, paths: list[str]):
        """Queue uploads for dropped files/folders, one at a time."""
        pending = []
        for p in paths:
            name = os.path.basename(p)
            remote = self._current_path.rstrip("/") + "/" + name
            pending.append((p, remote, name))

        self._drop_total = len(pending)
        self._drop_done = 0
        self._pending_uploads = pending
        self.controller.set_busy(True)
        self.controller.progressBar.setVisible(True)
        self.controller.progressBar.setValue(0)
        self._upload_dropped_next()

    def _upload_dropped_next(self):
        """Upload next item in the drop queue."""
        if not self._pending_uploads:
            self.controller.set_busy(False)
            self.controller.progressBar.setVisible(False)
            self.controller.progressLabel.setText("")
            InfoBar.success("上传完成", "", parent=self,
                            position=InfoBarPosition.TOP, duration=3000)
            self._list_current_path()
            return

        local, remote, name = self._pending_uploads.pop(0)
        self._drop_done += 1
        self.controller.progressLabel.setText(
            f"[{self._drop_done}/{self._drop_total}] 正在上传: {name}"
        )

        self._upload_worker = UsbUploadAssetWorker(
            self.controller.usbRC, local, remote, parent=self
        )
        self._upload_worker.progress_updated.connect(self._on_task_progress)
        self._upload_worker.upload_completed.connect(
            lambda _: self._upload_dropped_next())
        self._upload_worker.upload_failed.connect(
            lambda e, n=name: self._on_drop_upload_failed(e, n)
        )
        self._track_worker(self._upload_worker)
        self._upload_worker.start()

    def _on_drop_upload_failed(self, error, name: str):
        """Single drop-upload failed — continue with remaining."""
        InfoBar.error("上传失败", f"{name}: {error}", parent=self,
                      position=InfoBarPosition.TOP, duration=5000)
        self._upload_dropped_next()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh_file_list(self):
        """Public entry point to refresh file list"""
        self._list_current_path()

    def clear_file_list(self):
        """Clear the file list"""
        self.fileList.clear()
        self._current_path = "/"
        self.pathLabel.setText("/")
        self.btnGoUp.setEnabled(False)

    def set_buttons_enabled(self, enabled: bool):
        """Batch set button enabled state"""
        self.btnRefresh.setEnabled(enabled)
        self.btnUploadFile.setEnabled(enabled)
        self.btnUploadFolder.setEnabled(enabled)
        self.btnDownload.setEnabled(enabled)
        self.btnDelete.setEnabled(enabled)
        self.btnCopy.setEnabled(enabled)
        self.btnMove.setEnabled(enabled)
        self.btnStat.setEnabled(enabled)
        self.btnMkdir.setEnabled(enabled)
        self.btnInvertSel.setEnabled(enabled)
        self.btnCopyPath.setEnabled(enabled)
        self.btnGoUp.setEnabled(enabled and self._current_path != "/")
        self.fileList.setEnabled(enabled)

    def _track_worker(self, worker):
        """Dispose a finished background worker so parented QThreads don't pile up.

        Qt only frees child QObjects when the parent (this page) is destroyed, so
        each finished per-operation worker would otherwise leak for the page's
        lifetime. Connect QThread.finished -> deleteLater and drop our reference.
        """
        def _cleanup():
            for attr in (
                "_list_worker", "_upload_worker", "_download_worker",
                "_delete_worker", "_copy_worker", "_move_worker",
                "_stat_worker", "_mkdir_worker",
            ):
                if getattr(self, attr, None) is worker:
                    setattr(self, attr, None)
            worker.deleteLater()

        worker.finished.connect(_cleanup)
        return worker

    def shutdown(self):
        """Wait for all background workers"""
        for worker in [
            self._list_worker,
            self._upload_worker,
            self._download_worker,
            self._delete_worker,
            self._copy_worker,
            self._move_worker,
            self._stat_worker,
            self._mkdir_worker,
        ]:
            if worker and worker.isRunning():
                worker.wait(3000)

    # ------------------------------------------------------------------
    # List directory
    # ------------------------------------------------------------------

    def _list_current_path(self):
        """List files and dirs at the current remote path"""
        ctrl = self.controller
        if ctrl._is_busy or not ctrl._is_connected:
            return
        ctrl.set_busy(True)

        self._list_worker = UsbListAssetsWorker(
            ctrl.usbRC, self._current_path, parent=self
        )
        self._list_worker.list_completed.connect(self._on_list_loaded)
        self._list_worker.list_failed.connect(self._on_list_failed)
        self._track_worker(self._list_worker)
        self._list_worker.start()

    def _on_list_loaded(self, files: list, dirs: list):
        """Directory listing loaded"""
        self.fileList.clear()

        for d in sorted(dirs):
            widget = UsbFileListItemWidget(d, is_dir=True, parent_page=self)
            item = QListWidgetItem(self.fileList)
            item.setSizeHint(widget.sizeHint())
            item.setData(Qt.ItemDataRole.UserRole, {"name": d, "is_dir": True})
            self.fileList.addItem(item)
            self.fileList.setItemWidget(item, widget)

        for f in sorted(files):
            widget = UsbFileListItemWidget(f, is_dir=False, parent_page=self)
            item = QListWidgetItem(self.fileList)
            item.setSizeHint(widget.sizeHint())
            item.setData(Qt.ItemDataRole.UserRole, {
                         "name": f, "is_dir": False})
            self.fileList.addItem(item)
            self.fileList.setItemWidget(item, widget)

        # Disable go-up button at root
        self.btnGoUp.setEnabled(self._current_path.rstrip("/") != "")

        self.controller.set_busy(False)

    def _on_list_failed(self, error):
        """Directory listing failed"""
        self.controller.set_busy(False)
        InfoBar.error(
            "列出目录失败",
            str(error),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _on_copy_path(self):
        """Copy current path to clipboard"""
        QApplication.clipboard().setText(self._current_path)
        InfoBar.success("已复制", self._current_path, parent=self,
                        position=InfoBarPosition.TOP, duration=2000)

    def _on_go_up(self):
        """Navigate to parent directory"""
        parent = os.path.dirname(self._current_path.rstrip("/")) or "/"
        self._navigate_to(parent)

    def _on_item_double_clicked(self, item: QListWidgetItem):
        """Double-click directory → navigate into it"""
        data = item.data(Qt.ItemDataRole.UserRole)
        if data and data.get("is_dir"):
            new_path = self._current_path.rstrip("/") + "/" + data["name"]
            self._navigate_to(new_path)

    def _navigate_to(self, path: str):
        """Change current path and refresh listing"""
        self._current_path = path or "/"
        self.pathLabel.setText(self._current_path)
        self.btnGoUp.setEnabled(self._current_path.rstrip("/") != "")
        self._list_current_path()

    # ------------------------------------------------------------------
    # Upload file
    # ------------------------------------------------------------------

    def _on_upload_file(self):
        """Upload a single file to current remote directory"""
        ctrl = self.controller
        if ctrl._is_busy or not ctrl._is_connected:
            return
        local_path, _ = QFileDialog.getOpenFileName(self, "选择要上传的文件", "")
        if not local_path:
            return

        filename = os.path.basename(local_path)
        remote_path = self._current_path.rstrip("/") + "/" + filename

        ctrl.set_busy(True)
        ctrl.progressBar.setVisible(True)
        ctrl.progressBar.setValue(0)
        ctrl.progressLabel.setText(f"正在上传: {filename}")

        self._upload_worker = UsbUploadAssetWorker(
            ctrl.usbRC, local_path, remote_path, parent=self
        )
        self._upload_worker.progress_updated.connect(self._on_task_progress)
        self._upload_worker.upload_completed.connect(self._on_upload_done)
        self._upload_worker.upload_failed.connect(self._on_upload_failed)
        self._track_worker(self._upload_worker)
        self._upload_worker.start()

    def _on_upload_folder(self):
        """Upload a local folder to current remote directory"""
        ctrl = self.controller
        if ctrl._is_busy or not ctrl._is_connected:
            return
        local_dir = QFileDialog.getExistingDirectory(self, "选择要上传的文件夹", "")
        if not local_dir:
            return

        dirname = os.path.basename(local_dir)
        remote_path = self._current_path.rstrip("/") + "/" + dirname

        ctrl.set_busy(True)
        ctrl.progressBar.setVisible(True)
        ctrl.progressBar.setValue(0)
        ctrl.progressLabel.setText(f"正在上传: {dirname}")

        self._upload_worker = UsbUploadAssetWorker(
            ctrl.usbRC, local_dir, remote_path, parent=self
        )
        self._upload_worker.progress_updated.connect(self._on_task_progress)
        self._upload_worker.upload_completed.connect(self._on_upload_done)
        self._upload_worker.upload_failed.connect(self._on_upload_failed)
        self._track_worker(self._upload_worker)
        self._upload_worker.start()

    def _on_upload_done(self, remote_path: str):
        """Upload complete → refresh"""
        ctrl = self.controller
        ctrl.progressBar.setVisible(False)
        ctrl.progressLabel.setText("")
        ctrl.set_busy(False)
        InfoBar.success(
            "上传完成",
            f"已上传至 {remote_path}",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=3000,
        )
        self._list_current_path()

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

    def _on_delete(self):
        """Delete selected files/directories"""
        ctrl = self.controller
        if ctrl._is_busy or not ctrl._is_connected:
            return

        selected = self._get_selected_items()
        if not selected:
            InfoBar.warning(
                "未选择",
                "请先选择要删除的文件或文件夹。",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=3000,
            )
            return

        names = [s["name"] for s in selected]
        msg = "确认删除以下项目？\n" + \
            "\n".join(f"  • {n}" for n in names) + "\n此操作不可撤销。"
        reply = QMessageBox.question(
            self,
            "确认删除",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Gate the whole multi-item delete: set_busy disables the other USB
        # buttons so a concurrent worker cannot drive the shared UsbResponderClient
        # while the delete loop is running (root cause of the file-manager crash).
        ctrl.set_busy(True)
        ctrl.progressBar.setVisible(True)
        ctrl.progressBar.setValue(0)
        self._pending_deletes = selected
        self._delete_next()

    def _delete_next(self):
        """Delete items one at a time (sequential)"""
        if not self._pending_deletes:
            self.controller.set_busy(False)
            self.controller.progressBar.setVisible(False)
            self.controller.progressLabel.setText("")
            InfoBar.success(
                "删除完成",
                "已删除所有选中项目。",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=3000,
            )
            self._list_current_path()
            return

        item = self._pending_deletes.pop(0)
        remote_path = self._current_path.rstrip("/") + "/" + item["name"]
        self.controller.progressLabel.setText(f"正在删除: {item['name']}")

        self._delete_worker = UsbDeleteAssetWorker(
            self.controller.usbRC, remote_path, parent=self
        )
        self._delete_worker.delete_completed.connect(
            lambda _: self._delete_next()
        )
        self._delete_worker.delete_failed.connect(
            lambda e: self._on_delete_one_failed(e, item["name"])
        )
        self._track_worker(self._delete_worker)
        self._delete_worker.start()

    def _on_delete_one_failed(self, error, name: str):
        """Single delete failed — continue with remaining items"""
        InfoBar.error(
            "删除失败",
            f"{name}: {error}",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )
        self._delete_next()

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def _on_download(self):
        """Download selected files/directories"""
        ctrl = self.controller
        if ctrl._is_busy or not ctrl._is_connected:
            return

        selected = self._get_selected_items()
        if not selected:
            InfoBar.warning(
                "未选择",
                "请先选择要下载的文件或文件夹。",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=3000,
            )
            return

        save_dir = QFileDialog.getExistingDirectory(self, "选择保存目录")
        if not save_dir:
            return

        self._pending_downloads = []
        for s in selected:
            remote_path = self._current_path.rstrip("/") + "/" + s["name"]
            local_path = os.path.join(save_dir, s["name"])
            self._pending_downloads.append(
                (remote_path, local_path, s["name"]))

        ctrl.set_busy(True)
        ctrl.progressBar.setVisible(True)
        ctrl.progressBar.setValue(0)
        self._download_next()

    def _download_next(self):
        """Download items one at a time (sequential)"""
        if not self._pending_downloads:
            self.controller.set_busy(False)
            self.controller.progressBar.setVisible(False)
            self.controller.progressLabel.setText("")
            InfoBar.success(
                "下载完成",
                "已下载所有选中项目。",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=3000,
            )
            return

        remote_path, local_path, name = self._pending_downloads.pop(0)
        self.controller.progressLabel.setText(f"正在下载: {name}")

        self._download_worker = UsbDownloadAssetWorker(
            self.controller.usbRC, remote_path, local_path, parent=self
        )
        self._download_worker.progress_updated.connect(self._on_task_progress)
        self._download_worker.download_completed.connect(
            lambda _: self._download_next()
        )
        self._download_worker.download_failed.connect(
            lambda e: self._on_download_one_failed(e, name)
        )
        self._track_worker(self._download_worker)
        self._download_worker.start()

    def _on_download_one_failed(self, error, name: str):
        """Single download failed — continue with remaining items"""
        InfoBar.error(
            "下载失败",
            f"{name}: {error}",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )
        self._download_next()

    # ------------------------------------------------------------------
    # Copy
    # ------------------------------------------------------------------

    def _on_copy(self):
        """Copy checked item to a destination path via cp -r"""
        ctrl = self.controller
        if ctrl._is_busy or not ctrl._is_connected:
            return

        checked = self._get_selected_items()
        if not checked:
            InfoBar.warning("未选择", "请先勾选要复制的文件或文件夹。", parent=self,
                            position=InfoBarPosition.TOP, duration=3000)
            return

        src_name = checked[0]["name"]
        src_path = self._current_path.rstrip("/") + "/" + src_name

        dst, ok = QInputDialog.getText(self, "复制", f"将 \"{src_name}\" 复制到：")
        if not ok or not dst.strip():
            return

        ctrl.set_busy(True)
        ctrl.progressLabel.setText(f"正在复制: {src_name}")

        self._copy_worker = UsbCopyWorker(
            ctrl.usbRC, src_path, dst.strip(), parent=self)
        self._copy_worker.copy_completed.connect(self._on_copy_done)
        self._copy_worker.copy_failed.connect(self._on_copy_failed)
        self._track_worker(self._copy_worker)
        self._copy_worker.start()

    def _on_copy_done(self):
        self.controller.set_busy(False)
        self.controller.progressLabel.setText("")
        InfoBar.success("复制完成", "", parent=self,
                        position=InfoBarPosition.TOP, duration=3000)
        self._list_current_path()

    def _on_copy_failed(self, error):
        self.controller.set_busy(False)
        self.controller.progressLabel.setText("")
        InfoBar.error("复制失败", str(error), parent=self,
                      position=InfoBarPosition.TOP, duration=5000)

    # ------------------------------------------------------------------
    # Move
    # ------------------------------------------------------------------

    def _on_move(self):
        """Move/rename checked items via file_rename"""
        ctrl = self.controller
        if ctrl._is_busy or not ctrl._is_connected:
            return

        checked = self._get_selected_items()
        if not checked:
            InfoBar.warning("未选择", "请先勾选要移动的文件或文件夹。", parent=self,
                            position=InfoBarPosition.TOP, duration=3000)
            return

        if len(checked) == 1:
            # Single item: prompt for destination path (rename or move)
            src_name = checked[0]["name"]
            dst, ok = QInputDialog.getText(
                self, "移动", f"将 \"{src_name}\" 移动到：\n（请输入已存在的绝对路径）")
            if not ok or not dst.strip():
                return
            self._pending_moves = [{
                "name": src_name,
                "src": self._current_path.rstrip("/") + "/" + src_name,
                "dst": dst.strip(),
            }]
        else:
            # Multiple items: prompt for destination directory
            dst, ok = QInputDialog.getText(
                self, "移动", f"将 {len(checked)} 个项目移动到目录：")
            if not ok or not dst.strip():
                return
            dst_dir = dst.strip().rstrip("/")
            self._pending_moves = []
            for item in checked:
                name = item["name"]
                self._pending_moves.append({
                    "name": name,
                    "src": self._current_path.rstrip("/") + "/" + name,
                    "dst": dst_dir + "/" + name,
                })

        ctrl.set_busy(True)
        self._move_next()

    def _move_next(self):
        """Move items one at a time (sequential)"""
        if not self._pending_moves:
            self.controller.set_busy(False)
            self.controller.progressLabel.setText("")
            InfoBar.success("移动完成", "", parent=self,
                            position=InfoBarPosition.TOP, duration=3000)
            self._list_current_path()
            return

        entry = self._pending_moves.pop(0)
        self.controller.progressLabel.setText(f"正在移动: {entry['name']}")

        self._move_worker = UsbMoveWorker(
            self.controller.usbRC, entry["src"], entry["dst"], parent=self
        )
        self._move_worker.move_completed.connect(lambda: self._move_next())
        self._move_worker.move_failed.connect(
            lambda e, n=entry["name"]: self._on_move_one_failed(e, n)
        )
        self._track_worker(self._move_worker)
        self._move_worker.start()

    def _on_move_one_failed(self, error, name: str):
        """Single move failed — continue with remaining items"""
        InfoBar.error("移动失败", f"{name}: {error}", parent=self,
                      position=InfoBarPosition.TOP, duration=5000)
        self._move_next()

    def _on_move_failed(self, error):
        self.controller.set_busy(False)
        self.controller.progressLabel.setText("")
        InfoBar.error("移动失败", str(error), parent=self,
                      position=InfoBarPosition.TOP, duration=5000)

    # ------------------------------------------------------------------
    # Stat
    # ------------------------------------------------------------------

    def _on_stat(self):
        """Show file/directory properties via file_stat.

        Prefers the highlighted list item; falls back to checkbox selection.
        """
        ctrl = self.controller
        if ctrl._is_busy or not ctrl._is_connected:
            return

        # Prefer highlighted item
        selected = self.fileList.selectedItems()
        if selected:
            data = selected[0].data(Qt.ItemDataRole.UserRole)
            if data:
                name = data["name"]
            else:
                return
        else:
            # Fallback to checkbox
            checked = self._get_selected_items()
            if not checked:
                InfoBar.warning("未选择", "请先选中或勾选要查看属性的文件或文件夹。",
                                parent=self, position=InfoBarPosition.TOP, duration=3000)
                return
            name = checked[0]["name"]

        path = self._current_path.rstrip("/") + "/" + name

        ctrl.set_busy(True)
        ctrl.progressLabel.setText(f"正在获取属性: {name}")

        self._stat_worker = UsbStatWorker(ctrl.usbRC, path, parent=self)
        self._stat_worker.stat_completed.connect(
            lambda info, n=name: self._on_stat_done(n, info))
        self._stat_worker.stat_failed.connect(self._on_stat_failed)
        self._track_worker(self._stat_worker)
        self._stat_worker.start()

    _STAT_LABELS = {
        "owner": "所有者",
        "perm": "权限",
        "size": "大小",
        "type": "类型",
    }

    @staticmethod
    def _format_size(val: str) -> str:
        """Format raw byte count to human-readable form."""
        try:
            size = int(val)
            for unit in ("B", "KB", "MB", "GB", "TB"):
                if abs(size) < 1024:
                    return f"{size} {unit}"
                size //= 1024
            return f"{size} PB"
        except (ValueError, TypeError):
            return val

    @staticmethod
    def _format_perm(val: str) -> str:
        """Format octal permission to rwx notation (e.g. 0755 → rwxr-xr-x)."""
        rwx_map = {"0": "---", "1": "--x", "2": "-w-", "3": "-wx",
                   "4": "r--", "5": "r-x", "6": "rw-", "7": "rwx"}
        perm = val.strip().lstrip("0") or "0"
        try:
            if len(perm) <= 3:
                perm = perm.zfill(3)
            result = "".join(rwx_map.get(d, "???") for d in perm[-3:])
            return f"{val}  ({result})"
        except Exception:
            return val

    def _on_stat_done(self, name: str, info: dict):
        self.controller.set_busy(False)
        self.controller.progressLabel.setText("")

        # Human-readable section
        human_lines = []
        for k, v in info.items():
            label = self._STAT_LABELS.get(k, k)
            if k == "size":
                display_val = self._format_size(v)
            elif k == "perm":
                display_val = self._format_perm(v)
            else:
                display_val = v
            human_lines.append(f"  {label}:  {display_val}")

        # Raw section
        raw_lines = ["--- 原始返回 ---"]
        for k, v in info.items():
            raw_lines.append(f"  {k}: {v}")

        text = "\n".join(human_lines + [""] + raw_lines)
        QMessageBox.information(self, f"属性 - {name}", text)

    def _on_stat_failed(self, error):
        self.controller.set_busy(False)
        self.controller.progressLabel.setText("")
        InfoBar.error("获取属性失败", str(error), parent=self,
                      position=InfoBarPosition.TOP, duration=5000)

    # ------------------------------------------------------------------
    # Mkdir
    # ------------------------------------------------------------------

    def _on_mkdir(self):
        """Create a new directory in current path"""
        ctrl = self.controller
        if ctrl._is_busy or not ctrl._is_connected:
            return

        name, ok = QInputDialog.getText(self, "新建文件夹", "文件夹名称：")
        if not ok or not name.strip():
            return

        path = self._current_path.rstrip("/") + "/" + name.strip()
        ctrl.set_busy(True)
        ctrl.progressLabel.setText(f"正在创建文件夹: {name.strip()}")

        self._mkdir_worker = UsbMkdirWorker(ctrl.usbRC, path, parent=self)
        self._mkdir_worker.mkdir_completed.connect(self._on_mkdir_done)
        self._mkdir_worker.mkdir_failed.connect(self._on_mkdir_failed)
        self._track_worker(self._mkdir_worker)
        self._mkdir_worker.start()

    def _on_mkdir_done(self):
        self.controller.set_busy(False)
        self.controller.progressLabel.setText("")
        InfoBar.success("文件夹已创建", "", parent=self,
                        position=InfoBarPosition.TOP, duration=3000)
        self._list_current_path()

    def _on_mkdir_failed(self, error):
        self.controller.set_busy(False)
        self.controller.progressLabel.setText("")
        InfoBar.error("创建文件夹失败", str(error), parent=self,
                      position=InfoBarPosition.TOP, duration=5000)

    # ------------------------------------------------------------------
    # Invert selection
    # ------------------------------------------------------------------

    def _on_invert_selection(self):
        """Invert the checked state of all checkboxes in the file list."""
        for i in range(self.fileList.count()):
            widget = self.fileList.itemWidget(self.fileList.item(i))
            if widget:
                widget.checkbox.setChecked(not widget.checkbox.isChecked())

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_selected_items(self) -> list[dict]:
        """Return user-data dicts for checkbox-checked items.

        Falls back to the currently highlighted list item if no checkboxes
        are checked, so single-click selection also works for all operations.
        """
        result = []
        for i in range(self.fileList.count()):
            item = self.fileList.item(i)
            widget = self.fileList.itemWidget(item)
            if widget and widget.checkbox.isChecked():
                data = item.data(Qt.ItemDataRole.UserRole)
                if data:
                    result.append(data)

        if not result:
            # Fallback: use the currently selected (highlighted) list item
            selected = self.fileList.selectedItems()
            if selected:
                data = selected[0].data(Qt.ItemDataRole.UserRole)
                if data:
                    result.append(data)

        return result
