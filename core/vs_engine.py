"""In-process VapourSynth engine: module loading, core singleton, source nodes.

This is the entry point for using VapourSynth as an IN-PROCESS rendering core
(``clip.get_frame(n)``) rather than only as the ``VSPipe.exe`` subprocess that
``core/media_pipeline.py`` drives for export.

Loading strategy (both halves are load-bearing; each was verified empirically):

1. ``os.add_dll_directory(<tools/media>)`` + ``importlib`` explicit-spec load of
   ``tools/media/vapoursynth.pyd``. We must NOT ``sys.path.insert`` that
   directory: it is a flat embedded CPython distribution (``_ctypes.pyd``,
   ``_socket.pyd``, ``python312.dll`` …) and shadowing the host interpreter's
   extension modules breaks ``ctypes`` outright ("class must define a '_type_'
   attribute").
2. We must NOT pip-install the bundled wheel into the venv either: VapourSynth
   autoloads plugins from directories resolved relative to the *loaded*
   ``VapourSynth.dll`` (the ``portable.vs`` marker + ``vs-plugins`` /
   ``vs-coreplugins`` next to it), so a venv-installed copy would silently find
   no ``lsmas``/``imwri``. Loading from ``tools/media`` gets them for free.

Requires Python >= 3.12: the bundled wheel is ``cp312-abi3`` (stable ABI with a
3.12 floor) and its ``.pyd`` imports symbols absent from 3.11's ``python3.dll``.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any, Optional

from config.vsconfig import VSConfig, load_vsconfig
from core.media_tools import MediaToolchain
from utils.file_utils import get_app_dir

logger = logging.getLogger(__name__)

_MODULE_NAME = "vapoursynth"
_PYD_NAME = "vapoursynth.pyd"

_lock = threading.Lock()
_vs_module: Optional[Any] = None
_core: Optional[Any] = None
_source_cache: dict = {}


class VSUnavailable(RuntimeError):
    """VapourSynth could not be loaded, or a required plugin is missing."""


def vs_dir() -> Path:
    """Directory holding vapoursynth.pyd / VapourSynth.dll / vs-plugins.

    Anchored on the discovered VSPipe (same bundle) so a PATH-provided or
    relocated toolchain keeps working; falls back to <app>/tools/media.
    """
    toolchain = MediaToolchain.discover()
    if toolchain.vspipe_path:
        parent = Path(toolchain.vspipe_path).parent
        if (parent / _PYD_NAME).is_file():
            return parent
    return Path(get_app_dir()) / "tools" / "media"


def load_vapoursynth() -> Any:
    """Import the bundled vapoursynth module in-process (cached)."""
    global _vs_module
    with _lock:
        if _vs_module is not None:
            return _vs_module

        if sys.version_info < (3, 12):
            raise VSUnavailable(
                "VapourSynth 需要 Python 3.12+(捆绑 wheel 为 cp312-abi3);"
                f"当前为 {sys.version_info.major}.{sys.version_info.minor}"
            )

        base = vs_dir()
        pyd = base / _PYD_NAME
        if not pyd.is_file():
            raise VSUnavailable(f"未找到 VapourSynth 绑定: {pyd}")

        existing = sys.modules.get(_MODULE_NAME)
        if existing is not None:  # someone already imported it — reuse
            _vs_module = existing
            return _vs_module

        try:
            # add_dll_directory lets vapoursynth.pyd resolve VapourSynth.dll
            # (and lets that DLL anchor its plugin autoload dirs) without
            # touching sys.path.
            with os.add_dll_directory(str(base)):
                spec = importlib.util.spec_from_file_location(_MODULE_NAME, str(pyd))
                if spec is None or spec.loader is None:
                    raise VSUnavailable(f"无法为 {pyd} 创建模块 spec")
                module = importlib.util.module_from_spec(spec)
                sys.modules[_MODULE_NAME] = module
                spec.loader.exec_module(module)
        except VSUnavailable:
            raise
        except Exception as exc:  # ImportError / OSError / …
            sys.modules.pop(_MODULE_NAME, None)
            raise VSUnavailable(f"加载 VapourSynth 失败: {exc}") from exc

        _vs_module = module
        logger.info("VapourSynth loaded in-process from %s", base)
        return _vs_module


def _assert_qt_not_loaded() -> None:
    """Refuse to initialize the VS core after PyQt6 has been imported.

    HARD ORDERING CONSTRAINT, established empirically on this bundle: creating
    the VapourSynth core in a process where any ``PyQt6`` extension module is
    already imported SEGFAULTS (exit 139) — reproduced with QtCore, QtGui,
    QtWidgets and QtNetwork individually, with and without a QApplication
    instance, and it is unaffected by DLL-directory handling or pre-loading
    VapourSynth.dll. The reverse order is clean: VS core first, then Qt (any
    number of modules, plus a QApplication) works and can pull real frames.

    So the core MUST be warmed before Qt loads — see ``prewarm()``, called at
    the very top of main.py. Raising here converts an unattributable crash into
    an actionable error.
    """
    if _core is not None:
        return
    qt_loaded = [name for name in sys.modules if name.startswith("PyQt6.Qt")]
    if qt_loaded:
        raise VSUnavailable(
            "必须在导入 PyQt6 之前初始化 VapourSynth core"
            f"(已加载: {', '.join(sorted(qt_loaded)[:3])});"
            "请在 main.py 最早处调用 core.vs_engine.prewarm()"
        )


def prewarm() -> bool:
    """Initialize the VS core BEFORE Qt is imported. Returns success.

    Safe to call unconditionally at process start: a missing/broken bundle is
    logged and swallowed so the app still launches (export keeps using the
    VSPipe subprocess, and the metadata probe falls back to mpv).
    """
    try:
        get_core()
        return True
    except Exception as exc:
        logger.warning("VapourSynth prewarm skipped: %s", exc)
        return False


def get_core(config: Optional[VSConfig] = None) -> Any:
    """Return the process-wide vs.core, configured from VSConfig (cached)."""
    global _core
    _assert_qt_not_loaded()
    vs = load_vapoursynth()
    with _lock:
        if _core is not None:
            return _core
        core = vs.core
        cfg = config or load_vsconfig()
        threads = int(getattr(cfg, "num_threads", 0) or 0)
        if threads > 0:
            core.num_threads = threads
        cache_mb = int(getattr(cfg, "max_cache_size_mb", 0) or 0)
        if cache_mb > 0:
            core.max_cache_size = cache_mb
        _core = core
        logger.info(
            "VS core R%s ready (num_threads=%s, max_cache_size=%sMB)",
            core.core_version.release_major, core.num_threads, core.max_cache_size,
        )
        return _core


def available_plugins() -> set:
    """Namespaces of every autoloaded plugin."""
    core = get_core()
    return {p.namespace for p in core.plugins()}


def missing_plugins(config: Optional[VSConfig] = None) -> tuple:
    """Required-but-absent plugin namespaces, per VSConfig (single source of truth).

    Mirrors the out-of-process probe in core/media_tools._plugin_probe_script,
    which checks `hasattr(core, name)`; VS R73 exposes each plugin as a
    namespace property on Core, so namespace membership is the right test.
    """
    cfg = config or load_vsconfig()
    try:
        present = available_plugins()
    except VSUnavailable:
        return tuple(cfg.required_plugins)
    return tuple(n for n in cfg.required_plugins if n not in present)


def verify_plugins(config: Optional[VSConfig] = None) -> None:
    """Raise VSUnavailable if a required plugin did not autoload."""
    missing = missing_plugins(config)
    if missing:
        raise VSUnavailable(
            "缺少 VapourSynth 插件: " + ", ".join(missing)
            + f"(应位于 {vs_dir() / 'vs-plugins'})"
        )


def lwi_cache_path(video_path: str) -> str:
    """Stable per-app .lwi index location for PREVIEW sources.

    Export keeps its index beside the generated .vpy in the staging dir and
    deletes it afterwards (core/vs_script.py). Preview has no staging dir, so
    park the index in an app cache keyed by the absolute path: the user's media
    folder stays clean and a second load of the same file skips the index build.
    """
    import hashlib

    resolved = os.path.abspath(video_path)
    digest = hashlib.sha1(resolved.encode("utf-8", "replace")).hexdigest()[:16]
    cache_dir = Path(get_app_dir()) / ".cache" / "lwi"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return str(cache_dir / f"{Path(resolved).stem}_{digest}.lwi")


def source_clip(video_path: str, *, is_image: bool = False, use_cache: bool = True):
    """Open a media file as a VideoNode (cached per path+kind).

    Video goes through lsmas.LWLibavSource (the same source the export script
    uses); images through imwri.Read normalized to the configured RGB format.
    The cache stops the metadata probe and the preview from each building the
    same .lwi index.
    """
    verify_plugins()
    vs = load_vapoursynth()
    core = get_core()
    cfg = load_vsconfig()
    key = (os.path.abspath(video_path), bool(is_image))
    if use_cache:
        cached = _source_cache.get(key)
        if cached is not None:
            return cached

    if is_image:
        clip = core.imwri.Read(video_path)
        want = getattr(vs, cfg.image_source_format, None)
        if want is not None and clip.format.id != want:
            clip = getattr(core.resize, cfg.resampler_kernel)(clip, format=want)
    else:
        clip = core.lsmas.LWLibavSource(
            video_path, cachefile=lwi_cache_path(video_path)
        )

    if use_cache:
        _source_cache[key] = clip
    return clip


def clear_caches() -> None:
    """Drop cached source nodes (call when media on disk may have changed)."""
    with _lock:
        _source_cache.clear()


def reset_for_tests() -> None:
    """Test hook: forget the module/core/source caches (does not unload the DLL)."""
    global _vs_module, _core
    with _lock:
        _vs_module = None
        _core = None
        _source_cache.clear()
