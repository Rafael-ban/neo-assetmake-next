"""只读取脚本头注释的便携声明解析器。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from . import AssetmakerVSError


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


class ScriptHeaderError(AssetmakerVSError):
    """脚本声明缺失、非法或不满足协议。"""


class InvocationError(AssetmakerVSError):
    """脚本 header 与调用参数不一致。"""


def _error(
    message: str,
    *,
    code: str,
    field: str,
    expected: Any = None,
    actual: Any = None,
) -> ScriptHeaderError:
    return ScriptHeaderError(
        message,
        code=code,
        field=field,
        expected=expected,
        actual=actual,
    )


def _comma_list(value: str, field: str, *, allow_empty: bool) -> list[str]:
    if not value.strip():
        if allow_empty:
            return []
        raise _error(
            f"assetmaker-{field} 不能为空",
            code=f"header.{field}",
            field=field,
            expected="non-empty comma list",
            actual=value,
        )
    items = [item.strip() for item in value.split(",")]
    if any(not item for item in items):
        raise _error(
            f"assetmaker-{field} 包含空条目",
            code=f"header.{field}",
            field=field,
            actual=value,
        )
    if len(set(items)) != len(items):
        raise _error(
            f"assetmaker-{field} 包含重复条目",
            code=f"header.{field}",
            field=field,
            actual=value,
        )
    return items


def _decode_header_window(data: bytes) -> str:
    window = data[:HEADER_LIMIT_BYTES]
    if len(data) > HEADER_LIMIT_BYTES and not window.endswith(b"\n"):
        last_complete_line = window.rfind(b"\n")
        window = window[: last_complete_line + 1] if last_complete_line >= 0 else b""
    try:
        return window.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise _error(
            f"脚本 header 不是合法 UTF-8: {exc}",
            code="header.encoding",
            field="header",
            expected="UTF-8",
            actual=str(exc),
        ) from exc


def parse_script_header_text(text: str) -> dict[str, Any]:
    """解析文本最前方连续注释/空行中的 assetmaker 声明。"""
    prefix = _decode_header_window(text.encode("utf-8"))
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
            raise _error(
                f"未知 assetmaker header: {field!r}",
                code="header.unknown",
                field=field,
                actual=field,
            )
        if field in declarations:
            raise _error(
                f"重复 assetmaker header: {field!r}",
                code="header.duplicate",
                field=field,
                actual=field,
            )
        declarations[field] = value

    missing = sorted(_KNOWN_FIELDS - set(declarations))
    if missing:
        raise _error(
            f"缺少 assetmaker header: {', '.join(missing)}",
            code="header.required",
            field=missing[0],
            expected=missing,
        )
    if declarations["api"] != "1":
        raise _error(
            f"不支持 assetmaker-api: {declarations['api']!r}",
            code="header.api",
            field="api",
            expected="1",
            actual=declarations["api"],
        )
    mode = declarations["mode"]
    if mode not in ("compatible", "raw"):
        raise _error(
            f"未知 assetmaker-mode: {mode!r}",
            code="header.mode",
            field="mode",
            expected=["compatible", "raw"],
            actual=mode,
        )
    editor_text = declarations["editor-output"]
    if editor_text not in ("0", "1"):
        raise _error(
            "assetmaker-editor-output 必须为 0 或 1",
            code="header.editor_output",
            field="editor-output",
            expected=[0, 1],
            actual=editor_text,
        )

    capabilities = _comma_list(
        declarations["capabilities"], "capabilities", allow_empty=False
    )
    unknown_capabilities = sorted(set(capabilities) - _KNOWN_CAPABILITIES)
    if unknown_capabilities:
        raise _error(
            f"未知 capability: {', '.join(unknown_capabilities)}",
            code="header.capabilities",
            field="capabilities",
            expected=sorted(_KNOWN_CAPABILITIES),
            actual=unknown_capabilities,
        )
    requirements = _comma_list(
        declarations["requires"], "requires", allow_empty=True
    )
    invalid_requirements = [
        item for item in requirements if _REQUIREMENT_RE.fullmatch(item) is None
    ]
    if invalid_requirements:
        raise _error(
            f"非法 requirement: {', '.join(invalid_requirements)}",
            code="header.requires",
            field="requires",
            expected="namespace.Function",
            actual=invalid_requirements,
        )

    editor_output = int(editor_text)
    if (
        mode == "compatible"
        and _EDITOR_OPERATIONS.intersection(capabilities)
        and editor_output != 1
    ):
        raise _error(
            "compatible 脚本声明 trim/crop/rotation 时必须设置 "
            "assetmaker-editor-output: 1",
            code="header.editor_output",
            field="editor-output",
            expected=1,
            actual=editor_output,
        )
    if mode == "raw" and editor_output != 0:
        raise _error(
            "raw 脚本不读取编辑器输出，assetmaker-editor-output 必须为 0",
            code="header.editor_output",
            field="editor-output",
            expected=0,
            actual=editor_output,
        )
    return {
        "api_version": 1,
        "mode": mode,
        "capabilities": capabilities,
        "requires": requirements,
        "editor_output": editor_output,
    }


def parse_script_header(path: str | os.PathLike[str]) -> dict[str, Any]:
    """仅读取脚本前 8 KiB；错误携带 canonical 文件绝对路径。"""
    script_path = Path(path)
    absolute = str(script_path.resolve())
    try:
        with script_path.open("rb") as handle:
            text = _decode_header_window(handle.read(HEADER_LIMIT_BYTES + 1))
        return parse_script_header_text(text)
    except ScriptHeaderError as exc:
        raise exc.with_path(absolute) from exc
    except (OSError, UnicodeError) as exc:
        raise ScriptHeaderError(
            str(exc),
            code="header.io",
            field="header",
            path=absolute,
            actual=str(exc),
        ) from exc


def validate_invocation(
    header: dict[str, Any], *, api_version: str | int, mode: str
) -> None:
    """在执行用户代码前比对 header 与 worker/VSPipe 调用身份。"""
    expected_api = str(header.get("api_version"))
    actual_api = str(api_version)
    if expected_api != actual_api:
        raise InvocationError(
            f"调用 API {actual_api!r} 与脚本 header {expected_api!r} 不一致",
            code="invocation.api",
            field="api_version",
            expected=expected_api,
            actual=actual_api,
        )
    expected_mode = header.get("mode")
    if expected_mode != mode:
        raise InvocationError(
            f"调用 mode {mode!r} 与脚本 header {expected_mode!r} 不一致",
            code="invocation.mode",
            field="mode",
            expected=expected_mode,
            actual=mode,
        )


__all__ = [
    "HEADER_LIMIT_BYTES",
    "InvocationError",
    "ScriptHeaderError",
    "parse_script_header",
    "parse_script_header_text",
    "validate_invocation",
]
