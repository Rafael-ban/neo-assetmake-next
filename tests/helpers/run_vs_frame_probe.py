"""Clean interpreter helper for tests that must construct a VapourSynth core.

Do not import PyQt here. The parent test module intentionally stays VS-free so
it can run beside Qt preview worker tests without triggering the bundled
runtime's PyQt-before-VS crash.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]


def _frame_contract() -> dict[str, object]:
    from core import vs_engine
    from core.vs_frame import frame_to_bgr, request_bgr_frame, to_display_rgb_clip

    vs = vs_engine.load_vapoursynth()
    core = vs_engine.get_core()

    red = core.std.BlankClip(
        width=64, height=32, length=1, format=vs.RGB24, color=[255, 0, 0]
    )
    red_bgr = request_bgr_frame(red, 0)
    assert red_bgr is not None
    assert red_bgr.shape == (32, 64, 3)
    assert red_bgr.dtype == np.uint8
    assert tuple(int(v) for v in red_bgr[0, 0]) == (0, 0, 255)

    blue = core.std.BlankClip(
        width=8, height=8, length=1, format=vs.RGB24, color=[0, 0, 255]
    )
    blue_bgr = request_bgr_frame(blue, 0)
    assert blue_bgr is not None
    assert tuple(int(v) for v in blue_bgr[0, 0]) == (255, 0, 0)

    # Width 360 has a measured 384-byte plane stride in the bundled core.
    green = core.std.BlankClip(
        width=360, height=16, length=1, format=vs.RGB24, color=[0, 255, 0]
    )
    green_bgr = request_bgr_frame(green, 0)
    assert green_bgr is not None
    assert green_bgr.shape == (16, 360, 3)
    assert bool((green_bgr[:, :, 1] == 255).all())
    assert bool((green_bgr[:, :, 0] == 0).all())

    owned = core.std.BlankClip(
        width=32, height=16, length=1, format=vs.RGB24, color=[10, 20, 30]
    ).get_frame(0)
    try:
        owned_bgr = frame_to_bgr(owned)
    finally:
        owned.close()
    assert owned_bgr is not None
    assert tuple(int(v) for v in owned_bgr[0, 0]) == (30, 20, 10)
    assert int(owned_bgr.sum()) == 32 * 16 * 60

    gray = core.std.BlankClip(width=16, height=16, length=1, format=vs.GRAY8)
    gray_frame = gray.get_frame(0)
    try:
        assert frame_to_bgr(gray_frame) is None
    finally:
        gray_frame.close()

    yuv = core.std.BlankClip(width=48, height=32, length=1, format=vs.YUV420P8)
    rgb = to_display_rgb_clip(yuv, vs)
    assert rgb.format.id == vs.RGB24
    assert to_display_rgb_clip(rgb, vs) is rgb
    return {"status": "ok"}


def _source_cache(path: Path) -> dict[str, object]:
    from core import vs_engine

    vs_engine.clear_caches()
    first = vs_engine.source_clip(str(path))
    second = vs_engine.source_clip(str(path))
    assert first is second
    return {"status": "ok"}


def _real_frame(path: Path) -> dict[str, object]:
    from core import vs_engine
    from core.vs_frame import request_bgr_frame, to_display_rgb_clip

    vs = vs_engine.load_vapoursynth()
    clip = to_display_rgb_clip(vs_engine.source_clip(str(path)), vs)
    frame = request_bgr_frame(clip, 10)
    assert frame is not None
    assert frame.shape == (360, 240, 3)
    return {"shape": list(frame.shape), "mean": int(frame.mean())}


def _main() -> dict[str, object]:
    if len(sys.argv) < 2:
        raise ValueError("missing probe case")
    case = sys.argv[1]
    if case == "frame_contract":
        return _frame_contract()
    if case == "source_cache":
        return _source_cache(Path(sys.argv[2]).resolve())
    if case == "real_frame":
        return _real_frame(Path(sys.argv[2]).resolve())
    raise ValueError(f"unknown probe case: {case}")


if __name__ == "__main__":
    try:
        result = _main()
    except BaseException as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}))
        raise
    print(json.dumps(result))
