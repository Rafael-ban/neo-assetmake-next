'''USB管理器子页面容器'''
from __future__ import annotations
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from gui.widgets.usb_control_page import UsbControlPage

from gui.widgets.usb_app_page import UsbAppPage
from gui.widgets.usb_file_page import UsbFilePage
from gui.widgets.usb_material_page import UsbMaterialPage


class UsbSubPageWidget(QWidget):
    """USB管理器右侧子页面容器。

    包含素材管理、应用管理和文件管理三个子页面，
    通过 QStackedWidget 切换显示。
    """

    def __init__(self, controller: UsbControlPage, parent=None):
        super().__init__(parent)
        self.controller = controller

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.stackedWidget = QStackedWidget()

        self.materialPage = UsbMaterialPage(controller=self.controller)
        self.appPage = UsbAppPage(controller=self.controller)
        self.filePage = UsbFilePage(controller=self.controller)

        self.stackedWidget.addWidget(self.materialPage)   # index 0
        self.stackedWidget.addWidget(self.appPage)        # index 1
        self.stackedWidget.addWidget(self.filePage)       # index 2
        layout.addWidget(self.stackedWidget)

    # ------------------------------------------------------------------
    # Public API — 委托给对应子页面
    # ------------------------------------------------------------------

    def setCurrentIndex(self, index: int):
        """切换当前显示的子页面"""
        self.stackedWidget.setCurrentIndex(index)

    # -- materialPage delegates --

    def refresh_asset_list(self):
        """委托 materialPage 刷新素材列表"""
        self.materialPage.refresh_asset_list()

    def clear_asset_list(self):
        """委托 materialPage 清空素材列表"""
        self.materialPage.clear_asset_list()

    # -- appPage delegates --

    def refresh_app_list(self):
        """委托 appPage 刷新应用列表"""
        self.appPage.refresh_app_list()

    def clear_app_list(self):
        """委托 appPage 清空应用列表"""
        self.appPage.clear_app_list()

    # -- filePage delegates --

    def refresh_file_list(self):
        """委托 filePage 刷新文件列表"""
        self.filePage.refresh_file_list()

    def clear_file_list(self):
        """委托 filePage 清空文件列表"""
        self.filePage.clear_file_list()

    def currentIndex(self) -> int:
        """返回当前显示的子页面索引"""
        return self.stackedWidget.currentIndex()

    def refresh_current_page(self):
        """根据当前显示的子页面，刷新对应的列表"""
        idx = self.stackedWidget.currentIndex()
        if idx == 0:
            self.materialPage.refresh_asset_list()
        elif idx == 1:
            self.appPage.refresh_app_list()
        else:
            self.filePage.refresh_file_list()

    # -- shared delegates --

    def set_buttons_enabled(self, enabled: bool):
        """委托所有子页面设置按钮启用状态"""
        self.materialPage.set_buttons_enabled(enabled)
        self.appPage.set_buttons_enabled(enabled)
        self.filePage.set_buttons_enabled(enabled)

    def shutdown(self):
        """委托所有子页面关闭工作线程并清理"""
        self.materialPage.shutdown()
        self.appPage.shutdown()
        self.filePage.shutdown()
