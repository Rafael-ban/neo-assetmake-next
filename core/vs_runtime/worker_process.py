"""VapourSynth worker 的唯一子进程、PIPE 与协议传输。"""

from __future__ import annotations

import math
import os
import queue
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from core.vs_runtime.protocol import (
    MessageDecoder,
    ProtocolError,
    encode_message,
    write_all,
)
from core.vs_runtime.session import RenderSession, SessionMetadata, resolve_worker_command
from core.vs_runtime.shared_frame import FrameSlot, checked_frame_bytes
from utils.file_utils import get_app_dir


REQUEST_TYPES = frozenset(
    {
        "hello",
        "load",
        "request_frame",
        "request_plane_digest",
        "cancel_epoch",
        "unload",
        "shutdown",
    }
)
RESPONSE_TYPES = frozenset(
    {
        "ready",
        "metadata",
        "frame_ready",
        "frame_discarded",
        "plane_digest",
        "requirement_error",
        "script_error",
        "contract_error",
        "request_error",
        "log",
    }
)
FRAME_TERMINAL_TYPES = frozenset(
    {"frame_ready", "frame_discarded", "request_error"}
)
ERROR_RESPONSE_TYPES = frozenset(
    {"requirement_error", "script_error", "contract_error", "request_error"}
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ERROR_FIELDS = {
    "type",
    "request_id",
    "epoch",
    "error_type",
    "message",
    "traceback",
    "code",
    "field",
    "path",
    "expected",
    "actual",
    "hint",
}
MAX_STDERR_LINE_BYTES = 64 * 1024


class WorkerProcessError(RuntimeError):
    """worker 传输或生命周期错误。"""


class WorkerCrashedError(WorkerProcessError):
    def __init__(self, message: str, *, exit_code: int | None = None) -> None:
        self.exit_code = exit_code
        super().__init__(message)


class WorkerRequestError(WorkerProcessError):
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.error_type = str(response.get("error_type", "request_error"))
        self.code = response.get("code")
        self.field = response.get("field")
        self.path = response.get("path")
        self.expected = response.get("expected")
        self.actual = response.get("actual")
        self.hint = response.get("hint")
        super().__init__(str(response.get("message", self.error_type)))


class FrameDiscardedError(WorkerProcessError):
    """同步帧请求被取消或换代时的稳定终态。"""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.reason = str(response.get("reason", "discarded"))
        self.epoch = response.get("epoch")
        self.index = response.get("index")
        self.surface = response.get("surface")
        super().__init__(f"frame request discarded: {self.reason}")


@dataclass
class _PendingRequest:
    request_type: str
    worker_generation: int
    epoch: int | None
    slot: FrameSlot | None
    index: int | None = None
    surface: str | None = None
    mode: str | None = None


class WorkerProcess:
    """唯一拥有 Popen、binary PIPE、协议线程和帧槽状态的 transport。"""

    MAX_INFLIGHT_FRAMES = 3

    def __init__(
        self,
        *,
        app_dir: str | os.PathLike[str] | None = None,
        command: Sequence[str] | None = None,
        self_test: bool = False,
        env: dict[str, str] | None = None,
    ) -> None:
        self.app_dir = Path(
            get_app_dir() if app_dir is None else app_dir
        ).resolve()
        resolved_command = (
            list(command)
            if command is not None
            else resolve_worker_command(self.app_dir)
        )
        if not resolved_command or not Path(resolved_command[0]).is_absolute():
            raise ValueError("worker command executable 必须是绝对路径")
        self.command = resolved_command
        self.self_test = self_test
        self.env = dict(env) if env is not None else None
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._listener_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._write_queue: queue.Queue[tuple[int, bytes] | None] = queue.Queue()
        self._process: subprocess.Popen[bytes] | None = None
        self._generation = 0
        self._next_request_id = 1
        self._next_slot_generation = 1
        self._pending: dict[int, _PendingRequest] = {}
        self._frame_reservations = 0
        self._latest_frame_sequence = 0
        self._coalesced_frame: tuple[int, int, dict[str, Any]] | None = None
        self._exit_event = threading.Event()
        self._exit_code: int | None = None
        self._exit_codes: dict[int, int] = {}
        self._expected_exit = False
        self._transport_failure = ""
        self._stderr_tail: deque[str] = deque(maxlen=64)
        self._threads: list[threading.Thread] = []
        self._stdout_done = threading.Event()
        self._stderr_done = threading.Event()
        self._closed = False

    @property
    def generation(self) -> int:
        with self._state_lock:
            return self._generation

    @property
    def pid(self) -> int | None:
        with self._state_lock:
            return None if self._process is None else self._process.pid

    @property
    def alive(self) -> bool:
        with self._state_lock:
            return (
                not self._closed
                and self._process is not None
                and not self._exit_event.is_set()
                and self._process.poll() is None
            )

    @property
    def settling(self) -> bool:
        """子进程已存在、但本代 reader/waiter 尚未完成资源回收。"""
        with self._state_lock:
            return self._process is not None and not self._exit_event.is_set()

    def add_listener(self, listener: Callable[[dict[str, Any]], None]) -> None:
        if not callable(listener):
            raise TypeError("listener 必须可调用")
        with self._listener_lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[dict[str, Any]], None]) -> None:
        with self._listener_lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _emit(self, event: dict[str, Any]) -> None:
        with self._listener_lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                continue

    def start(self) -> int:
        with self._state_lock:
            if self._closed:
                raise WorkerProcessError("worker transport 已关闭")
            if self.alive:
                return self._generation
            if self._process is not None and not self._exit_event.is_set():
                raise WorkerProcessError("上一代 worker 仍在收尾")
            generation = self._generation + 1
            exit_event = threading.Event()
            write_queue: queue.Queue[tuple[int, bytes] | None] = queue.Queue()
            stdout_done = threading.Event()
            stderr_done = threading.Event()
            stderr_tail: deque[str] = deque(maxlen=64)
            command = list(self.command)
            if self.self_test:
                command.append("--self-test")
            kwargs: dict[str, Any] = {
                "cwd": str(self.app_dir),
                "env": self.env,
                "stdin": subprocess.PIPE,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "shell": False,
                "bufsize": 0,
            }
            if sys_platform_is_windows():
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            try:
                process = subprocess.Popen(command, **kwargs)
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                raise WorkerProcessError(f"worker 启动失败: {exc}") from exc
            if process.stdin is None or process.stdout is None or process.stderr is None:
                try:
                    process.kill()
                    process.wait(timeout=5)
                except (OSError, subprocess.SubprocessError):
                    pass
                raise WorkerProcessError("worker binary PIPE 创建失败")
            threads = [
                threading.Thread(
                    target=self._writer_loop,
                    args=(process, generation, write_queue),
                    name=f"VSWorkerWriter-{generation}",
                    daemon=True,
                ),
                threading.Thread(
                    target=self._stdout_loop,
                    args=(process, generation, stdout_done),
                    name=f"VSWorkerStdout-{generation}",
                    daemon=True,
                ),
                threading.Thread(
                    target=self._stderr_loop,
                    args=(process, generation, stderr_done, stderr_tail),
                    name=f"VSWorkerStderr-{generation}",
                    daemon=True,
                ),
                threading.Thread(
                    target=self._waiter_loop,
                    args=(
                        process,
                        generation,
                        stdout_done,
                        stderr_done,
                        write_queue,
                        exit_event,
                        stderr_tail,
                    ),
                    name=f"VSWorkerWaiter-{generation}",
                    daemon=True,
                ),
            ]
            # Popen 与 PIPE 均成功后才发布新代，避免一次 spawn 失败把
            # generation/exit_event 留在永远无法收尾的半启动状态。
            self._generation = generation
            self._exit_event = exit_event
            self._exit_code = None
            self._expected_exit = False
            self._transport_failure = ""
            self._stderr_tail = stderr_tail
            self._write_queue = write_queue
            self._latest_frame_sequence = 0
            self._stdout_done = stdout_done
            self._stderr_done = stderr_done
            self._process = process
            self._threads = threads
            started_threads: list[threading.Thread] = []
            try:
                for thread in self._threads:
                    thread.start()
                    started_threads.append(thread)
            except RuntimeError as exc:
                self._expected_exit = True
                self._transport_failure = f"worker 协议线程启动失败: {exc}"
                write_queue.put(None)
                try:
                    process.terminate()
                except OSError:
                    pass
                start_error = exc
            else:
                return generation

        # 线程资源耗尽等半启动失败必须在 start() 返回前彻底收束；此时
        # exit_event 仍未置位，其他 start() 会被“上一代仍在收尾”挡住。
        try:
            code = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            try:
                code = process.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                code = process.poll()
        except (OSError, subprocess.SubprocessError):
            code = process.poll()
        stdout_done.set()
        stderr_done.set()
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        for thread in started_threads:
            if thread.is_alive():
                thread.join(timeout=1)
        if type(code) is not int:
            code = -1
        with self._state_lock:
            if generation == self._generation:
                self._exit_code = code
                self._exit_codes[generation] = code
                exit_event.set()
        raise WorkerProcessError(
            f"worker 协议线程启动失败: {start_error}"
        ) from start_error

    def _writer_loop(
        self,
        process: subprocess.Popen[bytes],
        generation: int,
        write_queue: queue.Queue[tuple[int, bytes] | None],
    ) -> None:
        assert process.stdin is not None
        while True:
            item = write_queue.get()
            if item is None:
                return
            item_generation, data = item
            if item_generation != generation:
                continue
            try:
                write_all(process.stdin, data)
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                self._fail_transport(generation, f"worker stdin 写入失败: {exc}")
                return

    @staticmethod
    def _read_chunk(stream: Any) -> bytes:
        read1 = getattr(stream, "read1", None)
        if callable(read1):
            return read1(64 * 1024)
        return stream.read(64 * 1024)

    def _stdout_loop(
        self,
        process: subprocess.Popen[bytes],
        generation: int,
        done: threading.Event,
    ) -> None:
        assert process.stdout is not None
        decoder = MessageDecoder()
        try:
            while True:
                chunk = self._read_chunk(process.stdout)
                if not chunk:
                    decoder.finish()
                    return
                for message in decoder.feed(chunk):
                    self._handle_message(message, generation)
        except BaseException as exc:
            try:
                detail = str(exc)
            except BaseException:
                detail = "异常文本不可读取"
            self._fail_transport(
                generation,
                "worker stdout 协议处理失败: "
                f"{type(exc).__name__}: {detail}",
            )
        finally:
            done.set()

    def _stderr_loop(
        self,
        process: subprocess.Popen[bytes],
        generation: int,
        done: threading.Event,
        stderr_tail: deque[str],
    ) -> None:
        del generation
        assert process.stderr is not None
        buffer = b""
        try:
            while True:
                chunk = self._read_chunk(process.stderr)
                if not chunk:
                    break
                buffer += chunk
                while True:
                    newline = buffer.find(b"\n")
                    if 0 <= newline <= MAX_STDERR_LINE_BYTES:
                        line = buffer[:newline]
                        buffer = buffer[newline + 1 :]
                    elif len(buffer) > MAX_STDERR_LINE_BYTES:
                        line = buffer[:MAX_STDERR_LINE_BYTES]
                        buffer = buffer[MAX_STDERR_LINE_BYTES:]
                    else:
                        break
                    stderr_tail.append(
                        line.decode("utf-8", errors="replace")
                    )
            if buffer:
                stderr_tail.append(buffer.decode("utf-8", errors="replace"))
        except OSError as exc:
            stderr_tail.append(f"stderr read failed: {exc}")
        finally:
            done.set()

    def _waiter_loop(
        self,
        process: subprocess.Popen[bytes],
        generation: int,
        stdout_done: threading.Event,
        stderr_done: threading.Event,
        write_queue: queue.Queue[tuple[int, bytes] | None],
        exit_event: threading.Event,
        stderr_tail: deque[str],
    ) -> None:
        code = process.wait()
        # 子进程会在退出前 flush 最后一条结构化错误。必须先让 stdout reader
        # 消费到 EOF，随后才能清 pending 并合成本地 crash 事件。
        stdout_done.wait(5)
        stderr_done.wait(5)
        with self._state_lock:
            if generation != self._generation:
                write_queue.put(None)
                for stream in (process.stdin, process.stdout, process.stderr):
                    try:
                        if stream is not None:
                            stream.close()
                    except OSError:
                        pass
                exit_event.set()
                return
            expected = self._expected_exit
            failure = self._transport_failure
            stderr_lines = tuple(stderr_tail)
            pending = tuple(self._pending.values())
            self._pending.clear()
            self._frame_reservations = 0
            self._coalesced_frame = None
        for request in pending:
            if request.slot is not None:
                request.slot.close()
        write_queue.put(None)
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        detail_parts = [f"worker exit code {code}"]
        if failure:
            detail_parts.append(failure)
        if stderr_lines:
            detail_parts.append("\n".join(stderr_lines))
        with self._state_lock:
            # 旧代的清理、slot 回收与 PIPE 关闭均已完成；从这里起才允许
            # listener 或其他线程启动下一代。exit_event 必须是本代捕获值，
            # 不能在 listener 重启后再通过 self 误置新代事件。
            self._exit_code = code
            self._exit_codes[generation] = code
            exit_event.set()
        self._emit(
            {
                "type": "worker_exited" if expected and code == 0 else "worker_crashed",
                "generation": generation,
                "exit_code": code,
                "message": "; ".join(detail_parts),
            }
        )

    def _fail_transport(self, generation: int, message: str) -> None:
        with self._state_lock:
            if generation != self._generation or self._exit_event.is_set():
                return
            if not self._transport_failure:
                self._transport_failure = message
            process = self._process
        self._emit(
            {"type": "protocol_error", "generation": generation, "message": message}
        )
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def _expected_response(self, pending: _PendingRequest, response_type: str) -> bool:
        allowed = {
            "hello": {"ready", "request_error"},
            "load": {"metadata", *ERROR_RESPONSE_TYPES},
            "request_frame": set(FRAME_TERMINAL_TYPES),
            "request_plane_digest": {"plane_digest", "request_error"},
            "cancel_epoch": {"ready", "request_error"},
            "unload": {"ready", "request_error"},
            "shutdown": {"ready", "request_error"},
        }
        return response_type in allowed.get(pending.request_type, set())

    @staticmethod
    def _require_exact_fields(
        message: dict[str, Any], fields: set[str], label: str
    ) -> None:
        if set(message) != fields:
            raise ProtocolError(
                f"{label} response 字段不完整或包含未知字段",
                code="protocol.response_shape",
            )

    def _validate_response_shape(
        self,
        message: dict[str, Any],
        pending: _PendingRequest,
    ) -> None:
        response_type = message["type"]
        if response_type == "ready":
            self._require_exact_fields(
                message,
                {"type", "request_id", "api_version", "operation"},
                "ready",
            )
            expected_operation = pending.request_type
            if (
                type(message["api_version"]) is not int
                or message["api_version"] != 1
                or message["operation"] != expected_operation
            ):
                raise ProtocolError(
                    "ready operation/API 与请求不匹配",
                    code="protocol.response_identity",
                )
            return
        if response_type == "metadata":
            self._require_exact_fields(
                message,
                {"type", "request_id", "epoch", "metadata"},
                "metadata",
            )
        elif response_type == "frame_ready":
            self._require_exact_fields(
                message,
                {
                    "type",
                    "request_id",
                    "epoch",
                    "index",
                    "surface",
                    "slot_name",
                    "slot_generation",
                    "width",
                    "height",
                    "byte_count",
                },
                "frame_ready",
            )
        elif response_type == "frame_discarded":
            self._require_exact_fields(
                message,
                {
                    "type",
                    "request_id",
                    "epoch",
                    "index",
                    "surface",
                    "slot_name",
                    "slot_generation",
                    "reason",
                },
                "frame_discarded",
            )
            if type(message["reason"]) is not str or not message["reason"]:
                raise ProtocolError(
                    "frame_discarded.reason 非法",
                    code="protocol.response_shape",
                )
        elif response_type == "plane_digest":
            self._require_exact_fields(
                message,
                {"type", "request_id", "epoch", "index", "surface", "digests"},
                "plane_digest",
            )
            digests = message["digests"]
            if (
                not isinstance(digests, dict)
                or set(digests) != {"Y", "U", "V"}
                or any(
                    type(value) is not str or _SHA256_RE.fullmatch(value) is None
                    for value in digests.values()
                )
            ):
                raise ProtocolError(
                    "plane_digest.digests 非法",
                    code="protocol.response_shape",
                )
        elif response_type in ERROR_RESPONSE_TYPES:
            fields = set(_ERROR_FIELDS)
            if pending.request_type == "request_frame":
                fields.update({"slot_name", "slot_generation"})
            self._require_exact_fields(message, fields, response_type)
            for field in ("error_type", "message", "traceback"):
                if type(message[field]) is not str:
                    raise ProtocolError(
                        f"{response_type}.{field} 必须是字符串",
                        code="protocol.response_shape",
                    )
            for field in ("code", "field", "path", "hint"):
                if message[field] is not None and type(message[field]) is not str:
                    raise ProtocolError(
                        f"{response_type}.{field} 类型非法",
                        code="protocol.response_shape",
                    )
        if "epoch" in message:
            epoch = message["epoch"]
            if pending.epoch is None:
                if epoch is not None and (type(epoch) is not int or epoch <= 0):
                    raise ProtocolError(
                        "response epoch 必须是正整数或 null",
                        code="protocol.response_identity",
                    )
            elif type(epoch) is not int or epoch != pending.epoch:
                raise ProtocolError(
                    "response epoch 与请求不匹配",
                    code="protocol.response_identity",
                )
        if response_type in {"frame_ready", "frame_discarded", "plane_digest"}:
            if (
                type(message["index"]) is not int
                or message["index"] != pending.index
                or type(message["surface"]) is not str
                or message["surface"] != pending.surface
            ):
                raise ProtocolError(
                    "response frame index/surface 与请求不匹配",
                    code="protocol.response_identity",
                )

    def _validate_frame_identity(
        self, message: dict[str, Any], pending: _PendingRequest
    ) -> None:
        assert pending.slot is not None
        descriptor = pending.slot.descriptor
        expected = (pending.epoch, descriptor.name, descriptor.generation)
        actual = (
            message.get("epoch"),
            message.get("slot_name"),
            message.get("slot_generation"),
        )
        if (
            type(message.get("epoch")) is not int
            or type(message.get("slot_name")) is not str
            or type(message.get("slot_generation")) is not int
            or actual != expected
        ):
            raise ProtocolError(
                f"frame terminal identity 不匹配: {actual!r} != {expected!r}",
                code="protocol.frame_identity",
            )

    def _handle_message(self, message: dict[str, Any], generation: int) -> None:
        response_type = message["type"]
        if response_type not in RESPONSE_TYPES:
            raise ProtocolError(
                f"worker response type 非法: {response_type!r}",
                code="protocol.response_type",
            )
        if response_type == "log":
            if (
                set(message) != {"type", "level", "message"}
                or type(message.get("level")) is not str
                or type(message.get("message")) is not str
            ):
                raise ProtocolError(
                    "log response 字段非法", code="protocol.response_shape"
                )
            event = dict(message)
            event["generation"] = generation
            self._emit(event)
            return
        request_id = message.get("request_id")
        with self._state_lock:
            if generation != self._generation:
                return
            pending = self._pending.get(request_id)
        if pending is None or pending.worker_generation != generation:
            raise ProtocolError(
                f"worker response 引用了未知 request_id: {request_id!r}",
                code="protocol.unknown_request",
            )
        if not self._expected_response(pending, response_type):
            raise ProtocolError(
                f"{pending.request_type} 收到非法响应 {response_type}",
                code="protocol.unexpected_response",
            )
        self._validate_response_shape(message, pending)

        event = dict(message)
        if pending.request_type == "request_frame":
            self._validate_frame_identity(message, pending)
            assert pending.slot is not None
            if response_type == "frame_ready":
                width = message.get("width")
                height = message.get("height")
                byte_count = message.get("byte_count")
                if type(width) is not int or type(height) is not int:
                    raise ProtocolError(
                        "frame dimensions 必须是严格整数",
                        code="protocol.frame_shape",
                    )
                try:
                    expected_bytes = checked_frame_bytes(width, height)
                except ValueError as exc:
                    raise ProtocolError(
                        str(exc), code="protocol.frame_shape"
                    ) from exc
                if (
                    type(byte_count) is not int
                    or byte_count != expected_bytes
                    or byte_count > pending.slot.descriptor.capacity
                ):
                    raise ProtocolError(
                        "frame byte_count/capacity 不匹配",
                        code="protocol.frame_byte_count",
                    )
                event["frame"] = pending.slot.read_bgr(
                    width=width,
                    height=height,
                    byte_count=byte_count,
                )
        elif response_type == "metadata":
            if "metadata" not in message:
                raise ProtocolError(
                    "metadata response 缺少 metadata",
                    code="protocol.response_shape",
                )
            metadata = SessionMetadata.from_wire(message["metadata"])
            if metadata.epoch != pending.epoch or metadata.mode != pending.mode:
                raise ProtocolError(
                    "nested metadata identity 与 load 请求不匹配",
                    code="protocol.response_identity",
                )
            event["metadata"] = metadata

        with self._state_lock:
            current = self._pending.get(request_id)
            if current is not pending:
                raise ProtocolError(
                    "request 已收到重复 terminal response",
                    code="protocol.duplicate_terminal",
                )
            self._pending.pop(request_id)
            coalesced = None
            if pending.request_type == "request_frame":
                coalesced = self._coalesced_frame
                self._coalesced_frame = None
        if pending.slot is not None:
            pending.slot.close()
        event["generation"] = generation
        self._emit(event)
        if (
            coalesced is not None
            and coalesced[0] == generation
            and self.generation == generation
            and self.alive
        ):
            submitted_id = self.request_frame(
                **coalesced[2],
                _sequence=coalesced[1],
                _worker_generation=coalesced[0],
            )
            if submitted_id is not None:
                self._emit(
                    {
                        "type": "frame_submitted",
                        "request_id": submitted_id,
                        "epoch": coalesced[2]["epoch"],
                        "index": coalesced[2]["index"],
                        "surface": coalesced[2]["surface"],
                        "generation": generation,
                    }
                )

    def _allocate_request_id(self) -> int:
        with self._state_lock:
            request_id = self._next_request_id
            self._next_request_id += 1
            return request_id

    def send_request(self, message: dict[str, Any]) -> int:
        request_type = message.get("type")
        if request_type not in REQUEST_TYPES or request_type == "request_frame":
            raise ValueError("请使用受支持的非帧 request type")
        with self._state_lock:
            if not self.alive:
                raise WorkerProcessError("worker 未运行")
            generation = self._generation
            write_queue = self._write_queue
            request_id = self._next_request_id
            self._next_request_id += 1
        payload = dict(message)
        payload["request_id"] = request_id
        encoded = encode_message(payload)
        epoch = payload.get("epoch")
        with self._state_lock:
            if generation != self._generation or not self.alive:
                raise WorkerProcessError("worker 已在请求编码期间退出或换代")
            self._pending[request_id] = _PendingRequest(
                request_type=request_type,
                worker_generation=generation,
                epoch=epoch if type(epoch) is int else None,
                slot=None,
                index=(
                    payload.get("index")
                    if type(payload.get("index")) is int
                    else None
                ),
                surface=(
                    payload.get("surface")
                    if type(payload.get("surface")) is str
                    else None
                ),
                mode=(
                    payload.get("mode")
                    if type(payload.get("mode")) is str
                    else None
                ),
            )
            if request_type == "shutdown":
                self._expected_exit = True
            # Queue.put() is unbounded and non-blocking. Keep admission and
            # publication in one generation-locked commit so waiter cannot
            # clear pending/slots and publish its sentinel in between.
            write_queue.put((generation, encoded))
        return request_id

    def request_frame(
        self,
        *,
        epoch: int,
        index: int,
        surface: str,
        viewport: tuple[int, int],
        zoom_factor: float,
        pan: tuple[float, float],
        coalesce: bool = False,
        _sequence: int | None = None,
        _worker_generation: int | None = None,
    ) -> int | None:
        if type(epoch) is not int or epoch <= 0:
            raise ValueError("epoch 必须是严格正整数")
        if type(index) is not int or index < 0:
            raise ValueError("index 必须是严格非负整数")
        if type(surface) is not str or surface not in ("final", "editor"):
            raise ValueError("surface 必须是 final/editor")
        if not isinstance(viewport, (tuple, list)) or len(viewport) != 2:
            raise ValueError("viewport 必须是 (width, height)")
        capacity = checked_frame_bytes(viewport[0], viewport[1])
        viewport = (viewport[0], viewport[1])
        if (
            isinstance(zoom_factor, bool)
            or not isinstance(zoom_factor, (int, float))
            or not math.isfinite(zoom_factor)
            or not 0.01 <= zoom_factor <= 100.0
        ):
            raise ValueError("zoom_factor 必须满足 0.01 <= value <= 100.0")
        zoom_factor = float(zoom_factor)
        if not isinstance(pan, (tuple, list)) or len(pan) != 2:
            raise ValueError("pan 必须是 (x, y)")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0.0 <= value <= 1.0
            for value in pan
        ):
            raise ValueError("pan.x/pan.y 必须满足 0.0 <= value <= 1.0")
        pan = (float(pan[0]), float(pan[1]))
        if type(coalesce) is not bool:
            raise ValueError("coalesce 必须是 bool")
        if _sequence is not None and (
            type(_sequence) is not int or _sequence <= 0
        ):
            raise ValueError("_sequence 必须是严格正整数")
        fields = {
            "epoch": epoch,
            "index": index,
            "surface": surface,
            "viewport": viewport,
            "zoom_factor": zoom_factor,
            "pan": pan,
            "coalesce": coalesce,
        }
        with self._state_lock:
            if not self.alive:
                raise WorkerProcessError("worker 未运行")
            generation = self._generation
            if (
                _worker_generation is not None
                and _worker_generation != generation
            ):
                return None
            write_queue = self._write_queue
            inflight = sum(
                pending.request_type == "request_frame"
                for pending in self._pending.values()
            )
            if _sequence is None:
                sequence = self._latest_frame_sequence + 1
            else:
                sequence = _sequence
                if sequence != self._latest_frame_sequence:
                    return None
            if inflight + self._frame_reservations >= self.MAX_INFLIGHT_FRAMES:
                if coalesce:
                    if _sequence is None:
                        self._latest_frame_sequence = sequence
                    self._coalesced_frame = (generation, sequence, fields)
                    return None
                raise WorkerProcessError("已达到 3 个 in-flight frame 上限")
            if _sequence is None:
                self._latest_frame_sequence = sequence
            self._frame_reservations += 1
            slot_generation = self._next_slot_generation
            self._next_slot_generation += 1
        reservation_active = True
        slot: FrameSlot | None = None
        stale_submission = False
        try:
            slot = FrameSlot.create(
                capacity=capacity,
                generation=slot_generation,
            )
            request_id = self._allocate_request_id()
            payload = {
                "type": "request_frame",
                "request_id": request_id,
                "epoch": epoch,
                "index": index,
                "surface": surface,
                "slot": slot.descriptor.to_wire(),
                "display": {
                    "viewport": list(viewport),
                    "zoom_factor": zoom_factor,
                    "pan": list(pan),
                },
            }
            encoded = encode_message(payload)
            with self._state_lock:
                if generation != self._generation or not self.alive:
                    raise WorkerProcessError(
                        "worker 已在帧请求准备期间退出或换代"
                    )
                if (
                    _sequence is not None
                    and _sequence != self._latest_frame_sequence
                ):
                    self._frame_reservations -= 1
                    reservation_active = False
                    stale_submission = True
                else:
                    self._pending[request_id] = _PendingRequest(
                        request_type="request_frame",
                        worker_generation=generation,
                        epoch=epoch,
                        slot=slot,
                        index=index,
                        surface=surface,
                    )
                    self._frame_reservations -= 1
                    reservation_active = False
                    write_queue.put((generation, encoded))
        except BaseException:
            with self._state_lock:
                if (
                    reservation_active
                    and generation == self._generation
                    and self._frame_reservations > 0
                ):
                    self._frame_reservations -= 1
            if slot is not None:
                slot.close()
            raise
        if stale_submission:
            assert slot is not None
            slot.close()
            return None
        return request_id

    def cancel_epoch(self, epoch: int) -> int:
        with self._state_lock:
            # 同时作废已经从 coalesced 缓存取到 reader 局部变量、但尚未
            # commit 到 writer queue 的旧自动请求。
            self._latest_frame_sequence += 1
            if self._coalesced_frame is not None and (
                self._coalesced_frame[2]["epoch"] == epoch
            ):
                self._coalesced_frame = None
        return self.send_request({"type": "cancel_epoch", "epoch": epoch})

    def pending_frame_count(self) -> int:
        with self._state_lock:
            return sum(
                pending.request_type == "request_frame"
                for pending in self._pending.values()
            )

    def wait(self, *, timeout_ms: int) -> int:
        if type(timeout_ms) is not int or timeout_ms < 0:
            raise ValueError("timeout_ms 必须是非负整数")
        with self._state_lock:
            generation = self._generation
            exit_event = self._exit_event
        if not exit_event.wait(timeout_ms / 1000):
            raise TimeoutError("等待 worker 退出超时")
        with self._state_lock:
            code = self._exit_codes.get(generation)
        if code is None:
            raise WorkerProcessError("worker 退出状态缺失")
        return code

    def terminate(self) -> None:
        with self._state_lock:
            process = self._process
            self._expected_exit = True
        if process is not None and process.poll() is None:
            process.terminate()

    def kill(self) -> None:
        with self._state_lock:
            process = self._process
            self._expected_exit = True
        if process is not None and process.poll() is None:
            process.kill()

    def close(self) -> None:
        with self._state_lock:
            self._closed = True
            process = self._process
            exit_event = self._exit_event
            if process is not None:
                self._expected_exit = True
        if process is None:
            return
        if process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        if not exit_event.wait(1):
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
            exit_event.wait(1)


