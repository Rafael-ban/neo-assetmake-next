"""用户 `.vpy` 的便携执行环境与图生命周期。"""

from __future__ import annotations

import importlib
import io
import json
import os
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

from . import AssetmakerVSError
from .job_api import runtime_python_dirs_from_env


MAX_PROTOCOL_BYTES = 4 * 1024 * 1024
MAX_LOG_BODY_BYTES = MAX_PROTOCOL_BYTES - 1


class GraphLifecycleError(AssetmakerVSError):
    """图生命周期违反 portable executor 的进程级约束。"""


@dataclass
class _GraphLease:
    script_root: Path
    _released: bool = field(default=False, init=False, repr=False)

    def release(self) -> None:
        """按 token identity 幂等释放进程级单活租约。"""
        global _active_graph_lease
        with _graph_lease_lock:
            if self._released:
                return
            if _active_graph_lease is self:
                _active_graph_lease = None
            self._released = True


_graph_lease_lock = threading.Lock()
_active_graph_lease: _GraphLease | None = None


def _acquire_graph_lease(script_root: Path) -> _GraphLease:
    global _active_graph_lease
    root = script_root.resolve()
    with _graph_lease_lock:
        active = _active_graph_lease
        if active is not None:
            raise GraphLifecycleError(
                "已有 VapourSynth 图仍处于活动状态，必须先关闭后才能加载新图",
                code="executor.graph_active",
                field="script_root",
                expected="no active graph",
                actual=str(active.script_root),
                hint="等待旧图全部帧请求终态后调用 close()，再加载新图",
            )
        lease = _GraphLease(root)
        _active_graph_lease = lease
        return lease


def helper_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def build_module_search_paths(
    *,
    script_path: str | os.PathLike[str],
    runtime_dirs: Iterable[str | os.PathLike[str]] = (),
) -> tuple[Path, ...]:
    script = Path(script_path).resolve()
    candidates = (
        script.parent,
        script.parent / "modules",
        *(Path(path).resolve() for path in runtime_dirs),
        helper_root(),
    )
    ordered: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = _path_key(path)
        if key not in seen:
            seen.add(key)
            ordered.append(path)
    return tuple(ordered)


