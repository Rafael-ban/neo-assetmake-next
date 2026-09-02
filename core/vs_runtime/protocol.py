"""VapourSynth worker 的长度前缀 JSON 协议。"""

from __future__ import annotations

import json
import math
import struct
from typing import Any, BinaryIO


HEADER_BYTES = 4
MAX_MESSAGE_BYTES = 4 * 1024 * 1024
MAX_JSON_DEPTH = 128


class ProtocolError(ValueError):
    """协议帧或消息 envelope 不合法。"""

    def __init__(self, message: str, *, code: str) -> None:
        self.code = code
        super().__init__(message)


def write_all(stream: BinaryIO, payload: bytes) -> None:
    """把完整 payload 写入可能发生 short write 的原始二进制流。"""
    view = memoryview(payload)
    offset = 0
    while offset < len(view):
        written = stream.write(view[offset:])
        remaining = len(view) - offset
        if type(written) is not int or not 1 <= written <= remaining:
            raise OSError("协议流写入未取得有效进展")
        offset += written


def _validate_envelope(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError(
            "协议消息顶层必须是 JSON object",
            code="protocol.object_required",
        )
    if type(value.get("type")) is not str:
        raise ProtocolError(
            "协议消息 type 必须是 string",
            code="protocol.type_required",
        )
    if "request_id" in value and (
        type(value["request_id"]) is not int or value["request_id"] <= 0
    ):
        raise ProtocolError(
            "协议消息 request_id 必须是严格正整数",
            code="protocol.invalid_request_id",
        )
    return value


def _validate_json_values(root: Any) -> None:
    """拒绝 json.loads 会接受、但 strict UTF-8 JSON wire 不可往返的值。"""
    stack = [(root, 0)]
    containers: set[int] = set()
    while stack:
        value, depth = stack.pop()
        if depth > MAX_JSON_DEPTH:
            raise ProtocolError(
                "协议 JSON 嵌套过深", code="protocol.invalid_json"
            )
        if value is None or type(value) in (bool, int):
            continue
        if type(value) is float:
            if not math.isfinite(value):
                raise ProtocolError(
                    "协议 JSON 数值必须有限",
                    code="protocol.invalid_json",
                )
            continue
        if type(value) is str:
            try:
                value.encode("utf-8", errors="strict")
            except UnicodeEncodeError as exc:
                raise ProtocolError(
                    "协议 JSON 字符串含无效 Unicode surrogate",
                    code="protocol.invalid_utf8",
                ) from exc
            continue
        if type(value) is list:
            identity = id(value)
            if identity in containers:
                raise ProtocolError(
                    "协议 JSON 不允许循环或共享容器",
                    code="protocol.invalid_json",
                )
            containers.add(identity)
            stack.extend((item, depth + 1) for item in value)
            continue
        if type(value) is dict:
            identity = id(value)
            if identity in containers:
                raise ProtocolError(
                    "协议 JSON 不允许循环或共享容器",
                    code="protocol.invalid_json",
                )
            containers.add(identity)
            for key, item in value.items():
                if type(key) is not str:
                    raise ProtocolError(
                        "协议 JSON object key 必须是 string",
                        code="protocol.invalid_json",
                    )
                stack.append((key, depth + 1))
                stack.append((item, depth + 1))
            continue
        raise ProtocolError(
            f"协议 JSON 包含不支持的值类型: {type(value).__name__}",
            code="protocol.invalid_json",
        )


def encode_message(message: dict[str, Any]) -> bytes:
    """编码单个有界 JSON object，并添加 4-byte 大端正文长度。"""
    _validate_envelope(message)
    _validate_json_values(message)
    try:
        body = json.dumps(
            message,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ProtocolError(
            f"协议消息无法编码为 UTF-8 JSON: {exc}",
            code="protocol.encode_failed",
        ) from exc
    if not 1 <= len(body) <= MAX_MESSAGE_BYTES:
        raise ProtocolError(
            f"协议正文大小必须为 1..{MAX_MESSAGE_BYTES} bytes",
            code="protocol.invalid_length",
        )
    return struct.pack(">I", len(body)) + body


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"JSON 常量不合法: {value}")


def _decode_body(body: bytes) -> dict[str, Any]:
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ProtocolError(
            "协议正文不是合法 UTF-8",
            code="protocol.invalid_utf8",
        ) from exc
    try:
        value = json.loads(text, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise ProtocolError(
            f"协议正文不是合法 JSON: {exc}",
            code="protocol.invalid_json",
        ) from exc
    result = _validate_envelope(value)
    _validate_json_values(result)
    return result


class MessageDecoder:
    """可接收任意拆包或粘包边界的增量协议解码器。"""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._body_length: int | None = None

    def feed(
        self, chunk: bytes | bytearray | memoryview
    ) -> list[dict[str, Any]]:
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError("协议输入必须是 bytes-like")
        self._buffer.extend(chunk)
        messages: list[dict[str, Any]] = []
        try:
            while True:
                if self._body_length is None:
                    if len(self._buffer) < HEADER_BYTES:
                        break
                    self._body_length = struct.unpack(">I", self._buffer[:4])[0]
                    del self._buffer[:4]
                    if not 1 <= self._body_length <= MAX_MESSAGE_BYTES:
                        raise ProtocolError(
                            "协议正文长度超出允许范围",
                            code="protocol.invalid_length",
                        )
                if len(self._buffer) < self._body_length:
                    break
                body = bytes(self._buffer[: self._body_length])
                del self._buffer[: self._body_length]
                self._body_length = None
                messages.append(_decode_body(body))
        except BaseException:
            self._buffer.clear()
            self._body_length = None
            raise
        return messages

    def finish(self) -> None:
        """在流 EOF 时拒绝未完成 header/body，避免静默吞掉截断消息。"""
        if self._buffer or self._body_length is not None:
            self._buffer.clear()
            self._body_length = None
            raise ProtocolError(
                "协议流在消息完成前结束",
                code="protocol.truncated_message",
            )


__all__ = [
    "HEADER_BYTES",
    "MAX_MESSAGE_BYTES",
    "MessageDecoder",
    "ProtocolError",
    "encode_message",
    "write_all",
]
