"""Build the export filter graph PROGRAMMATICALLY (in-process twin of vs_script).

``core/vs_script.py`` emits a ``.vpy`` for the VSPipe→x264 export subprocess and
its output is pinned byte-for-byte by ``tests/test_vpy_golden.py``. This module
builds the SAME graph as live ``VideoNode`` objects so the preview can render
the exported pixels instead of a parallel approximation.

The step order here mirrors ``vs_script.write_vpy_script`` exactly and the order
is load-bearing (crop before loop; padding after the resize). Both consume the
same ``VideoExportParams`` and the same ``VSConfig``, so the two constructions
stay in step; ``tests/test_preview_export_parity`` is what proves it.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Optional

from config.constants import get_resolution_spec
from config.vsconfig import VSConfig, load_vsconfig

logger = logging.getLogger(__name__)


def _resizer(core: Any, kernel: str):
    """Delegate to the single authoritative lookup (raises on unknown names).

    This used to be `getattr(core.resize, kernel, core.resize.Bicubic)` — a
    misspelt kernel silently became Bicubic, so the config looked honoured when
    it was not.
    """
    from core import vs_engine

    return vs_engine.resize_filter(core, kernel)


def build_export_graph(params, *, config: Optional[VSConfig] = None) -> Any:
    """Return the VideoNode the export would encode (ends at the output format).

    Mirrors write_vpy_script step for step: source → trim → rotation → crop
    (ratio-preserving) → loop-if-image → colour convert → padding → final 180.
    """
    from core import vs_engine

    cfg = config or load_vsconfig()
    vs = vs_engine.load_vapoursynth()
    core = vs_engine.get_core(cfg)

    spec = get_resolution_spec(params.resolution)
    target_w = int(spec["width"])
    target_h = int(spec["height"])
    padded_w = int(spec["padded_width"])
    padded_h = int(spec["padded_height"])
    padding_side = spec["padding_side"]
    rotate_180 = bool(spec["rotate_180"])

    start_frame = max(0, int(params.start_frame))
    # clip[a:a] is an EMPTY clip — a degenerate trim must still yield one frame.
    end_frame = max(start_frame + 1, int(params.end_frame))
    crop_x, crop_y, crop_w, crop_h = [max(0, int(v)) for v in params.cropbox]
    rotation = (round(int(params.rotation) / 90) * 90) % 360

    is_image = bool(params.is_image)
    clip = vs_engine.source_clip(params.video_path, is_image=is_image)
    # Capture the SOURCE height before rotate/crop — the SD/HD matrix heuristic
    # below describes how the SOURCE was encoded, so our own editing must not
    # influence it. Measured after CropAbs, a crop dipping under the threshold
    # flipped the guessed matrix and visibly changed exported colours (probed:
    # a real BT.709 source cropped 800 -> 718 high went from ΔE 1 to ΔE 22).
    src_height = clip.height
    if not is_image:
        clip = clip[start_frame:end_frame]

    # --- rotation: Transpose is a reflection, not a rotation (matches cv2) ---
    if rotation == 90:
        clip = core.std.FlipHorizontal(core.std.Transpose(clip))
    elif rotation == 180:
        clip = core.std.Turn180(clip)
    elif rotation == 270:
        clip = core.std.FlipVertical(core.std.Transpose(clip))

    # --- crop: clamp to the real post-rotation size, even-aligned, ratio-safe ---
    if crop_w > 0 and crop_h > 0:
        target_ar = (target_w / target_h) if target_h else 0.0
        cx = min(max(0, crop_x), max(0, clip.width - 2)) & ~1
        cy = min(max(0, crop_y), max(0, clip.height - 2)) & ~1
        cw = min(crop_w, clip.width - cx)
        ch = min(crop_h, clip.height - cy)
        if target_ar > 0 and crop_w > 0 and crop_h > 0:
            # One shared scale so an overrunning box shrinks proportionally
            # instead of collapsing on a single axis (which the resize below
            # would turn into anisotropic stretching).
            scale = min(cw / float(crop_w), ch / float(crop_h))
            cw = int(min(cw, round(crop_w * scale)))
            ch = int(min(ch, round(cw / target_ar)))
            cw = int(min(cw, round(ch * target_ar)))
        cw &= ~1
        ch &= ~1
        if cw >= 2 and ch >= 2:
            clip = core.std.CropAbs(clip, width=cw, height=ch, left=cx, top=cy)

    if is_image:
        # Loop AFTER rotate/crop: one processed frame repeated.
        clip = core.std.Loop(clip, times=max(1, end_frame - start_frame))

    # --- colour: normalize to the configured output format/matrix ---
    resizer = _resizer(core, cfg.resampler_kernel)
    out_fmt = vs_engine.preset_format(vs, cfg.output_format)
    if not is_image:
        matrix = clip.get_frame(0).props.get("_Matrix", 2)
        if matrix == 2:
            h = cfg.heuristic
            stamped = h.hd_matrix if src_height >= h.height_threshold else h.sd_matrix
            clip = core.std.SetFrameProps(clip, _Matrix=stamped)
    clip = resizer(clip, width=target_w, height=target_h,
                   format=out_fmt, matrix_s=cfg.matrix_s)

    if padding_side == "right" and padded_w > target_w:
        clip = core.std.AddBorders(clip, right=padded_w - target_w)
    elif padding_side == "bottom" and padded_h > target_h:
        clip = core.std.AddBorders(clip, bottom=padded_h - target_h)

    if rotate_180:
        clip = core.std.Turn180(clip)

    return clip


def build_source_graph(video_path: str, *, is_image: bool = False,
                       rotation: int = 0, config: Optional[VSConfig] = None) -> Any:
    """Source (optionally rotated) converted to RGB24 for on-screen display.

    Used when the preview shows the *whole* frame with a crop rectangle drawn
    over it; ``build_export_graph`` + ``to_display_rgb_clip`` is what shows the
    cropped/scaled result the device will get.
    """
    from core import vs_engine
    from core.vs_frame import to_display_rgb_clip

    cfg = config or load_vsconfig()
    vs = vs_engine.load_vapoursynth()
    core = vs_engine.get_core(cfg)
    clip = vs_engine.source_clip(video_path, is_image=is_image)

    rotation = (round(int(rotation) / 90) * 90) % 360
    if rotation == 90:
        clip = core.std.FlipHorizontal(core.std.Transpose(clip))
    elif rotation == 180:
        clip = core.std.Turn180(clip)
    elif rotation == 270:
        clip = core.std.FlipVertical(core.std.Transpose(clip))

    return to_display_rgb_clip(clip, vs, cfg.resampler_kernel)


def build_display_graph(params, *, config: Optional[VSConfig] = None) -> Any:
    """The export graph converted back to RGB24 — literally 看到什么=导出什么."""
    from core import vs_engine
    from core.vs_frame import to_display_rgb_clip

    cfg = config or load_vsconfig()
    vs = vs_engine.load_vapoursynth()
    return to_display_rgb_clip(build_export_graph(params, config=cfg), vs,
                               cfg.resampler_kernel)


# Zoom is a VIEWPORT magnifier, not a whole-frame resize. Rendering the full
# frame at 100x measured 38400x64000 = 7.37 GB per RGB24 frame and 9.3 s to
# pull frame 0 (probed on this bundle) — and the viewport can only ever show
# ~1000x1800 of it. Cropping the source window FIRST keeps the cost flat at
# roughly viewport size regardless of the factor.
def apply_preview_zoom(clip, *, zoom_factor: float, viewport: tuple[int, int],
                       pan: tuple[float, float] = (0.5, 0.5),
                       kernel: str = "Point",
                       config: Optional[VSConfig] = None) -> Any:
    """Magnify the region of ``clip`` the viewport can actually show.

    Args:
        clip: RGB24 display clip (from build_display_graph/build_source_graph).
        zoom_factor: 1.0 = fit as-is; 100.0 = 10000%.
        viewport: (width, height) of the on-screen area, in device pixels.
        pan: Normalised centre of interest in (0..1, 0..1) source coordinates.
        kernel: resize kernel; ``Point`` keeps pixel edges hard so single
            source pixels stay countable at high factors (that is the point of
            a 10000% zoom — checking crop boundaries pixel by pixel).

    Returns:
        A clip no larger than the viewport, or ``clip`` unchanged at 1.0x.
    """
    from core import vs_engine

    if zoom_factor <= 1.0:
        return clip

    cfg = config or load_vsconfig()
    core = vs_engine.get_core(cfg)

    vw = max(2, int(viewport[0]))
    vh = max(2, int(viewport[1]))
    # The source window that maps onto the viewport at this magnification.
    win_w = max(2, min(clip.width, int(math.ceil(vw / zoom_factor))))
    win_h = max(2, min(clip.height, int(math.ceil(vh / zoom_factor))))
    # CropAbs needs even offsets/sizes for subsampled formats; RGB24 is not
    # subsampled, but staying even keeps this valid if the format ever changes.
    win_w &= ~1
    win_h &= ~1
    win_w = max(2, win_w)
    win_h = max(2, win_h)

    cx = min(max(float(pan[0]), 0.0), 1.0) * clip.width
    cy = min(max(float(pan[1]), 0.0), 1.0) * clip.height
    left = int(min(max(cx - win_w / 2.0, 0), clip.width - win_w)) & ~1
    top = int(min(max(cy - win_h / 2.0, 0), clip.height - win_h)) & ~1

    clip = core.std.CropAbs(clip, width=win_w, height=win_h, left=left, top=top)
    return _resizer(core, kernel)(clip, width=vw, height=vh)
