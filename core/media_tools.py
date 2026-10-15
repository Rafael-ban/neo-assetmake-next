"""Media tool discovery for the bundled preview and export pipeline."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from utils.file_utils import get_app_dir


REQUIRED_VAPOURSYNTH_PLUGINS = ("lsmas", "imwri")


def _exe_names(base_name: str) -> tuple[str, ...]:
    if sys.platform == "win32" and not base_name.lower().endswith(".exe"):
        return (f"{base_name}.exe", base_name)
    return (base_name,)


def _candidate_dirs(app_dir: Path) -> tuple[Path, ...]:
    return (
        app_dir / "tools" / "media",
        app_dir / "media",
        app_dir / "tools",
        app_dir,
    )


def _find_tool(app_dir: Path, names: Iterable[str]) -> str:
    for directory in _candidate_dirs(app_dir):
        for name in names:
            candidate = directory / name
            if candidate.is_file():
                return str(candidate)

    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return ""


def _resolve_tool_path(path: str) -> Optional[Path]:
    if not path:
        return None
    candidate = Path(path)
    if candidate.is_file():
        return candidate
    found = shutil.which(path)
    if found:
        return Path(found)
    return None


def _prepend_env_value(env: dict[str, str], name: str, value: Path) -> None:
    if not value.exists():
        return
    current = env.get(name, "")
    prefix = str(value)
    env[name] = prefix + (os.pathsep + current if current else "")


def build_media_subprocess_env(tool_path: str) -> dict[str, str]:
    """Build an environment suitable for bundled media subprocesses."""
    env = os.environ.copy()
    resolved = _resolve_tool_path(tool_path)
    media_dir = resolved.parent if resolved else Path(get_app_dir()) / "tools" / "media"

    _prepend_env_value(env, "PATH", media_dir)
    _prepend_env_value(env, "PYTHONPATH", media_dir / "Lib" / "site-packages")
    _prepend_env_value(
        env,
        "VAPOURSYNTH_EXTRA_PLUGIN_PATH",
        media_dir / "vs-plugins",
    )
    return env


@lru_cache(maxsize=16)
def _missing_vapoursynth_plugins(vspipe_path: str) -> tuple[str, ...]:
    resolved = _resolve_tool_path(vspipe_path)
    if resolved is None:
        return ()

    script_path = None
    script = "\n".join(
        [
            "import vapoursynth as vs",
            "core = vs.core",
            f"required = {REQUIRED_VAPOURSYNTH_PLUGINS!r}",
            "missing = [name for name in required if not hasattr(core, name)]",
            "if missing:",
            "    raise RuntimeError(",
            "        'missing VapourSynth plugin namespace(s): '",
            "        + ', '.join(missing)",
            "    )",
            "clip = core.std.BlankClip(",
            "    width=16, height=16, length=1, format=vs.YUV420P8",
            ")",
            "clip.set_output()",
            "",
        ]
    )

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            suffix=".vpy",
            delete=False,
        ) as handle:
            script_path = handle.name
            handle.write(script)

        run_kwargs = {
            "capture_output": True,
            "text": True,
            "timeout": 10,
            "env": build_media_subprocess_env(str(resolved)),
        }
        if sys.platform == "win32":
            run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(
            [str(resolved), "--info", script_path, "-"],
            **run_kwargs,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return (f"VapourSynth plugin check failed: {exc}",)
    finally:
        if script_path:
            try:
                os.remove(script_path)
            except OSError:
                pass

    if result.returncode == 0:
        return ()

    output = f"{result.stdout}\n{result.stderr}"
    missing = [name for name in REQUIRED_VAPOURSYNTH_PLUGINS if name in output]
    if not missing:
        details = output.strip().splitlines()
        tail = details[-1] if details else f"VSPipe exited {result.returncode}"
        return (f"VapourSynth plugin check failed: {tail}",)
    return tuple(f"VapourSynth plugin {name}" for name in missing)


@dataclass(frozen=True)
class MediaToolchain:
    """Resolved paths for the preview and export media toolchain."""

    mpv_path: str = ""
    vspipe_path: str = ""
    x264_path: str = ""
    muxer_path: str = ""

    @classmethod
    def discover(cls, app_dir: Optional[os.PathLike[str] | str] = None) -> "MediaToolchain":
        root = Path(app_dir) if app_dir is not None else Path(get_app_dir())
        return cls(
            mpv_path=_find_tool(root, _exe_names("mpv")),
            vspipe_path=_find_tool(root, _exe_names("VSPipe")),
            x264_path=_find_tool(root, ("x264-7mod.exe", "x264-7mod", "x264.exe", "x264")),
            muxer_path=_find_tool(
                root,
                (
                    "MP4Box.exe",
                    "MP4Box",
                    "mp4box.exe",
                    "mp4box",
                    "lsmash-muxer.exe",
                    "lsmash-muxer",
                    "muxer.exe",
                    "muxer",
                ),
            ),
        )

    def missing_for_export(self) -> list[str]:
        missing = []
        if not self.vspipe_path:
            missing.append("VSPipe")
        if not self.x264_path:
            missing.append("x264-7mod")
        if not self.muxer_path:
            missing.append("MP4Box or lsmash-muxer")
        if self.vspipe_path:
            missing.extend(_missing_vapoursynth_plugins(self.vspipe_path))
        return missing

    def missing_for_preview(self) -> list[str]:
        return [] if self.mpv_path else ["mpv"]

    def describe(self) -> str:
        parts = {
            "mpv": self.mpv_path,
            "VSPipe": self.vspipe_path,
            "x264-7mod": self.x264_path,
            "MP4 muxer": self.muxer_path,
        }
        return ", ".join(f"{name}={'found' if path else 'missing'}" for name, path in parts.items())
