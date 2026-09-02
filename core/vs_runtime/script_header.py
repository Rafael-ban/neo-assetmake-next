"""自定义 ``.vpy`` 脚本声明的应用侧不可变适配层。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal, NoReturn

from resources.vapoursynth.python.assetmaker_vs import script_header as _wire


HEADER_LIMIT_BYTES = _wire.HEADER_LIMIT_BYTES


class ScriptHeaderError(ValueError):
    """脚本声明错误；机器字段与便携 helper 保持完全一致。"""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        field: str | None = None,
        path: str | None = None,
        expected: Any = None,
        actual: Any = None,
        hint: str | None = None,
    ) -> None:
        self.code = code
        self.field = field
        self.path = path
        self.expected = expected
        self.actual = actual
        self.hint = hint
        super().__init__(str(message))


@dataclass(frozen=True)
class ScriptHeader:
    api_version: Literal[1]
    mode: Literal["compatible", "raw"]
    capabilities: tuple[str, ...]
    requires: tuple[str, ...]
    editor_output: Literal[0, 1]


def _raise_adapter(exc: _wire.ScriptHeaderError) -> NoReturn:
    raise ScriptHeaderError(
        str(exc),
        code=exc.code,
        field=exc.field,
        path=exc.path,
        expected=exc.expected,
        actual=exc.actual,
        hint=exc.hint,
    ) from exc


def _adapt(payload: dict[str, Any]) -> ScriptHeader:
    return ScriptHeader(
        api_version=payload["api_version"],
        mode=payload["mode"],
        capabilities=tuple(payload["capabilities"]),
        requires=tuple(payload["requires"]),
        editor_output=payload["editor_output"],
    )


def parse_script_header_text(text: str) -> ScriptHeader:
    """通过共享 wire 解析器解析文本，再冻结为应用侧模型。"""
    try:
        return _adapt(_wire.parse_script_header_text(text))
    except _wire.ScriptHeaderError as exc:
        _raise_adapter(exc)


def parse_script_header(path: str | os.PathLike[str]) -> ScriptHeader:
    """通过共享 wire 解析器解析文件，再冻结为应用侧模型。"""
    try:
        return _adapt(_wire.parse_script_header(path))
    except _wire.ScriptHeaderError as exc:
        _raise_adapter(exc)


__all__ = [
    "HEADER_LIMIT_BYTES",
    "ScriptHeader",
    "ScriptHeaderError",
    "parse_script_header",
    "parse_script_header_text",
]