def _is_under(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((_path_key(path), _path_key(root))) == _path_key(root)
    except (OSError, ValueError):
        return False


def _resolved_module_path(candidate: Any) -> Path | None:
    if candidate is None:
        return None
    try:
        value = os.fsdecode(candidate)
    except (TypeError, ValueError):
        return None
    if not value or value in {"built-in", "frozen"} or value.startswith("<"):
        return None
    try:
        return Path(value).resolve()
    except (OSError, TypeError, ValueError):
        return None


def _module_file_path(module: Any) -> Path | None:
    return _resolved_module_path(getattr(module, "__file__", None))


def _package_paths(module: Any) -> tuple[Path, ...]:
    package_paths = getattr(module, "__path__", ())
    if isinstance(package_paths, (str, bytes, os.PathLike)):
        candidates = (package_paths,)
    else:
        try:
            candidates = tuple(package_paths)
        except Exception:
            # 失去父包的 _NamespacePath 可能在重算时抛 KeyError；单个坏模块
            # 不应中止整个退休扫描，其 __file__ 仍会被独立识别。
            candidates = ()
    paths: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        path = _resolved_module_path(candidate)
        if path is None:
            continue
        key = _path_key(path)
        if key not in seen:
            seen.add(key)
            paths.append(path)
    return tuple(paths)


def _module_paths(module: Any) -> tuple[Path, ...]:
    """返回模块可识别的物理来源，包含无 ``__file__`` 的 namespace 包。"""
    file_path = _module_file_path(module)
    candidates = (() if file_path is None else (file_path,)) + _package_paths(module)
    return tuple(dict.fromkeys(candidates))


@dataclass(frozen=True)
class _ModuleRetirementPlan:
    modules: tuple[tuple[str, Any], ...]
    parent_bindings: tuple[tuple[Any, str, Any], ...]

    def retire(self) -> tuple[str, ...]:
        removed: list[str] = []
        for name, module in self.modules:
            if sys.modules.get(name) is module:
                sys.modules.pop(name, None)
                removed.append(name)
        for parent, attribute, child in self.parent_bindings:
            try:
                if getattr(parent, attribute, None) is child:
                    delattr(parent, attribute)
            except (AttributeError, TypeError):
                continue
        return tuple(removed)


def _capture_module_retirement(
    script_root: str | os.PathLike[str],
) -> _ModuleRetirementPlan:
    """在脚本搜索环境仍激活时冻结该图贡献的模块与父包属性。"""
    root = Path(script_root).resolve()
    helper_package = helper_root() / "assetmaker_vs"
    modules: list[tuple[str, Any]] = []
    for name, module in tuple(sys.modules.items()):
        if name == "assetmaker_vs" or name.startswith("assetmaker_vs."):
            continue
        file_path = _module_file_path(module)
        package_paths = _package_paths(module)
        all_paths = (() if file_path is None else (file_path,)) + package_paths
        if any(_is_under(path, helper_package) for path in all_paths):
            continue
        file_owned = file_path is not None and _is_under(file_path, root)
        local_package_paths = tuple(
            path for path in package_paths if _is_under(path, root)
        )
        external_paths = tuple(
            path for path in all_paths if not _is_under(path, root)
        )
        script_only_namespace = (
            file_path is None
            and bool(local_package_paths)
            and not external_paths
        )
        if file_owned or script_only_namespace:
            modules.append((name, module))

    parent_bindings: list[tuple[Any, str, Any]] = []
    for name, module in modules:
        parent_name, separator, attribute = name.rpartition(".")
        if not separator:
            continue
        parent = sys.modules.get(parent_name)
        if parent is not None and getattr(parent, attribute, None) is module:
            parent_bindings.append((parent, attribute, module))
    return _ModuleRetirementPlan(
        modules=tuple(modules),
        parent_bindings=tuple(parent_bindings),
    )


def evict_modules_under(script_root: str | os.PathLike[str]) -> tuple[str, ...]:
    """只驱逐由当前脚本根加载的模块，永不驱逐 helper 自身。"""
    return _capture_module_retirement(script_root).retire()


def _close_execution_environment(
    environment: "ExecutionEnvironment", script_root: Path
) -> tuple[str, ...]:
    retirement: _ModuleRetirementPlan | None = None
    try:
        retirement = _capture_module_retirement(script_root)
    finally:
        try:
            environment.close()
        finally:
            removed = () if retirement is None else retirement.retire()
            importlib.invalidate_caches()
    return removed


class ExecutionEnvironment:
    """保持模块搜索路径直到图及其延迟回调彻底关闭。"""

    def __init__(self, search_paths: Iterable[Path]) -> None:
        self.search_paths = tuple(Path(path).resolve() for path in search_paths)
        self._original: list[str] | None = None
        self._lock = threading.RLock()

    @property
    def active(self) -> bool:
        return self._original is not None

    def activate(self) -> None:
        with self._lock:
            if self.active:
                return
            self._original = list(sys.path)
            keys = {_path_key(path) for path in self.search_paths}
            remainder = []
            for value in sys.path:
                try:
                    key = _path_key(Path(value or os.curdir))
                except (OSError, TypeError, ValueError):
                    remainder.append(value)
                    continue
                if key not in keys:
                    remainder.append(value)
            sys.path[:] = [str(path) for path in self.search_paths] + remainder

    def close(self) -> None:
        with self._lock:
            if self._original is None:
                return
            sys.path[:] = self._original
            self._original = None


class PythonLogWriter(io.TextIOBase):
    """线程安全、逐行且按 UTF-8 边界限长的 Python stdout sink。"""

    encoding = "utf-8"

    def __init__(self, sink: Callable[[str], None]) -> None:
        super().__init__()
        if not callable(sink):
            raise TypeError("log sink must be callable")
        self._sink = sink
        self._buffer = ""
        self._lock = threading.RLock()

    @staticmethod
    def _chunks(text: str) -> list[str]:
        data = text.encode("utf-8")
        chunks: list[str] = []
        while len(data) > MAX_LOG_BODY_BYTES:
            end = MAX_LOG_BODY_BYTES
            while end > 0 and (data[end] & 0xC0) == 0x80:
                end -= 1
            chunks.append(data[:end].decode("utf-8"))
            data = data[end:]
        if data:
            chunks.append(data.decode("utf-8"))
        return chunks

    def _emit(self, text: str) -> None:
        for chunk in self._chunks(text):
            self._sink(chunk)

    def write(self, text: str) -> int:
        if not isinstance(text, str):
            text = str(text)
        with self._lock:
            self._buffer += text
            while "\n" in self._buffer:
                line, self._buffer = self._buffer.split("\n", 1)
                self._emit(line)
            if len(self._buffer.encode("utf-8")) > MAX_LOG_BODY_BYTES:
                chunks = self._chunks(self._buffer)
                self._buffer = chunks.pop() if chunks else ""
                for chunk in chunks:
                    self._sink(chunk)
        return len(text)

    def flush(self) -> None:
        with self._lock:
            if self._buffer:
                self._emit(self._buffer)
                self._buffer = ""

    def writable(self) -> bool:
        return True


def install_python_stdout(sink: Callable[[str], None]) -> PythonLogWriter:
    writer = PythonLogWriter(sink)
    sys.stdout = writer
    return writer


@dataclass
class ExecutedGraph:
    namespace: dict[str, Any]
    outputs: Mapping[int, Any]
    script_root: Path
    environment: ExecutionEnvironment
    _lease: _GraphLease = field(repr=False)
    _closed: bool = field(default=False, init=False, repr=False)
    _close_lock: threading.RLock = field(
        default_factory=threading.RLock, init=False, repr=False
    )

    def close(self) -> None:
        """在调用者确认全部 inflight 已终态后退休图及其脚本模块。"""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            try:
                _close_execution_environment(self.environment, self.script_root)
            finally:
                self._lease.release()


def execute_user_script(
    *,
    script_path: str | os.PathLike[str],
    job_path: str | os.PathLike[str],
    api_version: str | int,
    mode: str,
    python_module_dirs: Iterable[str | os.PathLike[str]] = (),
) -> ExecutedGraph:
    """执行用户脚本，并让 import 环境与返回图保持同寿命。"""
    script = Path(script_path).resolve(strict=True)
    job = Path(job_path).resolve(strict=True)
    search_paths = build_module_search_paths(
        script_path=script,
        runtime_dirs=python_module_dirs,
    )
    environment = ExecutionEnvironment(search_paths)
    lease = _acquire_graph_lease(script.parent)
    try:
        import vapoursynth as vs

        evict_modules_under(script.parent)
        importlib.invalidate_caches()
        vs.clear_outputs()
        namespace = {
            "__name__": "__vapoursynth__",
            "__file__": str(script),
            "assetmaker_job": str(job),
            "assetmaker_api": str(api_version),
            "assetmaker_script": str(script),
            "assetmaker_mode": mode,
        }
        environment.activate()
        code = compile(script.read_bytes(), str(script), "exec")
        exec(code, namespace, namespace)
        outputs = MappingProxyType(dict(vs.get_outputs()))
        return ExecutedGraph(
            namespace=namespace,
            outputs=outputs,
            script_root=script.parent,
            environment=environment,
            _lease=lease,
        )
    except BaseException:
        try:
            _close_execution_environment(environment, script.parent)
        finally:
            lease.release()
        raise


__all__ = [
    "ExecutedGraph",
    "ExecutionEnvironment",
    "GraphLifecycleError",
    "MAX_LOG_BODY_BYTES",
    "PythonLogWriter",
    "build_module_search_paths",
    "evict_modules_under",
    "execute_user_script",
    "helper_root",
    "install_python_stdout",
    "runtime_python_dirs_from_env",
]
