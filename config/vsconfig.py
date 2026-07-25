"""VapourSynth configuration model — the single source of truth for VS.

The export pipeline used to hardcode the VS plugin set, plugin directory and
colour/format defaults in Python across `core/media_pipeline.py` and
`core/media_tools.py` (three copies that could drift). They now live in one
schema-validated file, `config/vsconfig.json`, loaded here.

Mirrors `config/epconfig.py`: frozen dataclasses with paired `from_dict` /
`to_dict`, lenient defaults (a missing/partial file yields today's exact
literals, so behaviour is unchanged), and a `load_*` entry point that reads
`get_app_dir()/config/vsconfig.json`.

Adding a plugin later is a config edit: drop the DLL into `tools/media/vs-plugins/`
and add its namespace to `required_plugins` here — no Python edit needed to gate it.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Tuple

from utils.file_utils import get_app_dir

CONFIG_FILENAME = "vsconfig.json"

# Defaults below MUST equal the literals the code emitted before externalization
# (core/media_pipeline.py:200,267,271; core/media_tools.py:18,80): a missing
# vsconfig.json then produces byte-identical output.
_DEFAULT_REQUIRED_PLUGINS: Tuple[str, ...] = ("lsmas", "imwri")
_DEFAULT_EXTRA_PLUGIN_DIRS: Tuple[str, ...] = ("vs-plugins",)


@dataclass(frozen=True)
class MatrixHeuristic:
    """H.273 SD/HD split used to stamp an unspecified source `_Matrix`."""
    height_threshold: int = 720   # >= this height -> HD matrix, else SD
    hd_matrix: int = 1            # BT.709
    sd_matrix: int = 6            # BT.601 / SMPTE 170M

    def to_dict(self) -> Dict[str, Any]:
        return {
            "height_threshold": self.height_threshold,
            "hd_matrix": self.hd_matrix,
            "sd_matrix": self.sd_matrix,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "MatrixHeuristic":
        if not isinstance(data, dict):
            return cls()
        return cls(
            height_threshold=int(data.get("height_threshold", 720)),
            hd_matrix=int(data.get("hd_matrix", 1)),
            sd_matrix=int(data.get("sd_matrix", 6)),
        )


@dataclass(frozen=True)
class VSConfig:
    """VapourSynth runtime + pipeline configuration."""
    version: int = 1
    required_plugins: Tuple[str, ...] = _DEFAULT_REQUIRED_PLUGINS
    extra_plugin_dirs: Tuple[str, ...] = _DEFAULT_EXTRA_PLUGIN_DIRS
    image_source_format: str = "RGB24"
    output_format: str = "YUV420P8"
    resampler_kernel: str = "Bicubic"
    matrix_s: str = "170m"
    heuristic: MatrixHeuristic = field(default_factory=MatrixHeuristic)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "required_plugins": list(self.required_plugins),
            "extra_plugin_dirs": list(self.extra_plugin_dirs),
            "image_source_format": self.image_source_format,
            "output_format": self.output_format,
            "resampler_kernel": self.resampler_kernel,
            "colour": {
                "matrix_s": self.matrix_s,
                "heuristic": self.heuristic.to_dict(),
            },
        }

    @classmethod
    def from_dict(cls, data: Any) -> "VSConfig":
        if not isinstance(data, dict):
            return cls()
        colour = data.get("colour")
        colour = colour if isinstance(colour, dict) else {}
        req = data.get("required_plugins")
        dirs = data.get("extra_plugin_dirs")
        return cls(
            version=int(data.get("version", 1)),
            required_plugins=tuple(req) if isinstance(req, (list, tuple)) and req
            else _DEFAULT_REQUIRED_PLUGINS,
            extra_plugin_dirs=tuple(dirs) if isinstance(dirs, (list, tuple))
            else _DEFAULT_EXTRA_PLUGIN_DIRS,
            image_source_format=str(data.get("image_source_format", "RGB24")),
            output_format=str(data.get("output_format", "YUV420P8")),
            resampler_kernel=str(data.get("resampler_kernel", "Bicubic")),
            matrix_s=str(colour.get("matrix_s", "170m")),
            heuristic=MatrixHeuristic.from_dict(colour.get("heuristic")),
        )


def _vsconfig_path() -> Path:
    return Path(get_app_dir()) / "config" / CONFIG_FILENAME


@lru_cache(maxsize=1)
def load_vsconfig() -> VSConfig:
    """Load config/vsconfig.json (cached); fall back to dataclass defaults.

    Defaults reproduce today's hardcoded literals, so a missing file keeps the
    pipeline byte-identical. Call ``load_vsconfig.cache_clear()`` (also done by
    ``MediaToolchain.refresh()``) to pick up a runtime edit.
    """
    path = _vsconfig_path()
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            return VSConfig.from_dict(data)
    except (OSError, ValueError):
        pass
    return VSConfig()
