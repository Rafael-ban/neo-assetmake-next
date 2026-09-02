"""仅供独立 worker 使用的 portable VapourSynth loader。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

from config.vs_runtime import VSRuntimeConfig


_CODE_SUFFIXES = frozenset({".py", ".pyc", ".pyd", ".dll", ".zip", ".whl"})
_PORTABLE_CORE_FILES = ("vapoursynth.pyd", "vapoursynth.dll", "portable.vs")
_load_lock = threading.Lock()
_loaded_module: Any | None = None
_dll_directory_handle: Any | None = None


class VSLoaderError(RuntimeError):
    """portable VapourSynth runtime 缺失或无法加载。"""


def _validated_runtime(runtime: VSRuntimeConfig | dict[str, Any]) -> VSRuntimeConfig:
    if isinstance(runtime, VSRuntimeConfig):
        return VSRuntimeConfig.from_dict(runtime.to_dict())
    return VSRuntimeConfig.from_dict(runtime)


def _digest_record(digest: Any, label: str, data: bytes) -> None:
    label_bytes = label.encode("utf-8")
    digest.update(len(label_bytes).to_bytes(4, "big"))
    digest.update(label_bytes)
    digest.update(len(data).to_bytes(8, "big"))
    digest.update(data)


def _digest_file(digest: Any, label: str, path: Path) -> None:
    try:
        size = path.stat().st_size
        label_bytes = label.encode("utf-8")
        digest.update(len(label_bytes).to_bytes(4, "big"))
        digest.update(label_bytes)
        digest.update(size.to_bytes(8, "big"))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise VSLoaderError(f"无法读取 runtime 文件 {path}: {exc}") from exc


def _code_files(root: Path) -> list[Path]:
    try:
        if not root.is_dir():
            raise VSLoaderError(f"runtime 代码目录不存在: {root}")
        files = [
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.casefold() in _CODE_SUFFIXES
        ]
    except OSError as exc:
        raise VSLoaderError(f"无法遍历 runtime 代码目录 {root}: {exc}") from exc
    return sorted(
        files,
        key=lambda path: path.relative_to(root).as_posix().casefold(),
    )


def compute_runtime_fingerprint(
    app_dir: str | os.PathLike[str],
    runtime: VSRuntimeConfig | dict[str, Any],
) -> str:
    """哈希规范指定的 runtime JSON、portable core 与代码/插件文件。"""
    root = Path(app_dir).resolve()
    config = _validated_runtime(runtime)
    media_dir = root / "tools" / "media"
    digest = hashlib.sha256()
    canonical_runtime = json.dumps(
        config.to_dict(),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    _digest_record(digest, "runtime.json", canonical_runtime)

    for filename in _PORTABLE_CORE_FILES:
        path = media_dir / filename
        if not path.is_file():
            raise VSLoaderError(f"portable VapourSynth 文件不存在: {path}")
        _digest_file(digest, f"portable/{filename.casefold()}", path)

    directories: list[tuple[str, Path]] = [
        ("default-plugins", media_dir / "vs-plugins"),
        (
            "assetmaker-vs",
            root / "resources" / "vapoursynth" / "python" / "assetmaker_vs",
        ),
    ]
    directories.extend(
        (f"native-{index}", Path(path).resolve())
        for index, path in enumerate(config.plugins.native_plugin_dirs)
    )
    directories.extend(
        (f"python-{index}", Path(path).resolve())
        for index, path in enumerate(config.plugins.python_module_dirs)
    )
    for category, directory in directories:
        for path in _code_files(directory):
            relative = path.relative_to(directory).as_posix()
            _digest_file(digest, f"{category}/{relative}", path)
    return digest.hexdigest()


def load_vapoursynth(
    app_dir: str | os.PathLike[str],
    runtime: VSRuntimeConfig | dict[str, Any],
) -> Any:
    """从应用 portable tree 显式加载 VS；调用者必须是 worker。"""
    global _dll_directory_handle, _loaded_module
    config = _validated_runtime(runtime)
    media_dir = Path(app_dir).resolve() / "tools" / "media"
    pyd = media_dir / "vapoursynth.pyd"
    if sys.version_info < (3, 12):
        raise VSLoaderError("portable VapourSynth 需要 Python 3.12+")
    if not pyd.is_file():
        raise VSLoaderError(f"portable VapourSynth binding 不存在: {pyd}")

    with _load_lock:
        if _loaded_module is not None:
            return _loaded_module
        existing = sys.modules.get("vapoursynth")
        if existing is not None:
            raise VSLoaderError("worker 启动前已意外导入 vapoursynth")
        native_dirs = [str(Path(path)) for path in config.plugins.native_plugin_dirs]
        # 配置是唯一插件来源；空数组也必须清掉父进程可能继承的旧值。
        os.environ["VAPOURSYNTH_EXTRA_PLUGIN_PATH"] = os.pathsep.join(native_dirs)
        os.environ["ASSETMAKER_VS_PYTHON_DIRS_JSON"] = json.dumps(
            list(config.plugins.python_module_dirs),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            _dll_directory_handle = os.add_dll_directory(str(media_dir))
            spec = importlib.util.spec_from_file_location("vapoursynth", str(pyd))
            if spec is None or spec.loader is None:
                raise VSLoaderError(f"无法为 {pyd} 创建 import spec")
            module = importlib.util.module_from_spec(spec)
            sys.modules["vapoursynth"] = module
            spec.loader.exec_module(module)
            core = module.core
            if config.core.num_threads > 0:
                core.num_threads = config.core.num_threads
            if config.core.max_cache_size_mb > 0:
                core.max_cache_size = config.core.max_cache_size_mb
        except VSLoaderError:
            sys.modules.pop("vapoursynth", None)
            raise
        except BaseException as exc:
            sys.modules.pop("vapoursynth", None)
            raise VSLoaderError(f"加载 portable VapourSynth 失败: {exc}") from exc
        _loaded_module = module
        return module


__all__ = [
    "VSLoaderError",
    "compute_runtime_fingerprint",
    "load_vapoursynth",
]
