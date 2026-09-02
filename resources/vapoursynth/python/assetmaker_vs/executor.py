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

from .job_api import runtime_python_dirs_from_env


MAX_PROTOCOL_BYTES = 4 * 1024 * 1024
MAX_LOG_BODY_BYTES = MAX_PROTOCOL_BYTES - 1


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


def _module_paths(module: Any) -> tuple[Path, ...]:
    """返回模块可识别的物理来源，包含无 ``__file__`` 的 namespace 包。"""
    candidates: list[Any] = [getattr(module, "__file__", None)]
    package_paths = getattr(module, "__path__", ())
    if isinstance(package_paths, (str, bytes, os.PathLike)):
        candidates.append(package_paths)
    else:
        try:
            candidates.extend(package_paths)
        except Exception:
            # 失去父包的 _NamespacePath 可能在重算时抛 KeyError；单个坏模块
            # 不应中止整个退休扫描，其 __file__ 仍会在上方独立处理。
            pass
    paths: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            value = os.fsdecode(candidate)
        except (TypeError, ValueError):
            continue
        if not value or value in {"built-in", "frozen"} or value.startswith("<"):
            continue
        try:
            path = Path(value).resolve()
            key = _path_key(path)
        except (OSError, TypeError, ValueError):
            continue
        if key not in seen:
            seen.add(key)
            paths.append(path)
    return tuple(paths)


def evict_modules_under(script_root: str | os.PathLike[str]) -> tuple[str, ...]:
    """只驱逐由当前脚本根加载的模块，永不驱逐 helper 自身。"""
    root = Path(script_root).resolve()
    helper_package = helper_root() / "assetmaker_vs"
    removed: list[str] = []
    for name, module in tuple(sys.modules.items()):
        if name == "assetmaker_vs" or name.startswith("assetmaker_vs."):
            continue
        module_paths = _module_paths(module)
        if not module_paths:
            continue
        if any(_is_under(path, helper_package) for path in module_paths):
            continue
        if any(_is_under(path, root) for path in module_paths):
            sys.modules.pop(name, None)
            removed.append(name)
    return tuple(removed)


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
    _closed: bool = field(default=False, init=False, repr=False)
    _close_lock: threading.RLock = field(
        default_factory=threading.RLock, init=False, repr=False
    )

    def close(self) -> None:
        """在调用者确认全部 inflight 已终态后退休图及其脚本模块。"""
        with self._close_lock:
            if self._closed:
                return
            self.environment.close()
            evict_modules_under(self.script_root)
            importlib.invalidate_caches()
            self._closed = True


def execute_user_script(
    *,
    script_path: str | os.PathLike[str],
    job_path: str | os.PathLike[str],
    api_version: str | int,
    mode: str,
    python_module_dirs: Iterable[str | os.PathLike[str]] = (),
) -> ExecutedGraph:
    """执行用户脚本，并让 import 环境与返回图保持同寿命。"""
    import vapoursynth as vs

    script = Path(script_path).resolve(strict=True)
    job = Path(job_path).resolve(strict=True)
    search_paths = build_module_search_paths(
        script_path=script,
        runtime_dirs=python_module_dirs,
    )
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
    environment = ExecutionEnvironment(search_paths)
    environment.activate()
    try:
        code = compile(script.read_bytes(), str(script), "exec")
        exec(code, namespace, namespace)
        outputs = MappingProxyType(dict(vs.get_outputs()))
        return ExecutedGraph(
            namespace=namespace,
            outputs=outputs,
            script_root=script.parent,
            environment=environment,
        )
    except BaseException:
        environment.close()
        evict_modules_under(script.parent)
        importlib.invalidate_caches()
        raise


__all__ = [
    "ExecutedGraph",
    "ExecutionEnvironment",
    "MAX_LOG_BODY_BYTES",
    "PythonLogWriter",
    "build_module_search_paths",
    "evict_modules_under",
    "execute_user_script",
    "helper_root",
    "install_python_stdout",
    "runtime_python_dirs_from_env",
]
