"""VapourSynth configuration model — the single source of truth for VS.

The export pipeline used to hardcode the VS plugin set, plugin directory and
colour/format defaults in Python across `core/media_pipeline.py` and
`core/media_tools.py` (three copies that could drift). They now live in one
file, `config/vsconfig.json`, loaded here.

Validation is split on purpose:
* `schemas/vsconfig.schema.json` constrains the FILE SHIPPED IN THE REPO and is
  enforced only by `tests/test_vsconfig_contract.py` — it is not consulted at
  runtime, so it cannot protect against a user-edited file.
* `from_dict` below is what guards runtime: it degrades field-by-field (a bad
  value falls back to that field's default and logs a warning) instead of
  discarding the whole file.

Mirrors `config/epconfig.py`: frozen dataclasses with paired `from_dict` /
`to_dict`, lenient defaults (a missing/partial file yields today's exact
literals, so behaviour is unchanged), and a `load_*` entry point that reads
`get_app_dir()/config/vsconfig.json`.

Adding a plugin later is a config edit: drop the DLL into `tools/media/vs-plugins/`
and add its namespace to `required_plugins` here — no Python edit needed to gate it.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Tuple

from utils.file_utils import get_app_dir

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "vsconfig.json"


def _coerce_int(data: Dict[str, Any], key: str, default: int) -> int:
    """int(data[key]) with a per-FIELD fallback.

    Without this, one bad scalar aborted `from_dict` mid-construction: the
    ValueError propagated to `load_vsconfig`'s ``except (OSError, ValueError)``
    and the ENTIRE file was discarded silently, so a typo in `num_threads` also
    threw away a deliberate `matrix_s`. A non-dict value (e.g. ``version: {}``)
    raised TypeError instead, which that handler did NOT catch — same class of
    mistake, two different outcomes. Both now degrade to this one field.
    """
    raw = data.get(key)
    if raw is None:          # absent, or JSON null == "leave unset"
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "vsconfig: %s=%r is not an integer; using %r", key, raw, default
        )
        return default


def _coerce_str(data: Dict[str, Any], key: str, default: str) -> str:
    """str(data[key]) but reject values that cannot name a VS symbol.

    ``str(None)`` would happily yield the string ``"None"``, which then reaches
    `core.resize.<kernel>` / `vs.<format>` and fails far from its cause.
    """
    raw = data.get(key)
    if raw is None:          # absent, or JSON null == "leave unset"
        return default
    if isinstance(raw, str) and raw.strip():
        return raw
    logger.warning("vsconfig: %s=%r is not a non-empty string; using %r",
                   key, raw, default)
    return default

# Defaults below MUST equal the literals the code emitted before externalization
# (core/media_pipeline.py:200,267,271; core/media_tools.py:18,80): a missing
# vsconfig.json then produces byte-identical output.
_DEFAULT_REQUIRED_PLUGINS: Tuple[str, ...] = ("lsmas", "imwri")
_DEFAULT_EXTRA_PLUGIN_DIRS: Tuple[str, ...] = ("vs-plugins",)

# Kernel and format names are INTERPOLATED AS IDENTIFIERS — into `core.resize.X`
# and `vs.Y`, both in-process (core/vs_graph.py, core/vs_engine.py) and as text
# in the generated .vpy (core/vs_script.py). An unchecked value therefore had
# three different failure modes: silently falling back to Bicubic via
# `getattr(..., default)`, an AttributeError deep in the preview path, or a
# SyntaxError/NameError inside the VSPipe subprocess where the message has to
# travel back through a pipe. Validating once here makes all three impossible.
#
# The kernel list is `core.resize`'s full public surface on R73 (verified with
# `dir(core.resize)`): Bicubic, Bilinear, Bob, Lanczos, Point, Spline16,
# Spline36, Spline64. `Bob` is a deinterlacer, not a resampler — excluded.
_VALID_RESAMPLER_KERNELS: Tuple[str, ...] = (
    "Bicubic", "Bilinear", "Lanczos", "Point", "Spline16", "Spline36", "Spline64",
)


def _coerce_kernel(data: Dict[str, Any], key: str, default: str) -> str:
    """A resize kernel name that is known to exist on `core.resize`."""
    name = _coerce_str(data, key, default)
    if name in _VALID_RESAMPLER_KERNELS:
        return name
    logger.warning(
        "vsconfig: %s=%r is not a known resize kernel %s; using %r",
        key, name, _VALID_RESAMPLER_KERNELS, default,
    )
    return default


def _coerce_format(data: Dict[str, Any], key: str, default: str) -> str:
    """A `vs.<NAME>` preset format identifier (shape-checked, not resolved).

    Resolving against the real `vs` module is impossible here: importing
    VapourSynth must happen before PyQt6 loads (see core/vs_engine.prewarm) and
    config must stay importable with no VS at all. So this only rejects values
    that could not be an identifier — `core/media_tools.py`'s plugin probe is
    what proves the format actually exists, out of process.
    """
    name = _coerce_str(data, key, default)
    if name.isidentifier():
        return name
    logger.warning("vsconfig: %s=%r is not a valid identifier; using %r",
                   key, name, default)
    return default


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
            height_threshold=_coerce_int(data, "height_threshold", 720),
            hd_matrix=_coerce_int(data, "hd_matrix", 1),
            sd_matrix=_coerce_int(data, "sd_matrix", 6),
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
    # In-process core tuning (0 = leave VapourSynth's own default: thread count
    # = CPU count, cache = 4096MB as measured on R73).
    num_threads: int = 0
    max_cache_size_mb: int = 0

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
            "core": {
                "num_threads": self.num_threads,
                "max_cache_size_mb": self.max_cache_size_mb,
            },
        }

    @classmethod
    def from_dict(cls, data: Any) -> "VSConfig":
        if not isinstance(data, dict):
            return cls()
        colour = data.get("colour")
        colour = colour if isinstance(colour, dict) else {}
        core_cfg = data.get("core")
        core_cfg = core_cfg if isinstance(core_cfg, dict) else {}
        req = data.get("required_plugins")
        dirs = data.get("extra_plugin_dirs")
        return cls(
            version=_coerce_int(data, "version", 1),
            # An EMPTY list deliberately falls back to the default set (and the
            # schema forbids it via minItems: 1) — "gate on nothing" is not an
            # expressible intent. tests/test_vsconfig_contract.py pins this.
            required_plugins=tuple(req) if isinstance(req, (list, tuple)) and req
            else _DEFAULT_REQUIRED_PLUGINS,
            extra_plugin_dirs=tuple(dirs) if isinstance(dirs, (list, tuple))
            else _DEFAULT_EXTRA_PLUGIN_DIRS,
            image_source_format=_coerce_format(data, "image_source_format", "RGB24"),
            output_format=_coerce_format(data, "output_format", "YUV420P8"),
            resampler_kernel=_coerce_kernel(data, "resampler_kernel", "Bicubic"),
            matrix_s=_coerce_str(colour, "matrix_s", "170m"),
            heuristic=MatrixHeuristic.from_dict(colour.get("heuristic")),
            num_threads=_coerce_int(core_cfg, "num_threads", 0),
            max_cache_size_mb=_coerce_int(core_cfg, "max_cache_size_mb", 0),
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
    if not path.is_file():
        return VSConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        logger.warning("vsconfig: cannot read %s (%s); using defaults", path, exc)
        return VSConfig()
    except ValueError as exc:
        # Malformed JSON is the only remaining whole-file failure: `from_dict`
        # is total (every field degrades individually), so a single bad value no
        # longer discards the rest of the file.
        logger.warning("vsconfig: %s is not valid JSON (%s); using defaults",
                       path, exc)
        return VSConfig()
    return VSConfig.from_dict(data)
