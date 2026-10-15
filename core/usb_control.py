from __future__ import annotations
from typing import Callable, Optional
import argparse
import ctypes.util as _ctu
import os
import struct
import sys
import threading
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# cx_freeze 打包后 libusb-1.0.dll 放在 exe 同目录下，但 ctypes.util.find_library
# 在 Windows 上不搜索该目录。在 pyusb 首次 import 前劫持 find_library，让它在
# exe 所在目录中也能找到 DLL。
# ---------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    _FROZEN_DLL_DIR = os.path.dirname(sys.executable)
    _orig_find_library = _ctu.find_library

    def _find_library_in_exe_dir(name: str) -> str | None:
        """优先在 exe 目录中查找 DLL，回退到系统默认搜索。"""
        dll_name = name if name.endswith(".dll") else name + ".dll"
        dll_path = os.path.join(_FROZEN_DLL_DIR, dll_name)
        if os.path.isfile(dll_path):
            return dll_path
        return _orig_find_library(name)

    _ctu.find_library = _find_library_in_exe_dir

import usb.core
import usb.util

import core.protocol as P
from PyQt6.QtCore import (QObject, pyqtSignal)
import logging
logger = logging.getLogger(__name__)

MAX_PAYLOAD = 8 * 1024 * 1024
USB_REQUEST_CHUNK = 16 * 1024
DEFAULT_CHUNK = USB_REQUEST_CHUNK - 4


def _write_exact(ep, data: bytes, timeout: int) -> None:
    sent = 0
    while sent < len(data):
        n = ep.write(data[sent: sent + USB_REQUEST_CHUNK], timeout=timeout)
        if n <= 0:
            raise IOError("USB write failed")
        sent += n


def struct_pack_u32(v: int) -> bytes:
    return v.to_bytes(4, "little")


def struct_unpack_header(hdr: bytes) -> Tuple[int, int, int, int, int, int, int]:
    return struct.unpack("<IHHIIII", hdr)


