"""AssetMaker 的便携 VapourSynth 执行 ABI。

该包必须能被随 VSPipe 分发的嵌入式 Python 单独导入，因此这里只依赖标准库，
不得引用应用的 ``core``、``config`` 或 Qt 包。
"""

from __future__ import annotations

from typing import Any


class AssetmakerVSError(ValueError):
    """带稳定机器字段的共享 ABI 错误。"""

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
        self.message = message
        self.code = code
        self.field = field
        self.path = path
        self.expected = expected
        self.actual = actual
        self.hint = hint
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        location = f"{self.path}: " if self.path else ""
        return f"{location}[{self.code}] {self.message}"

    def with_path(self, path: str) -> "AssetmakerVSError":
        """返回同类型、同机器身份但附带文件路径的错误。"""
        return type(self)(
            self.message,
            code=self.code,
            field=self.field,
            path=path,
            expected=self.expected,
            actual=self.actual,
            hint=self.hint,
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "field": self.field,
            "path": self.path,
            "expected": self.expected,
            "actual": self.actual,
            "hint": self.hint,
            "message": self.message,
        }
        return payload


__all__ = ["AssetmakerVSError"]
