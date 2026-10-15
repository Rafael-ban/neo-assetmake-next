"""Remote upload progress dialog."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QDialog, QVBoxLayout
from qfluentwidgets import BodyLabel, ProgressBar, PushButton, SubtitleLabel


class RemoteUploadProgressDialog(QDialog):
    """Progress dialog for RNDIS HTTP asset uploads."""

    cancel_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_completed = False
        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("远程上传")
        self.setMinimumSize(400, 150)
        self.setModal(True)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        self.label_status = SubtitleLabel("准备远程上传...")
        self.label_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label_status)

        self.progress_bar = ProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)

        self.label_detail = BodyLabel("")
        self.label_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label_detail)

        self.btn_action = PushButton("取消")
        self.btn_action.clicked.connect(self._on_action_clicked)
        layout.addWidget(self.btn_action)

    def update_progress(self, value: int, message: str):
        self.progress_bar.setValue(value)
        self.label_detail.setText(message)

    def set_completed(self, success: bool, message: str):
        self._is_completed = True
        self.progress_bar.setValue(100 if success else self.progress_bar.value())
        self.label_status.setText("远程上传完成" if success else "远程上传失败")
        self.label_detail.setText(message)
        self.btn_action.setText("确定")
        self.label_status.setStyleSheet("color: green;" if success else "color: red;")

    def _on_action_clicked(self):
        if self._is_completed:
            self.accept()
            return
        self.cancel_requested.emit()
        self.label_status.setText("正在取消...")
        self.btn_action.setEnabled(False)

    def closeEvent(self, event):
        if self._is_completed:
            event.accept()
        else:
            event.ignore()
