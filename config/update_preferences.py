"""用户手动选择的更新渠道，与主窗口共用设置存储。"""
from enum import Enum

from PyQt6.QtCore import QSettings


class UpdateChannel(str, Enum):
    STABLE = "stable"
    PREVIEW = "preview"


def load_update_channel() -> UpdateChannel:
    value = QSettings("ArknightsPassMaker", "MainWindow").value(
        "update_channel", UpdateChannel.STABLE.value,
    )
    try:
        return UpdateChannel(value)
    except (TypeError, ValueError):
        return UpdateChannel.STABLE


def save_update_channel(channel: UpdateChannel) -> None:
    settings = QSettings("ArknightsPassMaker", "MainWindow")
    settings.setValue("update_channel", UpdateChannel(channel).value)
    settings.sync()
