'''USB管理器页'''
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QInputDialog,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    ComboBox,
    FluentIcon,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    SimpleCardWidget,
    SubtitleLabel,
)

from gui.widgets.usb_sub_pages import UsbSubPageWidget
from gui.workers.usb_workers import UsbConnectWorker, UsbRebootWorker, UsbRestartDrmWorker
import usb.core
import usb.util


class UsbControlPage(QWidget):
    setting_changed = pyqtSignal(str, object)
    usb_exception = pyqtSignal(object)

    def __init__(self, parent):
        super().__init__(parent)
        self._parent = parent
        self._settings: dict = {}
        self._is_busy = False
        self._is_connected = False
        self._is_scanning = False

        # Worker references
        self._connect_worker = None
        self._restart_worker = None
        self._reboot_worker = None

        # Scan timer (used after reboot to auto-reconnect)
        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(2000)
        self._scan_timer.timeout.connect(self._on_scan_tick)

        self._init_ui()
        self._connect_signals()
        self.set_busy(False)
        self.VID = self._settings.get("usb_controler_vid")
        self.PID = self._settings.get("usb_controler_pid")
        self.enable_restart = self._settings.get(
            'usb_controler_auto_restart_program')

    def _init_ui(self):
        '''初始化UI'''
        self.mainLayout = QVBoxLayout(self)
        self.mainLayout.setContentsMargins(0, 15, 0, 0)
        self.mainLayout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.titleLabel = SubtitleLabel("EPass USB管理器模式", self)
        self.titleLabel.setContentsMargins(30, 0, 0, 0)
        self.mainLayout.addWidget(self.titleLabel)
        self.mainLayout.addSpacing(10)

        # 两列：左列（连接按钮+下拉菜单），右列（子页面堆叠）
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setContentsMargins(10, 0, 10, 10)
        self._build_left_panel()
        self.subPageWidget = UsbSubPageWidget(controller=self, parent=self)
        self.splitter.addWidget(self.leftPanel)
        self.splitter.addWidget(self.subPageWidget)
        self.splitter.setSizes([200, 660])
        self.splitter.setStretchFactor(1, 3)
        self.mainLayout.addWidget(self.splitter, 1)

        # 底部
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
        '''左列构建 — 仅保留连接按钮和子页面下拉菜单'''
        self.leftPanel = SimpleCardWidget()
        self.leftPanel.setMinimumWidth(160)
        self.leftPanel.setMaximumWidth(200)

        layout = QVBoxLayout(self.leftPanel)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.btnConnect = PrimaryPushButton("连接设备")
        self.btnConnect.setIcon(FluentIcon.WIFI)
        layout.addWidget(self.btnConnect)

        self.btnRestartDrm = PushButton("重启 DrmApp")
        self.btnRestartDrm.setIcon(FluentIcon.UPDATE)
        layout.addWidget(self.btnRestartDrm)

        self.btnReboot = PushButton("重启设备")
        self.btnReboot.setIcon(FluentIcon.POWER_BUTTON)
        layout.addWidget(self.btnReboot)

        self.comboSubPage = ComboBox()
        self.comboSubPage.addItems(["素材管理", "应用管理", "文件管理"])
        self.comboSubPage.setCurrentIndex(0)
        layout.addWidget(self.comboSubPage)

    def _connect_signals(self):
        # UI 对象信号
        self.btnConnect.clicked.connect(self._on_connect)

        # 子页面下拉切换
        self.comboSubPage.currentIndexChanged.connect(
            self._on_sub_page_changed)

        # 重启DRM
        self.btnRestartDrm.clicked.connect(self._on_restart_drm)

        # 重启设备
        self.btnReboot.clicked.connect(self._on_reboot)

        # UI操作信号
        self.usb_exception.connect(self._on_usb_exception)

    def _on_sub_page_changed(self, index: int):
        """子页面切换"""
        self.subPageWidget.setCurrentIndex(index)

    # ------------------------------------------------------------------
    # Connect / disconnect
    # ------------------------------------------------------------------

    def _on_connect(self):
        self.VID = int(self._settings.get("usb_controler_vid"), 16)
        self.PID = int(self._settings.get("usb_controler_pid"), 16)
        """连接click"""
        if self._is_scanning:
            self._stop_scanning()
            self._update_connection_ui("Disconnected")
            return
        if (self._is_connected == True):
            self._on_manually_disconnect()
            return
        self.set_busy(True)
        self._update_connection_ui("Connecting")

        # 枚举设备 (UI线程，通常很快)
        usbDeviceList = []
        for dev in usb.core.find(find_all=True):
            if dev.idVendor == self.VID and dev.idProduct == self.PID:
                usbDeviceList.append(dev)

        usbNumberCounter = len(usbDeviceList)

        # 没有设备
        if usbNumberCounter == 0:
            InfoBar.error(
                "连接失败 无设备",
                f"找不到： VID {hex(self.VID)},PID {hex(self.PID)}",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=6000,
            )
            self._update_connection_ui("Disconnected")
            return

        if usbNumberCounter == 1:
            # 只有一个设备
            dev = usbDeviceList[0]
        else:
            # 多个设备，让用户选择
            items = [
                f"Bus {dev.bus}  Address {dev.address}  VID {hex(dev.idVendor)}  PID {hex(dev.idProduct)}"
                for dev in usbDeviceList
            ]

            item, ok = QInputDialog.getItem(
                self,
                "选择USB设备",
                "请选择需要连接的设备：",
                items,
                0,
                False
            )
            if not ok:
                self._update_connection_ui("Disconnected")
                return
            index = items.index(item)
            dev = usbDeviceList[index]

        # 在后台线程中创建 UsbResponderClient 并握手，避免阻塞 UI
        self._connect_worker = UsbConnectWorker(
            vid=dev.idVendor,
            pid=dev.idProduct,
            bus=dev.bus,
            address=dev.address,
            interface=0,
            timeout_ms=40000,
            usb_exception_callback=self.usb_exception.emit,
            parent=self,
        )
        self._connect_worker.connect_succeeded.connect(
            self._on_connect_success)
        self._connect_worker.connect_failed.connect(self._on_connect_fail)
        self._connect_worker.start()

    def _on_connect_success(self, usbRC, kv: dict):
        """连接成功（回调在 UI 线程）"""
        self.usbRC = usbRC
        temp_string_builder = ""
        for k in sorted(kv.keys()):
            temp_string_builder += f"{k}={kv[k]}\n"

        # 从 usbRC 中获取设备的 bus/address 信息来显示
        InfoBar.success(
            "连接成功",
            f"已连接到设备\n{temp_string_builder}",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=6000,
        )
        self._update_connection_ui("Connected")
        self.subPageWidget.refresh_current_page()

    def _on_connect_fail(self, error):
        """连接失败（回调在 UI 线程）"""
        self._on_usb_exception(error)

    def _on_usb_exception(self, error=None):
        '''异常接收   （超级无敌霹雳大兜底()()()()）'''
        need_disconnect = False

        if isinstance(error, usb.core.USBError):
            title = "USB通信失败"
            message_builder = f"USB设备通信异常：{error}"
            need_disconnect = True
        elif isinstance(error, TimeoutError):
            title = "USB通信超时"
            message_builder = "USB设备响应超时，请稍后重试"
        elif isinstance(error, RuntimeError):
            title = "USB协议错误"
            message_builder = str(error) or "USB协议异常"
        elif isinstance(error, (FileNotFoundError, OSError)):
            title = "USB文件操作失败"
            message_builder = str(error) or "文件操作失败"
        else:
            title = "USB操作失败"
            message_builder = str(error) if error else "设备断开连接"
            need_disconnect = True
        if (need_disconnect):
            try:
                self.usbRC.close()
            except:
                '''ignore'''
            self.usbRC = None
            self.subPageWidget.clear_asset_list()
            self.subPageWidget.clear_app_list()
            self.subPageWidget.clear_file_list()
            self._update_connection_ui("Disconnected")
        InfoBar.error(
            title,
            message_builder,
            parent=self,
            position=InfoBarPosition.TOP,
            duration=6000,
        )

    def _on_manually_disconnect(self):
        '''手动断开连接'''
        if (self.usbRC != None):
            try:
                self.usbRC.close()
            except Exception as ex:
                InfoBar.error(
                    "断开失败",
                    str(ex),
                    parent=self,
                    position=InfoBarPosition.TOP,
                    duration=6000,
                )
                return
            self.usbRC = None
            self.subPageWidget.clear_asset_list()
            self.subPageWidget.clear_app_list()
            self.subPageWidget.clear_file_list()
            self._update_connection_ui("Disconnected")

    # ------------------------------------------------------------------
    # Restart DRM
    # ------------------------------------------------------------------

    def _on_restart_drm(self):
        '''重启DRM'''
        if self._is_busy or not self._is_connected:
            return
        self.set_busy(True)

        self._restart_worker = UsbRestartDrmWorker(
            self.usbRC, parent=self
        )
        self._restart_worker.restart_succeeded.connect(self._on_restart_done)
        self._restart_worker.restart_failed.connect(self._on_restart_failed)
        self._restart_worker.start()

    def _on_restart_done(self):
        """重启DRM完成"""
        self.set_busy(False)
        InfoBar.success(
            "已请求重启",
            "已向设备发送 DrmApp 重启请求。",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=3000,
        )

    def _on_restart_failed(self, error):
        """重启DRM失败"""
        self.set_busy(False)
        InfoBar.error(
            "重启失败",
            str(error),
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )

    # ------------------------------------------------------------------
    # Reboot device
    # ------------------------------------------------------------------

    def _on_reboot(self):
        '''重启设备 — 发送 reboot 命令后断开连接'''
        if self._is_busy or not self._is_connected:
            return
        self.set_busy(True)
        self.connectionStatusLabel.setText("正在重启设备...")

        self._reboot_worker = UsbRebootWorker(self.usbRC, parent=self)
        self._reboot_worker.reboot_completed.connect(self._on_reboot_done)
        self._reboot_worker.start()

    def _on_reboot_done(self):
        """重启命令已发送，清理连接并开始扫描设备"""
        self.set_busy(False)
        self.usbRC = None
        self.subPageWidget.clear_asset_list()
        self.subPageWidget.clear_app_list()
        self.subPageWidget.clear_file_list()
        InfoBar.info(
            "已发送重启命令",
            f"正在扫描等待设备重启...",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=3000,
        )
        self._start_scanning()

    # ------------------------------------------------------------------
    # Device scanning (post-reboot auto-reconnect)
    # ------------------------------------------------------------------

    def _start_scanning(self):
        """开始扫描设备，等待重启后重连"""
        self._is_scanning = True
        self.connectionStatusLabel.setText("正在扫描设备...")
        self.btnConnect.setText("取消扫描")
        self.btnConnect.setEnabled(True)
        self.comboSubPage.setEnabled(False)
        self.btnRestartDrm.setEnabled(False)
        self.btnReboot.setEnabled(False)
        self.subPageWidget.set_buttons_enabled(False)
        self.progressBar.setRange(0, 0)  # 不确定进度
        self.progressBar.setVisible(True)
        self._scan_timer.start()

    def _stop_scanning(self):
        """停止扫描"""
        self._scan_timer.stop()
        self._is_scanning = False
        self.progressBar.setVisible(False)
        self.progressBar.setRange(0, 100)

    def _on_scan_tick(self):
        """扫描定时器回调 — 枚举设备并尝试连接"""
        self.VID = int(self._settings.get("usb_controler_vid"), 16)
        self.PID = int(self._settings.get("usb_controler_pid"), 16)

        # 枚举匹配设备
        candidates = []
        for dev in usb.core.find(find_all=True):
            if dev.idVendor == self.VID and dev.idProduct == self.PID:
                candidates.append(dev)

        if not candidates:
            # 未发现设备，继续扫描
            return

        # 发现设备，选第一个尝试连接
        self._stop_scanning()
        dev = candidates[0]
        self._update_connection_ui("Connecting")

        self._connect_worker = UsbConnectWorker(
            vid=dev.idVendor,
            pid=dev.idProduct,
            bus=dev.bus,
            address=dev.address,
            interface=0,
            timeout_ms=40000,
            usb_exception_callback=self.usb_exception.emit,
            parent=self,
        )
        self._connect_worker.connect_succeeded.connect(
            self._on_connect_success)
        self._connect_worker.connect_failed.connect(
            self._on_scan_connect_failed)
        self._connect_worker.start()

    def _on_scan_connect_failed(self, error):
        """扫描中发现设备但连接失败 — 停止扫描，显示错误"""
        self._update_connection_ui("Disconnected")
        self._on_usb_exception(error)

    # ------------------------------------------------------------------
    # Settings & UI helpers
    # ------------------------------------------------------------------

    def load_settings(self, settings: dict):
        self._settings = settings.copy()

    def _update_connection_ui(self, state: str, success_str=None):
        if state == "Connected":
            self._is_connected = True
            self._is_scanning = False
            self._scan_timer.stop()
            self.connectionStatusLabel.setText(
                f"已连接设备：{success_str}" if success_str else "已连接设备"
            )
            self.btnConnect.setText("断开连接")
            self._is_busy = False
        elif state == "Connecting":
            self.connectionStatusLabel.setText("正在连接 USB 设备...")
            self.btnConnect.setText("连接中...")
            self._is_busy = True
        elif state == "Disconnected":
            self._is_connected = False
            self._is_scanning = False
            self._scan_timer.stop()
            self.connectionStatusLabel.setText("连接失败")
            self.btnConnect.setText("连接设备")
            self._is_busy = False
        self.set_busy(self._is_busy)

    def set_busy(self, busy: bool):
        """设置全局忙状态，同时影响主页面和子页面控件"""
        self._is_busy = busy
        self.btnConnect.setEnabled(not busy)
        self.comboSubPage.setEnabled(not busy)
        self.btnRestartDrm.setEnabled(not busy and self._is_connected)
        self.btnReboot.setEnabled(not busy and self._is_connected)
        if self.subPageWidget:
            self.subPageWidget.set_buttons_enabled(
                not busy and self._is_connected
            )

    def shutdown(self):
        """等待所有后台工作线程结束"""
        self._scan_timer.stop()
        for worker in [self._connect_worker, self._restart_worker,
                       self._reboot_worker]:
            if worker and worker.isRunning():
                worker.wait(3000)
        if self.subPageWidget:
            self.subPageWidget.shutdown()
