"""本机用户 VPY 选择、路径约束与项目 bundle 信任记录。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from core.file_utils import atomic_write_json


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TRUST_FILENAME = "trust.json"


class ScriptTrustError(ValueError):
    """脚本引用、canonical 路径或本机 trust store 无效。"""


@dataclass(frozen=True)
class ScriptReference:
    """项目可保存的脚本来源；不携带 mode、API、hash 或信任状态。"""

    source: Literal["builtin", "global", "project"] = "builtin"
    path: str = ""

    def __post_init__(self) -> None:
        if self.source not in ("builtin", "global", "project"):
            raise ScriptTrustError("脚本来源必须是 builtin/global/project")
        if self.source in ("builtin", "global"):
            if self.path:
                raise ScriptTrustError(f"{self.source} 脚本不得保存路径")
            return
        path = self.path
        pure = Path(path)
        if (
            not isinstance(path, str)
            or not path
            or "\\" in path
            or pure.is_absolute()
            or any(part in ("", ".", "..") for part in path.split("/"))
        ):
            raise ScriptTrustError("项目脚本必须是规范 project-relative POSIX 路径")


def default_trust_path() -> Path:
    appdata = os.environ.get("APPDATA")
    root = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return root / "ArknightsPassMaker" / "vapoursynth" / TRUST_FILENAME


def _canonical(path: str | Path, *, location: str, directory: bool = False) -> Path:
    try:
        resolved = Path(path).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ScriptTrustError(f"{location} 无法 canonicalize: {path}") from exc
    if directory:
        if not resolved.is_dir():
            raise ScriptTrustError(f"{location} 不是目录: {resolved}")
    elif not resolved.is_file():
        raise ScriptTrustError(f"{location} 不是文件: {resolved}")
    return resolved


def resolve_project_script(
    project_root: str | Path, reference: ScriptReference
) -> Path:
    """解析项目相对路径，拒绝链接/reparse 后逃逸项目根。"""
    if reference.source != "project":
        raise ScriptTrustError("只能解析 project 脚本引用")
    root = _canonical(project_root, location="项目根", directory=True)
    candidate = root.joinpath(*reference.path.split("/"))
    script = _canonical(candidate, location="项目脚本")
    try:
        script.relative_to(root)
    except ValueError as exc:
        raise ScriptTrustError(f"项目脚本逃逸项目根: {reference.path}") from exc
    if script.suffix.casefold() != ".vpy":
        raise ScriptTrustError("项目脚本必须是 .vpy 文件")
    return script


def resolve_script_reference(
    reference: ScriptReference,
    *,
    project_root: str | Path,
    app_dir: str | Path,
    global_script_path: str = "",
) -> Path:
    """从来源解析主 VPY；全局路径仅来自本机 override。"""
    if reference.source == "builtin":
        return _canonical(
            Path(app_dir) / "resources" / "vapoursynth" / "default_pipeline.vpy",
            location="内置脚本",
        )
    if reference.source == "global":
        if not global_script_path:
            raise ScriptTrustError("未选择全局 VPY 脚本")
        script = _canonical(global_script_path, location="全局脚本")
        if script.suffix.casefold() != ".vpy":
            raise ScriptTrustError("全局脚本必须是 .vpy 文件")
        return script
    return resolve_project_script(project_root, reference)


class ProjectTrustStore:
    """仅记录本机已确认的 ``(canonical script root, bundle hash)``。"""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_trust_path()

    @staticmethod
    def _validate_hash(bundle_hash: str) -> None:
        if not isinstance(bundle_hash, str) or _SHA256_RE.fullmatch(bundle_hash) is None:
            raise ScriptTrustError("bundle hash 必须是小写 SHA-256")

    def _read(self) -> dict[str, list[str]]:
        if not self.path.exists():
            return {}
        try:
            payload: Any = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ScriptTrustError(f"无法读取 trust store: {self.path}") from exc
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != 1
            or set(payload) != {"schema_version", "project_hashes"}
            or not isinstance(payload["project_hashes"], dict)
        ):
            raise ScriptTrustError(f"trust store 格式无效: {self.path}")
        records: dict[str, list[str]] = {}
        for root, hashes in payload["project_hashes"].items():
            if (
                not isinstance(root, str)
                or not isinstance(hashes, list)
                or any(not isinstance(item, str) or _SHA256_RE.fullmatch(item) is None for item in hashes)
            ):
                raise ScriptTrustError(f"trust store 记录无效: {self.path}")
            records[root] = hashes
        return records

    def is_trusted(self, script_root: str | Path, bundle_hash: str) -> bool:
        self._validate_hash(bundle_hash)
        root = _canonical(script_root, location="脚本根", directory=True)
        return bundle_hash in self._read().get(str(root), [])

    def trust(self, script_root: str | Path, bundle_hash: str) -> None:
        self._validate_hash(bundle_hash)
        root = _canonical(script_root, location="脚本根", directory=True)
        records = self._read()
        hashes = set(records.get(str(root), []))
        hashes.add(bundle_hash)
        records[str(root)] = sorted(hashes)
        atomic_write_json(
            self.path,
            {"schema_version": 1, "project_hashes": records},
            indent=2,
        )


__all__ = [
    "ProjectTrustStore",
    "ScriptReference",
    "ScriptTrustError",
    "TRUST_FILENAME",
    "default_trust_path",
    "resolve_project_script",
    "resolve_script_reference",
]