class UsbResponderClient:
    def __init__(
            self,
            vid: int,
            pid: int,
            *,
            bus: Optional[int] = None,
            address: Optional[int] = None,
            interface: int = 0,
            timeout_ms: int = 300_000,
            disconnect_callback=None,
    ) -> None:
        self._disconnect_callback = disconnect_callback
        self._dev = usb.core.find(
            idVendor=vid,
            idProduct=pid,
            custom_match=lambda d:
            (bus is None or d.bus == bus) and
                (address is None or d.address == address)
        )
        if self._dev is None:
            raise RuntimeError(
                f"未找到设备 vid=0x{vid:04x} pid=0x{pid:04x}"
            )
        self._timeout = timeout_ms
        self._iface = interface
        try:
            if self._dev.is_kernel_driver_active(interface):
                self._dev.detach_kernel_driver(interface)
        except (NotImplementedError, ValueError, usb.core.USBError):
            pass
        self._dev.set_configuration()
        cfg = self._dev.get_active_configuration()
        intf = usb.util.find_descriptor(cfg, bInterfaceNumber=interface)
        if intf is None:
            raise RuntimeError(f"无接口 {interface}")
        self._ep_in = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(
                e.bEndpointAddress) == usb.util.ENDPOINT_IN,
        )
        self._ep_out = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(
                e.bEndpointAddress) == usb.util.ENDPOINT_OUT,
        )
        if self._ep_in is None or self._ep_out is None:
            raise RuntimeError("未找到 bulk IN/OUT 端点（期望 0x81 / 0x02）")
        self._req = 0
        self._rxbuf = bytearray()
        self._rx_read_size = USB_REQUEST_CHUNK
        # Serialises whole request->response transactions. The bulk endpoints,
        # _rxbuf framing buffer and _req id counter are shared mutable state, and
        # several QThread workers drive the same client concurrently; Qt provides
        # no implicit synchronisation, so without this lock frames interleave and
        # request_id matching corrupts (the file-manager crash / info.err class).
        self._lock = threading.Lock()

    def close(self) -> None:
        usb.util.dispose_resources(self._dev)

    def _next_id(self) -> int:
        self._req = (self._req + 1) & 0xFFFFFFFF
        if self._req == 0:
            self._req = 1
        return self._req

    def _send_frame(self, typ: int, payload: bytes = b"", req_id: Optional[int] = None) -> int:
        rid = self._next_id() if req_id is None else req_id
        raw = P.Frame(type=typ, request_id=rid, payload=payload).encode()
        _write_exact(self._ep_out, raw, self._timeout)
        # 当帧总长为端点最大包大小的整数倍时，需补发零长包(ZLP)通知设备传输结束
        if len(raw) % self._ep_out.wMaxPacketSize == 0:
            try:
                self._ep_out.write(b"", timeout=self._timeout)
            except Exception:
                pass
        return rid

    def _recv_frame(self) -> P.Frame:
        # _read_some 内部已用 wMaxPacketSize 对齐，每次读自然以 short packet
        # 或收满请求量终止，不依赖设备端 ZLP。此处只需按需补足缓冲区。
        while len(self._rxbuf) < P.HEADER_SIZE:
            self._read_some(P.HEADER_SIZE - len(self._rxbuf))
        hdr = bytes(self._rxbuf[: P.HEADER_SIZE])
        _magic, _ver, _typ, _fl, _rid, plen, _crc = struct_unpack_header(hdr)
        if plen > MAX_PAYLOAD:
            raise ValueError("payload too large")
        frame_size = P.HEADER_SIZE + plen
        while len(self._rxbuf) < frame_size:
            self._read_some(frame_size - len(self._rxbuf))
        raw = bytes(self._rxbuf[:frame_size])
        del self._rxbuf[:frame_size]
        return P.decode_frame(raw)

    def _read_some(self, size: int) -> None:
        # 用端点 wMaxPacketSize 作为最小读取单元：
        # - 避免 read(size) 过小导致 libUSB OVERFLOW（UDC 驱动不支持拆分
        #   FunctionFS 事件到多个 URB）
        # - 每次 read 要么收到 short packet 自然终止，要么收满请求量按 USB
        #   规范终止，全程不依赖设备端 ZLP
        # - FunctionFS 残留的 ZLP 事件（0 字节）被循环吞掉
        mps = self._ep_in.wMaxPacketSize
        while True:
            chunk = self._ep_in.read(max(size, mps), timeout=self._timeout)
            if chunk is None or len(chunk) == 0:
                # ZLP 残留：忽略，继续尝试读下一笔数据
                continue
            self._rxbuf.extend(bytes(chunk))
            return

    def _expect_kv(self, req_id: int) -> Dict[str, str]:
        fr = self._recv_frame()
        if fr.request_id != req_id:
            raise RuntimeError(
                f"request_id 不匹配: 期望 {req_id} 收到 {fr.request_id}")
        if fr.type == P.MSG_ERROR:
            kv = P.decode_kv(fr.payload)
            raise RuntimeError(kv.get("message", "ERROR"))
        if fr.type != P.MSG_STATUS:
            raise RuntimeError(f"意外消息类型 {fr.type}，期望 STATUS")
        return P.decode_kv(fr.payload)

    def hello(self) -> Dict[str, str]:
        try:
            with self._lock:
                rid = self._send_frame(P.MSG_HELLO)
                return self._expect_kv(rid)
        except Exception as e:
            self._notify_disconnect(e)
            raise

    def file_put(
            self,
            local_path: str,
            remote_path: str,
            chunk_size: int = DEFAULT_CHUNK,
            desire_storage: Optional[str] = None,
            perm: Optional[str] = None,
    ) -> None:
        try:
            with self._lock:
                rid = self._next_id()
                items: List[Tuple[str, str]] = [("path", remote_path)]
                if desire_storage:
                    items.append(("desire_storage", desire_storage))
                if perm:
                    items.append(("perm", perm))
                begin = P.encode_kv(items)
                self._send_frame(P.MSG_FILE_PUT_BEGIN, begin, req_id=rid)
                self._expect_kv(rid)

                with open(local_path, "rb") as f:
                    while True:
                        piece = f.read(chunk_size)
                        if not piece:
                            break
                        chunk_payload = struct_pack_u32(rid) + piece
                        self._send_frame(P.MSG_FILE_PUT_CHUNK,
                                         chunk_payload, req_id=rid)
                        self._expect_kv(rid)

                self._send_frame(P.MSG_FILE_PUT_END,
                                 struct_pack_u32(rid), req_id=rid)
                self._expect_kv(rid)
        except Exception as e:
            self._notify_disconnect(e)
            raise

    def file_get(self, remote_path: str, local_path: str) -> None:
        try:
            with self._lock:
                rid = self._send_frame(
                    P.MSG_FILE_GET, P.encode_kv([("path", remote_path)]))
                fr = self._recv_frame()
                if fr.request_id != rid:
                    raise RuntimeError(
                        f"request_id 不匹配: 期望 {rid} 收到 {fr.request_id}")
                if fr.type == P.MSG_ERROR:
                    kv = P.decode_kv(fr.payload)
                    raise RuntimeError(kv.get("message", "ERROR"))
                if fr.type != P.MSG_FILE_GET:
                    raise RuntimeError(f"意外类型 {fr.type}，期望 FILE_GET")
                with open(local_path, "wb") as out:
                    out.write(fr.payload)
        except Exception as e:
            self._notify_disconnect(e)
            raise

    def file_list(self, path: str = ".") -> Tuple[List[str], List[str]]:
        try:
            with self._lock:
                rid = self._send_frame(
                    P.MSG_FILE_LIST, P.encode_kv([("path", path)]))
                kv = self._expect_kv(rid)
                files_raw = kv.get("files", "")
                dirs_raw = kv.get("dirs", "")
                files = [line for line in files_raw.splitlines() if line]
                dirs = [line for line in dirs_raw.splitlines() if line]
                return files, dirs
        except Exception as e:
            self._notify_disconnect(e)
            raise

    def file_stat(self, path: str) -> Dict[str, str]:
        try:
            with self._lock:
                rid = self._send_frame(
                    P.MSG_FILE_STAT, P.encode_kv([("path", path)]))
                return self._expect_kv(rid)
        except Exception as e:
            self._notify_disconnect(e)
            raise

    def file_delete(self, remote_path: str, desire_storage: Optional[str] = None) -> None:
        try:
            with self._lock:
                items: List[Tuple[str, str]] = [("path", remote_path)]
                if desire_storage:
                    items.append(("desire_storage", desire_storage))
                rid = self._send_frame(P.MSG_FILE_DELETE, P.encode_kv(items))
                self._expect_kv(rid)
        except Exception as e:
            self._notify_disconnect(e)
            raise

    def file_rename(self, src: str, dst: str, desire_storage: Optional[str] = None) -> None:
        try:
            with self._lock:
                items: List[Tuple[str, str]] = [("from", src), ("to", dst)]
                if desire_storage:
                    items.append(("desire_storage", desire_storage))
                rid = self._send_frame(P.MSG_FILE_RENAME, P.encode_kv(items))
                self._expect_kv(rid)
        except Exception as e:
            self._notify_disconnect(e)
            raise

    def dir_mkdir(self, path: str, parents: bool = False, desire_storage: Optional[str] = None) -> None:
        try:
            with self._lock:
                items: List[Tuple[str, str]] = [("path", path)]
                if parents:
                    items.append(("parents", "1"))
                if desire_storage:
                    items.append(("desire_storage", desire_storage))
                rid = self._send_frame(P.MSG_FILE_MKDIR, P.encode_kv(items))
                self._expect_kv(rid)
        except Exception as e:
            self._notify_disconnect(e)
            raise

    def devinfo(self) -> Dict[str, str]:
        try:
            with self._lock:
                rid = self._send_frame(P.MSG_DEVINFO)
                fr = self._recv_frame()
                if fr.request_id != rid:
                    raise RuntimeError(
                        f"request_id 不匹配: 期望 {rid} 收到 {fr.request_id}")
                if fr.type == P.MSG_ERROR:
                    kv = P.decode_kv(fr.payload)
                    raise RuntimeError(kv.get("message", "ERROR"))
                if fr.type != P.MSG_DEVINFO:
                    raise RuntimeError(f"意外类型 {fr.type}，期望 DEVINFO")
                return P.decode_kv(fr.payload)
        except Exception as e:
            self._notify_disconnect(e)
            raise

    def command_exec(
            self,
            command: str,
            *,
            timeout_ms: int = 0,
            max_stdout: int = 0,
            max_stderr: int = 0,
    ) -> P.CommandResult:
        try:
            with self._lock:
                pl = P.encode_command_exec(
                    command, timeout_ms=timeout_ms, max_stdout=max_stdout, max_stderr=max_stderr
                )
                rid = self._send_frame(P.MSG_COMMAND_EXEC, pl)
                fr = self._recv_frame()
                if fr.request_id != rid:
                    raise RuntimeError(
                        f"request_id 不匹配: 期望 {rid} 收到 {fr.request_id}")
                if fr.type == P.MSG_ERROR:
                    kv = P.decode_kv(fr.payload)
                    raise RuntimeError(kv.get("message", "ERROR"))
                if fr.type != P.MSG_COMMAND_RESULT:
                    raise RuntimeError(f"意外类型 {fr.type}，期望 COMMAND_RESULT")
                return P.decode_command_result(fr.payload)
        except Exception as e:
            self._notify_disconnect(e)
            raise

    def _notify_disconnect(self, error: Optional[BaseException] = None) -> None:
        if self._disconnect_callback:
            self._disconnect_callback(error)
