"""旧 ``vsconfig.json`` 到运行配置覆盖文件的一次性迁移。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.vs_runtime import (
    VSRuntimeConfigError,
    default_vs_runtime_path,
    default_vs_runtime_user_path,
    save_vs_runtime_override,
)
from core.file_utils import atomic_write_json, sha256_file


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


def migrate_legacy_vsconfig_once(
    legacy_path: str | os.PathLike[str] | None = None,
    user_path: str | os.PathLike[str] | None = None,
    marker_path: str | os.PathLike[str] | None = None,
) -> MigrationReport:
    """按旧文件 hash 幂等迁移运行字段，并报告被忽略的滤镜字段。"""
    source = (
        Path(legacy_path)
        if legacy_path is not None
        else default_vs_runtime_path().with_name("vsconfig.json")
    )
    target = (
        Path(user_path)
        if user_path is not None
        else default_vs_runtime_user_path()
    )
    marker = (
        Path(marker_path)
        if marker_path is not None
        else target.parent / "vsconfig.migration.json"
    )
    if not source.exists():
        return MigrationReport(False, (), (), "")

    source_hash = sha256_file(source)
    legacy = _read_object(source, "legacy vsconfig")
    migrated: dict[str, Any] = {}
    migrated_fields: list[str] = []
    for old_field, new_field in LEGACY_FIELD_MAP.items():
        present, value = _get_nested(legacy, old_field)
        if present:
            _set_nested(migrated, new_field, value)
            migrated_fields.append(old_field)
    ignored_fields = tuple(
        field for field in IGNORED_FILTER_FIELDS if field in legacy
    )
    report_fields = tuple(migrated_fields)

    if marker.exists():
        marker_payload = _read_object(marker, "migration marker")
        if marker_payload.get("source_hash") == source_hash:
            return MigrationReport(
                False, report_fields, ignored_fields, source_hash
            )

    if target.exists():
        existing = _read_object(target, "runtime user override")
        migrated = _deep_merge(migrated, existing)
    save_vs_runtime_override(target, migrated)
    atomic_write_json(marker, {"source_hash": source_hash}, indent=2)
    return MigrationReport(True, report_fields, ignored_fields, source_hash)