def sys_platform_is_windows() -> bool:
    return os.name == "nt"


class SyncVSWorkerProcess:
    """在同一个 WorkerProcess 上增加 queue 等待；不启动第二种 child。"""

    def __init__(
        self,
        *,
        app_dir: str | os.PathLike[str] | None = None,
        command: Sequence[str] | None = None,
        self_test: bool = False,
        env: dict[str, str] | None = None,
    ) -> None:
        self.transport = WorkerProcess(
            app_dir=app_dir,
            command=command,
            self_test=self_test,
            env=env,
        )
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._backlog: dict[int, dict[str, Any]] = {}
        self.logs: list[tuple[str, str]] = []
        self.transport.add_listener(self._events.put)

    @property
    def pid(self) -> int | None:
        return self.transport.pid

    @property
    def generation(self) -> int:
        return self.transport.generation

    def _consume_event(self, event: dict[str, Any]) -> None:
        if event["type"] == "log":
            self.logs.append((event["level"], event["message"]))
            return
        if event["type"] == "frame_submitted":
            return
        request_id = event.get("request_id")
        if type(request_id) is int:
            self._backlog[request_id] = event

    def _wait_for(self, request_id: int, timeout_ms: int) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            event = self._backlog.pop(request_id, None)
            if event is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"request {request_id} 等待响应超时")
                try:
                    event = self._events.get(timeout=remaining)
                except queue.Empty as exc:
                    raise TimeoutError(
                        f"request {request_id} 等待响应超时"
                    ) from exc
            event_type = event["type"]
            if event.get("generation") != self.transport.generation:
                continue
            if event_type == "log":
                self.logs.append((event["level"], event["message"]))
                continue
            if event_type == "frame_submitted":
                continue
            if event_type in (
                "worker_crashed",
                "worker_exited",
                "protocol_error",
            ):
                raise WorkerCrashedError(
                    event["message"], exit_code=event.get("exit_code")
                )
            if event.get("request_id") != request_id:
                self._consume_event(event)
                continue
            if event_type in ERROR_RESPONSE_TYPES:
                raise WorkerRequestError(event)
            return event

    def start(self, *, timeout_ms: int = 15_000) -> dict[str, Any]:
        self.transport.start()
        request_id = self.transport.send_request(
            {"type": "hello", "api_version": 1}
        )
        return self._wait_for(request_id, timeout_ms)

    def load(
        self, session: RenderSession, *, timeout_ms: int = 30_000
    ) -> SessionMetadata:
        request_id = self.transport.send_request(session.to_load_message(1))
        response = self._wait_for(request_id, timeout_ms)
        return response["metadata"]

    def request_frame(
        self,
        *,
        epoch: int,
        index: int,
        surface: str,
        viewport: tuple[int, int],
        zoom_factor: float,
        pan: tuple[float, float],
        timeout_ms: int = 10_000,
    ) -> np.ndarray:
        request_id = self.transport.request_frame(
            epoch=epoch,
            index=index,
            surface=surface,
            viewport=viewport,
            zoom_factor=zoom_factor,
            pan=pan,
        )
        assert request_id is not None
        response = self._wait_for(request_id, timeout_ms)
        if response["type"] == "frame_discarded":
            raise FrameDiscardedError(response)
        return response["frame"]

    def request_plane_digest(
        self,
        *,
        epoch: int,
        index: int,
        surface: str = "final",
        timeout_ms: int = 10_000,
    ) -> dict[str, str]:
        request_id = self.transport.send_request(
            {
                "type": "request_plane_digest",
                "epoch": epoch,
                "index": index,
                "surface": surface,
            }
        )
        return self._wait_for(request_id, timeout_ms)["digests"]

    def cancel_epoch(self, epoch: int, *, timeout_ms: int = 10_000) -> None:
        request_id = self.transport.cancel_epoch(epoch)
        self._wait_for(request_id, timeout_ms)

    def unload(self, *, timeout_ms: int = 10_000) -> None:
        request_id = self.transport.send_request({"type": "unload"})
        self._wait_for(request_id, timeout_ms)

    def shutdown(self, *, timeout_ms: int = 3_000) -> dict[str, Any]:
        request_id = self.transport.send_request({"type": "shutdown"})
        try:
            response = self._wait_for(request_id, timeout_ms)
        except TimeoutError:
            # shutdown 是唯一无需用户确认的超时：即使 child 不再读 PIPE，
            # 也必须在返回调用者前升级 terminate -> kill。
            self.transport.terminate()
            try:
                self.transport.wait(timeout_ms=max(timeout_ms, 1_000))
            except TimeoutError:
                self.transport.kill()
                self.transport.wait(timeout_ms=max(timeout_ms, 1_000))
            raise
        try:
            self.transport.wait(timeout_ms=timeout_ms)
        except TimeoutError:
            self.transport.terminate()
            try:
                self.transport.wait(timeout_ms=timeout_ms)
            except TimeoutError:
                self.transport.kill()
                self.transport.wait(timeout_ms=timeout_ms)
        return response

    def wait(self, *, timeout_ms: int) -> int:
        return self.transport.wait(timeout_ms=timeout_ms)

    def terminate_and_restart(
        self, *, timeout_ms: int = 3_000
    ) -> dict[str, Any]:
        self.transport.terminate()
        try:
            self.transport.wait(timeout_ms=timeout_ms)
        except TimeoutError:
            self.transport.kill()
            self.transport.wait(timeout_ms=timeout_ms)
        self._backlog.clear()
        while True:
            try:
                self._events.get_nowait()
            except queue.Empty:
                break
        return self.start(timeout_ms=timeout_ms)

    def close(self) -> None:
        self.transport.close()


__all__ = [
    "ERROR_RESPONSE_TYPES",
    "FrameDiscardedError",
    "FRAME_TERMINAL_TYPES",
    "REQUEST_TYPES",
    "RESPONSE_TYPES",
    "SyncVSWorkerProcess",
    "WorkerCrashedError",
    "WorkerProcess",
    "WorkerProcessError",
    "WorkerRequestError",
]
