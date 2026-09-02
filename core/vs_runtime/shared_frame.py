"""Windows named mmap 帧槽与连续 BGR24 传输。"""

from __future__ import annotations

import mmap
import os
import struct
import uuid
from dataclasses import dataclass
from typing import Any

import numpy as np


MAX_FRAME_BYTES = 256 * 1024 * 1024
_META_MAGIC = b"AMVSLOT1"
_META_STRUCT = struct.Struct(">8sQQ")
_FILE_MAP_ALL_ACCESS = 0x000F001F


def _positive_int(value: Any, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field} 必须是严格正整数")
    return value


def checked_frame_bytes(
    width: int,
    height: int,
    channels: int = 3,
    max_bytes: int = MAX_FRAME_BYTES,
) -> int:
    """验证尺寸和上限后返回 packed frame 所需字节数。"""
    width = _positive_int(width, "width")
    height = _positive_int(height, "height")
    channels = _positive_int(channels, "channels")
    max_bytes = _positive_int(max_bytes, "max_bytes")
    byte_count = width * height * channels
    if byte_count > max_bytes:
        raise ValueError(
            f"帧大小 {byte_count} 超过允许上限 {max_bytes} bytes"
        )
    return byte_count


@dataclass(frozen=True)
class FrameSlotDescriptor:
    name: str
    generation: int
    capacity: int

    def __post_init__(self) -> None:
        if type(self.name) is not str or not self.name or "\0" in self.name:
            raise ValueError("slot.name 必须是非空字符串")
        _positive_int(self.generation, "slot.generation")
        capacity = _positive_int(self.capacity, "slot.capacity")
        if capacity > MAX_FRAME_BYTES:
            raise ValueError("slot.capacity 超过帧槽上限")

    def to_wire(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "generation": self.generation,
            "capacity": self.capacity,
        }

    @classmethod
    def from_wire(cls, value: Any) -> "FrameSlotDescriptor":
        if not isinstance(value, dict) or set(value) != {
            "name",
            "generation",
            "capacity",
        }:
            raise ValueError("slot descriptor 字段不完整或包含未知字段")
        return cls(
            name=value["name"],
            generation=value["generation"],
            capacity=value["capacity"],
        )


