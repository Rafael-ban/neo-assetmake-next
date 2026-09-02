"""旧 ``vsconfig.json`` 到运行配置覆盖文件的一次性迁移。"""

from __future__ import annotations

import json
import ntpath
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import config.vs_runtime as runtime_config
from config.vs_runtime import VSRuntimeConfigError
from core.file_utils import atomic_write_json, sha256_file
from utils.windows_paths import canonicalize_windows_absolute_path


LEGACY_FIELD_MAP = {
    "core.num_threads": "core.num_threads",
    "core.max_cache_size_mb": "core.max_cache_size_mb",
    "extra_plugin_dirs": "plugins.native_plugin_dirs",
}
IGNORED_FILTER_FIELDS = (
    "required_plugins",
    "image_source_format",
    "output_format",
    "resampler_kernel",
    "colour",
)


@dataclass(frozen=True)
class MigrationReport:
    applied: bool
    migrated_fields: tuple[str, ...]
    ignored_fields: tuple[str, ...]
    source_hash: str


def _read_object(path: Path, location: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VSRuntimeConfigError(f"{path.resolve()}: {exc}") from exc
    if not isinstance(payload, dict):
        raise VSRuntimeConfigError(f"{path.resolve()}: {location} 必须是对象")
    return payload


def _get_nested(data: dict[str, Any], dotted: str) -> tuple[bool, Any]:
    current: Any = data
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _set_nested(data: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    current = data
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[parts[-1]] = value


def _canonicalize_legacy_plugin_dirs(value: Any, source: Path) -> Any:
    if not isinstance(value, list):
        return value
    install_root = (
        source.parent.parent
        if source.parent.name.casefold() == "config"
        else source.parent
    )
    media_root = install_root / "tools" / "media"
    canonical = []
    for item in value:
        if not isinstance(item, str):
            canonical.append(item)
            continue
        candidate = (
            item
            if ntpath.isabs(item.replace("/", "\\"))
            else str(media_root / item)
        )
        canonical.append(
            canonicalize_windows_absolute_path(
                candidate, "plugins.native_plugin_dirs"
            )
        )
    return canonical


def _source_path_and_hash(
    legacy_path: str | os.PathLike[str] | None,
) -> tuple[Path, str | None]:
    """解析旧配置源并计算 hash，统一 public 路径错误边界。"""
    source_input: Any = legacy_path
    source: Path | None = None
    try:
        source = (
            Path(source_input)
            if source_input is not None
            else runtime_config.default_vs_runtime_path().with_name(
                "vsconfig.json"
            )
        ).resolve()
        if not source.exists():
            return source, None
        return source, sha256_file(source)
    except (OSError, TypeError, ValueError) as exc:
        context = source if source is not None else source_input
        if context is None:
            context = "legacy vsconfig"
        try:
            label = str(context)
        except (TypeError, ValueError):
            label = repr(context)
        raise VSRuntimeConfigError(f"{label}: {exc}") from exc


def _raise_target_error(target: Path, exc: VSRuntimeConfigError) -> None:
    """为已持锁 target 操作补充稳定的绝对路径上下文。"""
    resolved = str(target.resolve())
    if resolved in str(exc):
        raise exc
    raise VSRuntimeConfigError(f"{resolved}: {exc}") from exc


def migrate_legacy_vsconfig_once(
    legacy_path: str | os.PathLike[str] | None = None,
    user_path: str | os.PathLike[str] | None = None,
    marker_path: str | os.PathLike[str] | None = None,
) -> MigrationReport:
    """按旧文件 hash 幂等迁移运行字段，并报告被忽略的滤镜字段。"""
    source, source_hash = _source_path_and_hash(legacy_path)
    if source_hash is None:
        return MigrationReport(False, (), (), "")

    target = (
        Path(user_path)
        if user_path is not None
        else runtime_config.default_vs_runtime_user_path()
    )
    marker = (
        Path(marker_path)
        if marker_path is not None
        else target.parent / "vsconfig.migration.json"
    )

    with runtime_config._override_lock(target):
        try:
            existing, target_exists = (
                runtime_config._read_vs_runtime_override_locked(target)
            )
        except VSRuntimeConfigError as exc:
            _raise_target_error(target, exc)

        same_source_hash = False
        if marker.exists():
            marker_payload = _read_object(marker, "migration marker")
            same_source_hash = (
                marker_payload.get("source_hash") == source_hash
            )
        if same_source_hash and target_exists:
            return MigrationReport(False, (), (), source_hash)

        legacy = _read_object(source, "legacy vsconfig")
        migrated: dict[str, Any] = {}
        migrated_fields: list[str] = []
        for old_field, new_field in LEGACY_FIELD_MAP.items():
            already_present, _value = _get_nested(existing, new_field)
            if already_present:
                continue
            present, value = _get_nested(legacy, old_field)
            if not present:
                continue
            if old_field == "extra_plugin_dirs":
                try:
                    value = _canonicalize_legacy_plugin_dirs(value, source)
                except ValueError as exc:
                    raise VSRuntimeConfigError(f"{source}: {exc}") from exc
            _set_nested(migrated, new_field, value)
            migrated_fields.append(old_field)

        ignored_fields = tuple(
            field for field in IGNORED_FILTER_FIELDS if field in legacy
        )
        payload = runtime_config._deep_merge(existing, migrated)
        try:
            runtime_config._write_vs_runtime_override_locked(target, payload)
        except VSRuntimeConfigError as exc:
            _raise_target_error(target, exc)
        atomic_write_json(marker, {"source_hash": source_hash}, indent=2)
        return MigrationReport(
            True,
            tuple(migrated_fields),
            ignored_fields,
            source_hash,
        )
