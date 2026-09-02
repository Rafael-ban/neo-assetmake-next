"""VapourSynth 运行时策略配置。

该模块只保存 worker、core、插件目录和全局脚本位置。滤镜语义属于渲染
作业或脚本，不能回流到运行配置。R73 的 ``max_cache_size_mb`` 目前仅作为
软建议保留，worker 是否应用该值由后续里程碑决定。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from core.file_utils import atomic_write_json
from utils.file_utils import get_app_dir


RUNTIME_CONFIG_FILENAME = "vs_runtime.json"
USER_OVERRIDE_FILENAME = "vs_runtime.user.json"


class VSRuntimeConfigError(ValueError):
    """运行配置不存在之外的读取或校验错误。"""


def _require_object(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise VSRuntimeConfigError(f"{location} 必须是对象")
    return value


def _reject_unknown(
    data: dict[str, Any], allowed: set[str], location: str
) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise VSRuntimeConfigError(
            f"{location} 包含未知字段: {', '.join(unknown)}"
        )


def _non_negative_int(value: Any, location: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise VSRuntimeConfigError(f"{location} 必须是非负整数")
    return value


def _string_tuple(value: Any, location: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise VSRuntimeConfigError(f"{location} 必须是非空字符串数组")
    return tuple(value)


@dataclass(frozen=True)
class WorkerConfig:
    startup_timeout_ms: int = 15_000
    frame_timeout_ms: int = 10_000
    shutdown_timeout_ms: int = 3_000

    def to_dict(self) -> dict[str, int]:
        return {
            "startup_timeout_ms": self.startup_timeout_ms,
            "frame_timeout_ms": self.frame_timeout_ms,
            "shutdown_timeout_ms": self.shutdown_timeout_ms,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "WorkerConfig":
        data = _require_object(value, "worker")
        allowed = {
            "startup_timeout_ms",
            "frame_timeout_ms",
            "shutdown_timeout_ms",
        }
        _reject_unknown(data, allowed, "worker")
        if set(data) != allowed:
            raise VSRuntimeConfigError("worker 缺少必需字段")
        return cls(
            startup_timeout_ms=_non_negative_int(
                data["startup_timeout_ms"], "worker.startup_timeout_ms"
            ),
            frame_timeout_ms=_non_negative_int(
                data["frame_timeout_ms"], "worker.frame_timeout_ms"
            ),
            shutdown_timeout_ms=_non_negative_int(
                data["shutdown_timeout_ms"], "worker.shutdown_timeout_ms"
            ),
        )


@dataclass(frozen=True)
class CoreConfig:
    num_threads: int = 0
    max_cache_size_mb: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "num_threads": self.num_threads,
            "max_cache_size_mb": self.max_cache_size_mb,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "CoreConfig":
        data = _require_object(value, "core")
        allowed = {"num_threads", "max_cache_size_mb"}
        _reject_unknown(data, allowed, "core")
        if set(data) != allowed:
            raise VSRuntimeConfigError("core 缺少必需字段")
        return cls(
            num_threads=_non_negative_int(
                data["num_threads"], "core.num_threads"
            ),
            max_cache_size_mb=_non_negative_int(
                data["max_cache_size_mb"], "core.max_cache_size_mb"
            ),
        )


@dataclass(frozen=True)
class PluginConfig:
    native_plugin_dirs: tuple[str, ...] = ()
    python_module_dirs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "native_plugin_dirs": list(self.native_plugin_dirs),
            "python_module_dirs": list(self.python_module_dirs),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "PluginConfig":
        data = _require_object(value, "plugins")
        allowed = {"native_plugin_dirs", "python_module_dirs"}
        _reject_unknown(data, allowed, "plugins")
        if set(data) != allowed:
            raise VSRuntimeConfigError("plugins 缺少必需字段")
        return cls(
            native_plugin_dirs=_string_tuple(
                data["native_plugin_dirs"], "plugins.native_plugin_dirs"
            ),
            python_module_dirs=_string_tuple(
                data["python_module_dirs"], "plugins.python_module_dirs"
            ),
        )


@dataclass(frozen=True)
class ScriptConfig:
    global_script_path: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"global_script_path": self.global_script_path}

    @classmethod
    def from_dict(cls, value: Any) -> "ScriptConfig":
        data = _require_object(value, "scripts")
        allowed = {"global_script_path"}
        _reject_unknown(data, allowed, "scripts")
        if set(data) != allowed:
            raise VSRuntimeConfigError("scripts 缺少必需字段")
        path = data["global_script_path"]
        if not isinstance(path, str):
            raise VSRuntimeConfigError("scripts.global_script_path 必须是字符串")
        return cls(global_script_path=path)


@dataclass(frozen=True)
class VSRuntimeConfig:
    schema_version: Literal[1] = 1
    worker: WorkerConfig = field(default_factory=WorkerConfig)
    core: CoreConfig = field(default_factory=CoreConfig)
    plugins: PluginConfig = field(default_factory=PluginConfig)
    scripts: ScriptConfig = field(default_factory=ScriptConfig)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "worker": self.worker.to_dict(),
            "core": self.core.to_dict(),
            "plugins": self.plugins.to_dict(),
            "scripts": self.scripts.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "VSRuntimeConfig":
        data = _require_object(value, "runtime")
        allowed = {"schema_version", "worker", "core", "plugins", "scripts"}
        _reject_unknown(data, allowed, "runtime")
        if set(data) != allowed:
            raise VSRuntimeConfigError("runtime 缺少必需字段")
        version = data["schema_version"]
        if isinstance(version, bool) or version != 1:
            raise VSRuntimeConfigError(f"未知 schema_version: {version!r}")
        return cls(
            schema_version=1,
            worker=WorkerConfig.from_dict(data["worker"]),
            core=CoreConfig.from_dict(data["core"]),
            plugins=PluginConfig.from_dict(data["plugins"]),
            scripts=ScriptConfig.from_dict(data["scripts"]),
        )


def default_vs_runtime_path() -> Path:
    """返回随应用分发的只读运行配置路径。"""
    return Path(get_app_dir()) / "config" / RUNTIME_CONFIG_FILENAME


def default_vs_runtime_user_path() -> Path:
    """返回固定的可写用户覆盖路径。"""
    appdata = os.environ.get("APPDATA")
    root = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return (
        root
        / "ArknightsPassMaker"
        / "vapoursynth"
        / USER_OVERRIDE_FILENAME
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VSRuntimeConfigError(f"{path.resolve()}: {exc}") from exc


def _validate_partial(value: Any) -> dict[str, Any]:
    data = _require_object(value, "override")
    allowed = {"schema_version", "worker", "core", "plugins", "scripts"}
    _reject_unknown(data, allowed, "override")
    merged = _deep_merge(VSRuntimeConfig().to_dict(), data)
    VSRuntimeConfig.from_dict(merged)
    return data


def _deep_merge(
    base: dict[str, Any], override: dict[str, Any]
) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_vs_runtime(
    shipped_path: str | os.PathLike[str] | None = None,
    user_path: str | os.PathLike[str] | None = None,
) -> VSRuntimeConfig:
    """读取随包配置并叠加用户覆盖；仅缺失文件可回落默认值。"""
    use_default_user = shipped_path is None
    shipped = (
        Path(shipped_path)
        if shipped_path is not None
        else default_vs_runtime_path()
    )
    if shipped.exists():
        try:
            base = VSRuntimeConfig.from_dict(_read_json(shipped)).to_dict()
        except VSRuntimeConfigError as exc:
            if str(shipped.resolve()) in str(exc):
                raise
            raise VSRuntimeConfigError(f"{shipped.resolve()}: {exc}") from exc
    else:
        base = VSRuntimeConfig().to_dict()

    override_path = (
        Path(user_path)
        if user_path is not None
        else default_vs_runtime_user_path() if use_default_user else None
    )
    if override_path is not None and override_path.exists():
        try:
            override = _validate_partial(_read_json(override_path))
            base = _deep_merge(base, override)
            return VSRuntimeConfig.from_dict(base)
        except VSRuntimeConfigError as exc:
            if str(override_path.resolve()) in str(exc):
                raise
            raise VSRuntimeConfigError(
                f"{override_path.resolve()}: {exc}"
            ) from exc
    return VSRuntimeConfig.from_dict(base)


def save_vs_runtime_override(
    path: str | os.PathLike[str], override: dict[str, Any]
) -> None:
    """严格校验并原子写入用户覆盖；不会修改随包配置。"""
    payload = _validate_partial(override)
    atomic_write_json(path, payload, indent=2)