class FrameSlot:
    """一个 owner 创建、peer 打开的具名匿名映射。"""

    def __init__(
        self,
        descriptor: FrameSlotDescriptor,
        mapping: mmap.mmap,
        metadata_mapping: mmap.mmap,
        *,
        owner: bool,
    ) -> None:
        self.descriptor = descriptor
        self._mapping: mmap.mmap | None = mapping
        self._metadata_mapping: mmap.mmap | None = metadata_mapping
        self.owner = owner

    @staticmethod
    def _metadata_name(name: str) -> str:
        return f"{name}-descriptor"

    @staticmethod
    def _open_named_handle(name: str) -> int:
        if os.name != "nt":
            raise OSError("named mmap 帧槽仅支持 Windows")
        import ctypes
        from ctypes import wintypes

        open_mapping = ctypes.WinDLL(
            "kernel32", use_last_error=True
        ).OpenFileMappingW
        open_mapping.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
        open_mapping.restype = wintypes.HANDLE
        handle = open_mapping(_FILE_MAP_ALL_ACCESS, False, name)
        if not handle:
            error = ctypes.get_last_error()
            raise FileNotFoundError(error, ctypes.FormatError(error), name)
        return int(handle)

    @staticmethod
    def _close_named_handle(handle: int) -> None:
        import ctypes
        from ctypes import wintypes

        close_handle = ctypes.WinDLL(
            "kernel32", use_last_error=True
        ).CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        close_handle(handle)

    @staticmethod
    def _mapping_exists(name: str) -> bool:
        try:
            handle = FrameSlot._open_named_handle(name)
        except FileNotFoundError:
            return False
        FrameSlot._close_named_handle(handle)
        return True

    @staticmethod
    def _map(name: str, capacity: int) -> mmap.mmap:
        return mmap.mmap(
            -1,
            capacity,
            tagname=name,
            access=mmap.ACCESS_WRITE,
        )

    @classmethod
    def create(
        cls,
        *,
        capacity: int,
        generation: int,
        name: str | None = None,
    ) -> "FrameSlot":
        descriptor = FrameSlotDescriptor(
            name=name
            or f"Local\\ArknightsPassMaker-VS-{os.getpid()}-{uuid.uuid4().hex}",
            generation=generation,
            capacity=capacity,
        )
        metadata_name = cls._metadata_name(descriptor.name)
        if cls._mapping_exists(descriptor.name) or cls._mapping_exists(metadata_name):
            raise FileExistsError(f"帧槽名称已存在: {descriptor.name}")
        mapping = cls._map(descriptor.name, descriptor.capacity)
        try:
            metadata = cls._map(metadata_name, _META_STRUCT.size)
            metadata.seek(0)
            metadata.write(
                _META_STRUCT.pack(
                    _META_MAGIC,
                    descriptor.generation,
                    descriptor.capacity,
                )
            )
        except BaseException:
            mapping.close()
            raise
        return cls(descriptor, mapping, metadata, owner=True)

    @classmethod
    def open(cls, descriptor: FrameSlotDescriptor | dict[str, Any]) -> "FrameSlot":
        if not isinstance(descriptor, FrameSlotDescriptor):
            descriptor = FrameSlotDescriptor.from_wire(descriptor)
        metadata_name = cls._metadata_name(descriptor.name)
        mapping_handle = cls._open_named_handle(descriptor.name)
        try:
            metadata_handle = cls._open_named_handle(metadata_name)
        except BaseException:
            cls._close_named_handle(mapping_handle)
            raise
        mapping: mmap.mmap | None = None
        metadata: mmap.mmap | None = None
        try:
            mapping = cls._map(descriptor.name, descriptor.capacity)
            metadata = cls._map(metadata_name, _META_STRUCT.size)
            metadata.seek(0)
            magic, generation, capacity = _META_STRUCT.unpack(
                metadata.read(_META_STRUCT.size)
            )
            if (
                magic != _META_MAGIC
                or generation != descriptor.generation
                or capacity != descriptor.capacity
            ):
                raise ValueError("slot descriptor 与 owner 映射元数据不一致")
            return cls(descriptor, mapping, metadata, owner=False)
        except BaseException:
            if metadata is not None:
                metadata.close()
            if mapping is not None:
                mapping.close()
            raise
        finally:
            cls._close_named_handle(metadata_handle)
            cls._close_named_handle(mapping_handle)

    @property
    def mapping(self) -> mmap.mmap:
        return self._require_open()

    @property
    def closed(self) -> bool:
        return self._mapping is None or self._mapping.closed

    def _require_open(self) -> mmap.mmap:
        if self._mapping is None or self._mapping.closed:
            raise ValueError("帧槽已关闭")
        return self._mapping

    def close(self) -> None:
        mapping, self._mapping = self._mapping, None
        metadata, self._metadata_mapping = self._metadata_mapping, None
        if mapping is not None and not mapping.closed:
            mapping.close()
        if metadata is not None and not metadata.closed:
            metadata.close()

    def write_bgr(self, frame: np.ndarray) -> int:
        if not isinstance(frame, np.ndarray):
            raise ValueError("BGR frame 必须是 numpy.ndarray")
        if frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError("BGR frame 必须是 HxWx3 uint8")
        byte_count = checked_frame_bytes(frame.shape[1], frame.shape[0])
        if byte_count > self.descriptor.capacity:
            raise ValueError("BGR frame 超过帧槽 capacity")
        packed = np.ascontiguousarray(frame)
        mapping = self._require_open()
        mapping.seek(0)
        mapping.write(packed.tobytes(order="C"))
        return byte_count

    def write_vs_rgb(self, frame: Any) -> int:
        """按每平面 stride 读取 VS RGB24，并重排为 packed BGR24。"""
        format_ = getattr(frame, "format", None)
        if (
            format_ is None
            or getattr(format_, "name", None) != "RGB24"
            or getattr(format_, "num_planes", None) != 3
        ):
            raise ValueError("VapourSynth frame 必须是 planar RGB24")
        width = _positive_int(getattr(frame, "width", None), "frame.width")
        height = _positive_int(getattr(frame, "height", None), "frame.height")
        checked_frame_bytes(width, height)
        packed = np.empty((height, width, 3), dtype=np.uint8)
        for destination, source in enumerate((2, 1, 0)):
            stride = _positive_int(frame.get_stride(source), "frame.stride")
            if stride < width:
                raise ValueError("VapourSynth plane stride 小于有效行宽")
            view = frame[source]
            array = np.asarray(view)
            if array.dtype != np.uint8:
                raise ValueError("VapourSynth RGB24 平面必须是 uint8")
            if array.ndim == 2:
                if array.shape[0] < height or array.shape[1] < width:
                    raise ValueError("VapourSynth 平面短于有效区域")
                # R73 的二维 memoryview 在 strides[0] 中保留真实行步长；
                # 只复制 [:width]，自然排除 padding，且不要求 C-contiguous。
                if array.strides[0] != stride:
                    raise ValueError("VapourSynth 平面 stride 元数据不一致")
                packed[:, :, destination] = array[:height, :width]
                continue
            plane = memoryview(view).cast("B")
            if plane.nbytes < stride * height:
                raise ValueError("VapourSynth 平面数据短于 stride * height")
            for row in range(height):
                start = row * stride
                packed[row, :, destination] = np.frombuffer(
                    plane[start : start + width], dtype=np.uint8, count=width
                )
        return self.write_bgr(packed)

    def read_bgr(
        self, *, width: int, height: int, byte_count: int
    ) -> np.ndarray:
        expected = checked_frame_bytes(width, height)
        if type(byte_count) is not int or byte_count != expected:
            raise ValueError("frame byte_count 与实际尺寸不一致")
        if byte_count > self.descriptor.capacity:
            raise ValueError("frame byte_count 超过帧槽 capacity")
        mapping = self._require_open()
        return np.frombuffer(
            mapping, dtype=np.uint8, count=byte_count
        ).reshape(height, width, 3).copy()

    def __enter__(self) -> "FrameSlot":
        self._require_open()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


__all__ = [
    "MAX_FRAME_BYTES",
    "FrameSlot",
    "FrameSlotDescriptor",
    "checked_frame_bytes",
]
