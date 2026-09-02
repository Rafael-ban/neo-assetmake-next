"""把唯一 :class:`WorkerProcess` transport 安全地包装为 Qt signals。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import QObject, QTimer, Qt, pyqtSignal, pyqtSlot

from config.vs_runtime import WorkerConfig, load_vs_runtime
from core.vs_runtime.session import RenderSession, SessionMetadata
from core.vs_runtime.worker_process import WorkerProcess


@dataclass(frozen=True)
class _TimeoutToken:
    generation: int
    request_id: int
    epoch: int | None
    kind: str
    serial: int


class VSWorkerClient(QObject):
    """不复制 Popen/PIPE 逻辑的 GUI 线程适配器。"""

    # request_id/epoch 是协议严格正整数、没有 32-bit 上限；使用 object
    # 避免 pyqtSignal(int) 在长会话中静默截断 2**31 以上的标识。
    ready = pyqtSignal()
    metadata_ready = pyqtSignal(object, object)
    frame_ready = pyqtSignal(object, object, object)
    request_failed = pyqtSignal(object, str, str)
    request_timed_out = pyqtSignal(object, object)
    worker_crashed = pyqtSignal(str)
    log_received = pyqtSignal(str, str)

    _transport_event = pyqtSignal(object)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        app_dir: str | Path | None = None,
        transport: WorkerProcess | None = None,
        worker_config: WorkerConfig | None = None,
        timer_factory: Callable[[QObject], Any] = QTimer,
    ) -> None:
        super().__init__(parent)
        self.transport = transport or WorkerProcess(app_dir=app_dir)
        self.worker_config = worker_config or load_vs_runtime().worker
        self._timer_factory = timer_factory
        self._timeouts: dict[int, tuple[Any, _TimeoutToken]] = {}
        self._timed_out: dict[int, tuple[int, int]] = {}
        self._serial = 0
        self._closed = False
        self._restart_requested = False
        self._failure_reported_generation: int | None = None
        self._restart_kill_key = -1
        self._transport_event.connect(
            self._handle_transport_event,
            Qt.ConnectionType.QueuedConnection,
        )
        self.transport.add_listener(self._relay_transport_event)

    @property
    def generation(self) -> int:
        return self.transport.generation

    @property
    def pid(self) -> int | None:
        return self.transport.pid

    def _relay_transport_event(self, event: dict[str, Any]) -> None:
        if self._closed:
            return
        try:
            self._transport_event.emit(event)
        except RuntimeError:
            # QObject 可在 reader 线程投递事件的同时被 Qt 销毁；迟到事件
            # 不应把 transport 线程变成未捕获异常来源。
            return

    def _schedule_timeout(
        self,
        request_id: int,
        epoch: int | None,
        kind: str,
        timeout_ms: int,
        *,
        generation: int | None = None,
    ) -> None:
        self._clear_timeout(request_id)
        self._serial += 1
        token = _TimeoutToken(
            generation=(
                self.transport.generation
                if generation is None
                else generation
            ),
            request_id=request_id,
            epoch=epoch,
            kind=kind,
            serial=self._serial,
        )
        timer = self._timer_factory(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda current=token: self._timeout_fired(current))
        self._timeouts[request_id] = (timer, token)
        timer.start(timeout_ms)

    @staticmethod
    def _dispose_timer(timer: Any) -> None:
        timer.stop()
        timer.deleteLater()

    def _clear_timeout(self, request_id: int) -> None:
        current = self._timeouts.pop(request_id, None)
        if current is not None:
            self._dispose_timer(current[0])
        self._timed_out.pop(request_id, None)

    def _clear_all_timeouts(self) -> None:
        active = tuple(self._timeouts.values())
        self._timeouts.clear()
        self._timed_out.clear()
        for timer, _token in active:
            self._dispose_timer(timer)

    def _timeout_fired(self, token: _TimeoutToken) -> None:
        current = self._timeouts.get(token.request_id)
        if current is None or current[1] != token:
            return
        self._timeouts.pop(token.request_id, None)
        self._dispose_timer(current[0])
        if self._closed or token.generation != self.transport.generation:
            return
        if token.kind == "frame":
            assert token.epoch is not None
            self._timed_out[token.request_id] = (
                token.generation,
                token.epoch,
            )
            self.request_timed_out.emit(token.request_id, token.epoch)
            return
        if token.kind in {"startup", "load"}:
            self.request_failed.emit(
                token.request_id,
                "worker.timeout",
                "VapourSynth worker 请求超时",
            )
            return
        if token.kind in {"shutdown", "restart"}:
            self.transport.terminate()
            self._schedule_timeout(
                self._restart_kill_key,
                None,
                f"{token.kind}_kill",
                self.worker_config.shutdown_timeout_ms,
            )
            return
        if token.kind in {"shutdown_kill", "restart_kill"}:
            if self.transport.alive:
                self.transport.kill()

    def _start_transport(self) -> int:
        self.transport.start()
        self._failure_reported_generation = None
        request_id = self.transport.send_request(
            {"type": "hello", "api_version": 1}
        )
        self._schedule_timeout(
            request_id,
            None,
            "startup",
            self.worker_config.startup_timeout_ms,
        )
        return request_id

    def start(self) -> int:
        if self._closed:
            raise RuntimeError("VSWorkerClient 已关闭")
        return self._start_transport()

    def load(self, session: RenderSession) -> int:
        if not hasattr(session, "to_load_message"):
            raise TypeError("session 必须提供 to_load_message()")
        payload = session.to_load_message(1)
        request_id = self.transport.send_request(payload)
        self._schedule_timeout(
            request_id,
            payload["epoch"],
            "load",
            self.worker_config.startup_timeout_ms,
        )
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
    ) -> int | None:
        request_id = self.transport.request_frame(
            epoch=epoch,
            index=index,
            surface=surface,
            viewport=viewport,
            zoom_factor=zoom_factor,
            pan=pan,
            coalesce=coalesce,
        )
        if request_id is not None:
            self._schedule_timeout(
                request_id,
                epoch,
                "frame",
                self.worker_config.frame_timeout_ms,
            )
        return request_id

    def continue_wait(self, request_id: int) -> bool:
        timed_out = self._timed_out.pop(request_id, None)
        if timed_out is None or timed_out[0] != self.transport.generation:
            return False
        self._schedule_timeout(
            request_id,
            timed_out[1],
            "frame",
            self.worker_config.frame_timeout_ms,
        )
        return True

    def cancel_epoch(self, epoch: int) -> int:
        return self.transport.cancel_epoch(epoch)

    def unload(self) -> int:
        return self.transport.send_request({"type": "unload"})

    def shutdown(self) -> int:
        request_id = self.transport.send_request({"type": "shutdown"})
        self._schedule_timeout(
            request_id,
            None,
            "shutdown",
            self.worker_config.shutdown_timeout_ms,
        )
        return request_id

    def terminate_and_restart(self) -> None:
        if self._closed:
            return
        self._restart_requested = True
        self._clear_all_timeouts()
        if self.transport.alive:
            self.transport.terminate()
            self._schedule_timeout(
                self._restart_kill_key,
                None,
                "restart_kill",
                self.worker_config.shutdown_timeout_ms,
            )
        elif getattr(self.transport, "settling", False):
            # poll() 已观察到 child 退出不代表 reader/waiter 已关闭 PIPE、
            # 回收 slot 并发布最终代际事件；由该事件触发真正 restart。
            return
        else:
            self._restart_requested = False
            self._start_transport()

    @pyqtSlot(object)
    def _handle_transport_event(self, event: dict[str, Any]) -> None:
        if self._closed:
            return
        generation = event.get("generation")
        if type(generation) is not int or generation != self.transport.generation:
            return
        event_type = event.get("type")
        request_id = event.get("request_id")
        if event_type == "frame_submitted":
            epoch = event.get("epoch")
            if (
                type(request_id) is int
                and type(epoch) is int
                and request_id not in self._timeouts
            ):
                self._schedule_timeout(
                    request_id,
                    epoch,
                    "frame",
                    self.worker_config.frame_timeout_ms,
                    generation=generation,
                )
            return
        shutdown_ack = (
            event_type == "ready" and event.get("operation") == "shutdown"
        )
        if type(request_id) is int and not shutdown_ack:
            self._clear_timeout(request_id)
        if event_type == "ready":
            if event.get("operation") == "hello":
                self.ready.emit()
            return
        if event_type == "metadata":
            metadata = event.get("metadata")
            if not isinstance(metadata, SessionMetadata):
                self.request_failed.emit(
                    request_id,
                    "protocol.invalid_metadata",
                    "worker 返回了未解析的 metadata",
                )
                return
            self.metadata_ready.emit(metadata.epoch, metadata)
            return
        if event_type == "frame_ready":
            self.frame_ready.emit(
                event.get("epoch"), event.get("index"), event.get("frame")
            )
            return
        if event_type == "frame_discarded":
            return
        if event_type in {
            "requirement_error",
            "script_error",
            "contract_error",
            "request_error",
        }:
            code = event.get("code") or event.get("error_type") or event_type
            self.request_failed.emit(
                request_id, str(code), str(event.get("message", ""))
            )
            return
        if event_type == "log":
            self.log_received.emit(
                str(event.get("level", "info")),
                str(event.get("message", "")),
            )
            return
        if event_type == "protocol_error":
            if not self._restart_requested:
                self._clear_all_timeouts()
            if self._failure_reported_generation != generation:
                self._failure_reported_generation = generation
                self.worker_crashed.emit(
                    str(event.get("message", "worker protocol failed"))
                )
            # protocol_error 是 reader 发现损坏并请求 terminate 的预告；只有
            # waiter 的最终退出事件才能证明旧进程已经结束并允许启动新代。
            return
        if event_type in {"worker_crashed", "worker_exited"}:
            self._clear_all_timeouts()
            if self._restart_requested:
                self._restart_requested = False
                self._start_transport()
                return
            if (
                event_type == "worker_crashed"
                and self._failure_reported_generation != generation
            ):
                self._failure_reported_generation = generation
                self.worker_crashed.emit(str(event.get("message", "worker exited")))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._restart_requested = False
        self._clear_all_timeouts()
        self.transport.remove_listener(self._relay_transport_event)
        self.transport.close()


__all__ = ["VSWorkerClient"]
