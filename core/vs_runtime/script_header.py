"""自定义 ``.vpy`` 脚本开头的安全声明解析器。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


HEADER_LIMIT_BYTES = 8 * 1024
_KNOWN_FIELDS = {
    "api",
    "mode",
    "capabilities",
    "requires",
    "editor-output",
}
_KNOWN_CAPABILITIES = {
    "source",
    "trim",
    "crop",
    "rotation",
    "resolution",
    "image_loop",
}
_EDITOR_OPERATIONS = {"trim", "crop", "rotation"}
_REQUIREMENT_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$"
)
_DECLARATION_RE = re.compile(
    r"^#\s*assetmaker-([^:]+)\s*:\s*(.*?)\s*$"
)


class ScriptHeaderError(ValueError):
    """脚本声明缺失或不满足兼容协议。"""


@dataclass(frozen=True)
class ScriptHeader:
    api_version: Literal[1]
    mode: Literal["compatible", "raw"]
    capabilities: tuple[str, ...]
    requires: tuple[str, ...]
    editor_output: Literal[0, 1]


def _comma_list(value: str, field: str, *, allow_empty: bool) -> tuple[str, ...]:
    if not value.strip():
        if allow_empty:
            return ()
        raise ScriptHeaderError(f"assetmaker-{field} 不能为空")
    items = tuple(item.strip() for item in value.split(","))
    if any(not item for item in items):
        raise ScriptHeaderError(f"assetmaker-{field} 包含空条目")
    if len(set(items)) != len(items):
        raise ScriptHeaderError(f"assetmaker-{field} 包含重复条目")
    return items


def parse_script_header_text(text: str) -> ScriptHeader:
    """解析文本最前方连续注释/空行中的 assetmaker 声明。"""
    prefix = text.encode("utf-8")[:HEADER_LIMIT_BYTES].decode(
        "utf-8", errors="ignore"
    )
    prefix = prefix.removeprefix("\ufeff")
    declarations: dict[str, str] = {}
    for line in prefix.splitlines():
        stripped = line.lstrip()
        if not stripped:
            continue
        if not stripped.startswith("#"):
            break
        match = _DECLARATION_RE.match(stripped)
        if match is None:
            continue
        field, value = match.groups()
        field = field.strip()
        if field not in _KNOWN_FIELDS:
            raise ScriptHeaderError(f"未知 assetmaker header: {field!r}")
        if field in declarations:
            raise ScriptHeaderError(f"重复 assetmaker header: {field!r}")
        declarations[field] = value

    missing = sorted(_KNOWN_FIELDS - set(declarations))
    if missing:
        raise ScriptHeaderError(f"缺少 assetmaker header: {', '.join(missing)}")

    if declarations["api"] != "1":
        raise ScriptHeaderError(
            f"不支持 assetmaker-api: {declarations['api']!r}"
        )
    mode = declarations["mode"]
    if mode not in ("compatible", "raw"):
        raise ScriptHeaderError(
            f"未知 assetmaker-mode: {mode!r}"
        )
    if declarations["editor-output"] not in ("0", "1"):
        raise ScriptHeaderError(
            "assetmaker-editor-output 必须为 0 或 1"
        )

    capabilities = _comma_list(
        declarations["capabilities"], "capabilities", allow_empty=False
    )
    unknown_capabilities = sorted(set(capabilities) - _KNOWN_CAPABILITIES)
    if unknown_capabilities:
        raise ScriptHeaderError(
            f"未知 capability: {', '.join(unknown_capabilities)}"
        )

    requirements = _comma_list(
        declarations["requires"], "requires", allow_empty=True
    )
    invalid_requirements = [
        item for item in requirements if _REQUIREMENT_RE.fullmatch(item) is None
    ]
    if invalid_requirements:
        raise ScriptHeaderError(
            f"非法 requirement: {', '.join(invalid_requirements)}"
        )

    editor_output = int(declarations["editor-output"])
    if (
        mode == "compatible"
        and _EDITOR_OPERATIONS.intersection(capabilities)
        and editor_output != 1
    ):
        raise ScriptHeaderError(
            "compatible 脚本声明 trim/crop/rotation 时必须设置 "
            "assetmaker-editor-output: 1"
        )
    if mode == "raw" and editor_output != 0:
        raise ScriptHeaderError(
            "raw 脚本不读取编辑器输出，assetmaker-editor-output 必须为 0"
        )
    return ScriptHeader(
        api_version=1,
        mode=mode,
        capabilities=capabilities,
        requires=requirements,
        editor_output=editor_output,
    )


def parse_script_header(path: str | os.PathLike[str]) -> ScriptHeader:
    """仅读取脚本前 8 KiB 并解析声明，错误包含绝对路径。"""
    script_path = Path(path)
    try:
        with script_path.open("rb") as handle:
            text = handle.read(HEADER_LIMIT_BYTES).decode(
                "utf-8-sig", errors="ignore"
            )
        return parse_script_header_text(text)
    except (OSError, ScriptHeaderError) as exc:
        raise ScriptHeaderError(f"{script_path.resolve()}: {exc}") from exc
