"""Host-independent canonical Windows paths used by wire contracts."""

from __future__ import annotations

import ntpath
import re
from typing import Any


_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:\\")
_INVALID_COMPONENT_CHARS = frozenset('<>:"|?*')
_RESERVED_DEVICE_RE = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])$",
    re.IGNORECASE,
)


def _is_valid_component(component: str) -> bool:
    if (
        not component
        or component in (".", "..")
        or component.endswith((".", " "))
        or any(ord(char) < 32 for char in component)
        or any(char in _INVALID_COMPONENT_CHARS for char in component)
    ):
        return False
    device_stem = component.split(".", 1)[0]
    return _RESERVED_DEVICE_RE.fullmatch(device_stem) is None


def is_canonical_windows_absolute_path(value: Any) -> bool:
    """Return whether value is a full drive/UNC path in canonical wire form."""
    if not isinstance(value, str) or not value or "/" in value or "\0" in value:
        return False
    if value.startswith(("\\\\?\\", "\\\\.\\")):
        return False
    if _DRIVE_PREFIX_RE.match(value):
        components = value[3:].split("\\") if len(value) > 3 else []
    elif value.startswith("\\\\"):
        components = value[2:].split("\\")
        if len(components) < 2:
            return False
    else:
        return False
    if any(not _is_valid_component(component) for component in components):
        return False
    return ntpath.normpath(value) == value


def require_canonical_windows_absolute_path(
    value: Any, location: str = "path"
) -> str:
    """Return a canonical path or raise a stable ValueError."""
    if not is_canonical_windows_absolute_path(value):
        raise ValueError(
            f"{location} 必须是 canonical Windows drive/UNC 绝对路径: {value!r}"
        )
    return value


def canonicalize_windows_absolute_path(value: Any, location: str = "path") -> str:
    """Canonicalize separators/dot segments, but never resolve relative paths."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{location} 必须是非空字符串路径")
    canonical = ntpath.normpath(value.replace("/", "\\"))
    return require_canonical_windows_absolute_path(canonical, location)
