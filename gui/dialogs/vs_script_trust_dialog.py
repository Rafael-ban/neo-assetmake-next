"""用户项目 VPY 的本机信任确认。"""

from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


class VSScriptTrustDialog(QDialog):
    """仅用于确认执行一个已解析的项目脚本 bundle；它不是安全沙箱。"""

    def __init__(
        self,
        *,
        canonical_root: str,
        main_script: str,
        code_files: tuple[str, ...],
        bundle_hash: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("信任项目 VPY 脚本")
        self.setModal(True)
        layout = QVBoxLayout(self)

        details = (
            f"脚本根目录：{canonical_root}\n"
            f"主脚本：{main_script}\n"
            f"代码文件：{', '.join(code_files) or '无'}\n"
            f"SHA-256：{bundle_hash}"
        )
        details_label = QLabel(details)
        details_label.setWordWrap(True)
        layout.addWidget(details_label)

        risk = QLabel(
            "该脚本可执行任意 Python，可能读取或写入文件、访问网络、启动进程。"
            "信任只是一道本机 UX 防线，不是沙箱。请勿直接写 stdout 文件描述符，"
            "否则会破坏 worker 协议。"
        )
        risk.setWordWrap(True)
        layout.addWidget(risk)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.trust_button = QPushButton("信任并运行")
        self.cancel_button = QPushButton("取消")
        self.trust_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.trust_button)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)


__all__ = ["VSScriptTrustDialog"]
