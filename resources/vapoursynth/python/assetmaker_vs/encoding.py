"""导出边界：把脚本处理结果转换为设备编码帧。"""

from __future__ import annotations

from typing import Any


def prepare_encoding_clip(vs: Any, clip: Any, job: dict[str, Any]) -> Any:
    """保持时间轴，执行目标颜色/位深转换、设备补边及安装方向。"""
    output = job["output"]
    clip = vs.core.resize.Lanczos(
        clip,
        format=vs.YUV420P8,
        matrix_s=output["matrix"],
        transfer_s={"709": "709", "170m": "601"}[output["transfer"]],
        primaries_s=output["primaries"],
        range_s=output["range"],
        dither_type="error_diffusion",
    )
    right = output["coded_width"] - output["display_width"]
    bottom = output["coded_height"] - output["display_height"]
    if right or bottom:
        clip = vs.core.std.AddBorders(clip, right=right, bottom=bottom)
    if output["final_rotate_180"]:
        clip = vs.core.std.Turn180(clip)
    codes = {"709": 1, "170m": 6}
    return vs.core.std.SetFrameProps(
        clip,
        _Matrix=codes[output["matrix"]],
        _Transfer=codes[output["transfer"]],
        _Primaries=codes[output["primaries"]],
        _Range={"limited": 0, "full": 1}[output["range"]],
    )
