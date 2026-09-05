"""VapourSynth (.vpy) script authoring — decoupled from the encode pipeline.

This module owns "VS itself": how an export track becomes a VapourSynth
script. The plugin/colour/format knobs come from ``config/vsconfig.json`` via
``config.vsconfig.load_vsconfig`` (the single source of truth), so adding a
filter is a localized edit here (one builder method + one call in
``write_vpy_script``) and adding a plugin to the gate is a JSON edit — neither
requires touching ``core/media_pipeline.py``.

VS plugins are addressed by namespace on ``core`` and are 100% AUTOLOADED from
the plugin dirs (VS R73: tools/media/vapoursynth-stubs/__init__.pyi Core
properties ~:1505-1534; ``core.std.LoadPlugin`` exists at ~:1116 but is never
called), so the emitted script contains no explicit load lines.

M5 起，生产导出通过固定 runner 消费冻结的用户脚本与 job。为 M7 的
兼容性迁移保留，``core.media_pipeline`` 暂时 re-export 本模块的 writer，
但 M5 的 ExportWorker/MainWindow 生产调用点不得使用它。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from config.constants import get_resolution_spec
from config.vsconfig import VSConfig, load_vsconfig


def _vs_path(path: str) -> str:
    return Path(path).as_posix()


def _quote_vs_string(value: str) -> str:
    return repr(_vs_path(value))


def _quote_vs_literal(value: str) -> str:
    """Quote a plain string VALUE (not a path) for embedding in the .vpy.

    `matrix_s` reaches the script as `matrix_s='...'` built by hand, so a value
    containing a quote produced a .vpy that fails to parse — inside the VSPipe
    subprocess, far from the config file that caused it. `repr()` of the default
    '170m' is byte-identical to the old hand-written form, so goldens are
    unaffected. Identifier positions (kernel/format names) are whitelisted in
    config/vsconfig.py instead, since they cannot be quoted.
    """
    return repr(value)


class VpyScriptBuilder:
    """Assemble a .vpy line-by-line; one method per filter step.

    Each method appends the exact string the monolithic generator emitted, but
    reads its data knobs (source/output format, resampler kernel, colour matrix
    and the SD/HD heuristic) from ``VSConfig`` — so the default config yields
    byte-identical output while a config change is a one-line data edit.
    """

    def __init__(self, config: Optional[VSConfig] = None):
        self._cfg = config or load_vsconfig()
        self._lines: List[str] = []

    def header(self) -> "VpyScriptBuilder":
        self._lines.append("import vapoursynth as vs")
        self._lines.append("core = vs.core")
        return self

    def source_image(self, path: str) -> "VpyScriptBuilder":
        fmt = self._cfg.image_source_format
        kernel = self._cfg.resampler_kernel
        self._lines.append(f"clip = core.imwri.Read({_quote_vs_string(path)})")
        self._lines.append(
            f"clip = clip if clip.format.id == vs.{fmt} else "
            f"core.resize.{kernel}(clip, format=vs.{fmt})"
        )
        return self

    def source_video(self, path: str, cache_file: str, start: int, end: int) -> "VpyScriptBuilder":
        # cachefile keeps the lsmas .lwi index beside our own .vpy (VS R73 stub
        # LWLibavSource(source, ..., cachefile, ...)), not next to the user's
        # source video where nothing would clean it up.
        self._lines.append(
            f"clip = core.lsmas.LWLibavSource({_quote_vs_string(path)}, "
            f"cachefile={_quote_vs_string(cache_file)})"
        )
        # Capture the SOURCE height before rotate/crop: the SD/HD matrix
        # heuristic in colour_convert() describes how the SOURCE was encoded,
        # which our own editing must not influence. Measuring it after CropAbs
        # made a crop that dips below the threshold flip the guessed matrix, so
        # dragging the crop box visibly changed exported colours.
        self._lines.append("_src_height = clip.height")
        self._lines.append(f"clip = clip[{start}:{end}]")
        return self

    def rotation(self, degrees: int) -> "VpyScriptBuilder":
        # Match cv2.ROTATE_* (video_preview.py): Transpose is a reflection, not a
        # rotation — 90cw = Transpose+FlipHorizontal, 270 = Transpose+FlipVertical,
        # 180 = Turn180. `degrees` is pre-snapped to {0,90,180,270}.
        if degrees == 90:
            self._lines.append("clip = core.std.FlipHorizontal(core.std.Transpose(clip))")
        elif degrees == 180:
            self._lines.append("clip = core.std.Turn180(clip)")
        elif degrees == 270:
            self._lines.append("clip = core.std.FlipVertical(core.std.Transpose(clip))")
        return self

    def crop(self, x: int, y: int, w: int, h: int, target_ar: float = 0.0) -> "VpyScriptBuilder":
        """Emit an eval-time clamped, even-aligned, RATIO-PRESERVING CropAbs.

        Clamping is needed because CropAbs on YUV420 rejects odd/oversized
        boxes and aborts the encode. It used to clamp width and height
        INDEPENDENTLY, which changes the box's aspect ratio whenever it
        overruns one axis (e.g. a 600x1066 box at x=1500 on a 1920-wide clip
        became 420x1066: ratio 0.563 -> 0.394). Since `colour_convert` then
        resizes straight to the target with no aspect-preserving parameter
        (VS R73 `resize.Bicubic` takes independent width/height), that shows up
        as anisotropic stretching in the exported video. When `target_ar` is
        given, both axes are shrunk by ONE shared factor so the ratio survives.
        """
        if w <= 0 or h <= 0:
            return self
        self._lines.append(f"_cx = min(max(0, {x}), clip.width - 2) & ~1")
        self._lines.append(f"_cy = min(max(0, {y}), clip.height - 2) & ~1")
        self._lines.append(f"_cw = min({w}, clip.width - _cx)")
        self._lines.append(f"_ch = min({h}, clip.height - _cy)")
        if target_ar > 0:
            # Shrink proportionally to the tighter axis, then re-derive the
            # other from the target ratio (single scale => ratio preserved).
            self._lines.append(f"_ar = {target_ar!r}")
            self._lines.append("_scale = min(_cw / ({0} * 1.0), _ch / ({1} * 1.0))".format(w, h))
            self._lines.append(f"_cw = int(min(_cw, round({w} * _scale)))")
            self._lines.append("_ch = int(min(_ch, round(_cw / _ar)))")
            self._lines.append("_cw = int(min(_cw, round(_ch * _ar)))")
        self._lines.append("_cw = _cw & ~1")
        self._lines.append("_ch = _ch & ~1")
        self._lines.append("if _cw >= 2 and _ch >= 2:")
        self._lines.append("    clip = core.std.CropAbs(clip, width=_cw, height=_ch, left=_cx, top=_cy)")
        return self

    def loop(self, times: int) -> "VpyScriptBuilder":
        self._lines.append(f"clip = core.std.Loop(clip, times={max(1, times)})")
        return self

    def colour_convert(self, target_w: int, target_h: int, is_image: bool) -> "VpyScriptBuilder":
        cfg = self._cfg
        if not is_image:
            # zimg reads the input matrix from _Matrix; when a source leaves it
            # unspecified (2), stamp the H.273 resolution heuristic, then convert.
            h = cfg.heuristic
            self._lines.append("if clip.get_frame(0).props.get('_Matrix', 2) == 2:")
            self._lines.append(
                f"    clip = core.std.SetFrameProps(clip, "
                f"_Matrix={h.hd_matrix} if _src_height >= {h.height_threshold} else {h.sd_matrix})"
            )
        self._lines.append(
            f"clip = core.resize.{cfg.resampler_kernel}(clip, width={target_w}, height={target_h}, "
            f"format=vs.{cfg.output_format}, matrix_s='{cfg.matrix_s}')"
        )
        return self

    def padding(self, side: str, amount: int) -> "VpyScriptBuilder":
        if side == "right":
            self._lines.append(f"clip = core.std.AddBorders(clip, right={amount})")
        elif side == "bottom":
            self._lines.append(f"clip = core.std.AddBorders(clip, bottom={amount})")
        return self

    def final_turn180(self) -> "VpyScriptBuilder":
        self._lines.append("clip = core.std.Turn180(clip)")
        return self

    def set_output(self) -> "VpyScriptBuilder":
        self._lines.append("clip.set_output()")
        return self

    def render(self) -> str:
        return "\n".join(self._lines) + "\n"


def write_vpy_script(script_path: "str | os.PathLike[str]", params) -> None:
    """Write a VapourSynth script for one export track.

    Thin orchestrator: resolve the target spec + trim/rotation guards, then
    drive ``VpyScriptBuilder`` in the load-bearing order (crop before loop).
    """
    spec = get_resolution_spec(params.resolution)
    target_w = int(spec["width"])
    target_h = int(spec["height"])
    padded_w = int(spec["padded_width"])
    padded_h = int(spec["padded_height"])
    padding_side = spec["padding_side"]
    rotate_180 = bool(spec["rotate_180"])

    start_frame = max(0, int(params.start_frame))
    # start_frame + 1: a video clip[a:a] is EMPTY and aborts the encode with an
    # opaque y4m error — a degenerate trim must still yield one frame.
    end_frame = max(start_frame + 1, int(params.end_frame))
    crop_x, crop_y, crop_w, crop_h = [max(0, int(v)) for v in params.cropbox]
    # Snap rotation to a cardinal angle so export never diverges from the preview.
    rotation = (round(int(params.rotation) / 90) * 90) % 360

    builder = VpyScriptBuilder().header()

    if params.is_image:
        builder.source_image(params.video_path)
    else:
        cache_file = str(Path(script_path).with_suffix(".lwi"))
        builder.source_video(params.video_path, cache_file, start_frame, end_frame)

    # Rotation + crop apply to BOTH branches (format-generic VS ops).
    # target_ar makes the eval-time clamp shrink both axes by one shared factor,
    # so a box that overruns the frame can no longer come out anisotropically
    # stretched by the resize below (the GUI locks the ratio too; this is the
    # defence-in-depth for historical//programmatic boxes).
    builder.rotation(rotation)
    builder.crop(crop_x, crop_y, crop_w, crop_h,
                 target_ar=(target_w / target_h) if target_h else 0.0)

    if params.is_image:
        # Loop AFTER rotate/crop: one processed frame repeated.
        builder.loop(end_frame - start_frame)

    builder.colour_convert(target_w, target_h, params.is_image)

    if padding_side == "right":
        builder.padding("right", padded_w - target_w)
    elif padding_side == "bottom":
        builder.padding("bottom", padded_h - target_h)

    if rotate_180:
        builder.final_turn180()

    builder.set_output()

    Path(script_path).write_text(builder.render(), encoding="utf-8")
