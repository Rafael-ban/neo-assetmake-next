"""VPY 来源与 header 的只读状态面板。"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from core.vs_runtime.script_header import ScriptHeader
from core.vs_runtime.trust import ScriptReference


class VSScriptPanel(QWidget):
    """显示脚本状态；只提供来源选择及重载，不提供 VPY 编辑器。"""

    source_requested = pyqtSignal(str)
    reload_requested = pyqtSignal()
    open_directory_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(QLabel("VapourSynth 脚本"))

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("来源："))
        self.source_combo = QComboBox()
        self.source_combo.addItem("内置脚本", "builtin")
        self.source_combo.addItem("全局本机脚本", "global")
        self.source_combo.addItem("项目脚本", "project")
        self.source_combo.currentIndexChanged.connect(self._emit_source)
        source_row.addWidget(self.source_combo)
        self.reload_button = QPushButton("重载")
        self.reload_button.clicked.connect(self.reload_requested.emit)
        source_row.addWidget(self.reload_button)
        self.open_directory_button = QPushButton("打开目录")
        self.open_directory_button.clicked.connect(self.open_directory_requested.emit)
        source_row.addWidget(self.open_directory_button)
        layout.addLayout(source_row)

        self.source_label = QLabel("来源：builtin")
        self.path_label = QLabel("路径：")
        self.header_label = QLabel("header：未加载")
        self.trust_label = QLabel("信任：未加载")
        for label in (
            self.source_label,
            self.path_label,
            self.header_label,
            self.trust_label,
        ):
            label.setWordWrap(True)
            layout.addWidget(label)

    def _emit_source(self) -> None:
        source = self.source_combo.currentData()
        if isinstance(source, str):
            self.source_requested.emit(source)

    def set_script_info(
        self,
        *,
        reference: ScriptReference,
        canonical_root: str,
        main_script: str,
        header: ScriptHeader,
        bundle_hash: str,
        trusted: bool,
    ) -> None:
        index = self.source_combo.findData(reference.source)
        self.source_combo.blockSignals(True)
        self.source_combo.setCurrentIndex(index)
        self.source_combo.blockSignals(False)
        self.source_label.setText(f"来源：{reference.source}")
        self.path_label.setText(
            f"路径：{reference.path or main_script}\n根目录：{canonical_root}"
        )
        self.header_label.setText(
            "header："
            f"mode={header.mode}，API={header.api_version}，"
            f"capabilities={', '.join(header.capabilities) or '无'}，"
            f"requires={', '.join(header.requires) or '无'}，"
            f"editor_output={header.editor_output}"
        )
        state = "已信任" if trusted else "未获信任"
        self.trust_label.setText(f"信任：{state}\nSHA-256：{bundle_hash}")

    def set_error(self, reference: ScriptReference, reason: str) -> None:
        index = self.source_combo.findData(reference.source)
        self.source_combo.blockSignals(True)
        self.source_combo.setCurrentIndex(index)
        self.source_combo.blockSignals(False)
        self.source_label.setText(f"来源：{reference.source}")
        self.path_label.setText(f"路径：{reference.path or '本机设置'}")
        self.header_label.setText("header：无法加载")
        self.trust_label.setText(f"信任：不可执行\n原因：{reason}")


__all__ = ["VSScriptPanel"]
