"""VapourSynth output 0/1 的严格、只读输出契约。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from . import AssetmakerVSError


MATRIX_CODES = {"709": 1, "170m": 6}
TRANSFER_CODES = {"709": 1, "170m": 6}
PRIMARIES_CODES = {"709": 1, "170m": 6}
X264_MATRIX = {1: "bt709", 6: "smpte170m"}
X264_TRANSFER = {1: "bt709", 6: "smpte170m"}
X264_PRIMARIES = {1: "bt709", 6: "smpte170m"}
_CODE_LABELS = {1: "709", 6: "170m"}


class OutputContractError(AssetmakerVSError):
    """脚本注册的输出不满足编码或编辑画布契约。"""

    MARKER = "ASSETMAKER_VS_ERROR:"

    def _format_message(self) -> str:
        payload = self.to_dict()
        return self.MARKER + json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


class RequirementError(AssetmakerVSError):
    """脚本声明的 VapourSynth 函数不可调用。"""


def decode_output_contract_error(value: BaseException | str) -> OutputContractError | None:
    """从 VapourSynth 包装后的 callback traceback 中恢复结构化契约错误。"""
    text = str(value)
    marker_at = text.find(OutputContractError.MARKER)
    if marker_at < 0:
        return None
    start = marker_at + len(OutputContractError.MARKER)
    try:
        payload, _ = json.JSONDecoder().raw_decode(text[start:])
        return OutputContractError(
            payload["message"],
            code=payload["code"],
            field=payload.get("field"),
            path=payload.get("path"),
            expected=payload.get("expected"),
            actual=payload.get("actual"),
            hint=payload.get("hint"),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


@dataclass(frozen=True)
class X264Vui:
    colormatrix: str
    colorprim: str
    transfer: str
    range_: str

    def to_dict(self) -> dict[str, str]:
        return {
            "colormatrix": self.colormatrix,
            "colorprim": self.colorprim,
            "transfer": self.transfer,
            "range": self.range_,
        }


@dataclass(frozen=True)
class ValidatedOutputs:
    clip: Any
    guarded_clip: Any
    editor_clip: Any | None
    vui: X264Vui


def _error(
    field: str,
    expected: Any,
    actual: Any,
    *,
    message: str | None = None,
    hint: str | None = None,
) -> OutputContractError:
    return OutputContractError(
        message or f"输出字段 {field} 不符合契约",
        code=f"contract.{field}",
        field=field,
        expected=expected,
        actual=actual,
        hint=hint or "请在 .vpy 中显式生成满足 job 的输出；宿主不会自动修正。",
    )


def verify_required_callables(core: Any, requirements: Iterable[str]) -> None:
    """在执行用户代码前确认每个 ``namespace.Function`` 都存在且可调用。"""
    for requirement in requirements:
        try:
            namespace_name, function_name = requirement.split(".", 1)
            namespace = getattr(core, namespace_name)
            function = getattr(namespace, function_name)
        except (AttributeError, ValueError):
            function = None
        if not callable(function):
            raise RequirementError(
                f"缺少 VapourSynth requirement: {requirement}",
                code="requirement.missing",
                field="requires",
                expected="callable namespace.Function",
                actual=requirement,
                hint="请安装声明的原生插件，或修正 assetmaker-requires。",
            )


def _get_output_tuple(vs: Any, index: int) -> Any:
    field = f"output.{index}"
    try:
        output = vs.get_output(index)
    except BaseException as exc:
        raise _error(
            field,
            "registered VideoOutputTuple",
            "missing",
            message=f"缺少 VapourSynth output {index}",
        ) from exc
    if not isinstance(output, vs.VideoOutputTuple):
        raise _error(f"{field}.type", "VideoOutputTuple", type(output).__name__)
    if not isinstance(output.clip, vs.VideoNode):
        raise _error(
            f"{field}.clip", "VideoNode", type(output.clip).__name__
        )
    if output.alpha is not None:
        raise _error(f"{field}.alpha", None, type(output.alpha).__name__)
    if output.alt_output != 0:
        raise _error(f"{field}.alt_output", 0, output.alt_output)
    return output


def _format_name(format_: Any) -> Any:
    return None if format_ is None else getattr(format_, "name", None)


def _check_node_static(vs: Any, clip: Any, job: dict[str, Any]) -> None:
    format_ = clip.format
    if format_ is None or getattr(format_, "id", None) != vs.YUV420P8:
        raise _error("pixel_format", "YUV420P8", _format_name(format_))
    subsampling_w = 1 << int(getattr(format_, "subsampling_w", 0))
    subsampling_h = 1 << int(getattr(format_, "subsampling_h", 0))
    if clip.width % subsampling_w or clip.height % subsampling_h:
        raise _error(
            "chroma_geometry",
            f"width % {subsampling_w} == 0 and height % {subsampling_h} == 0",
            [clip.width, clip.height],
        )
    output = job["output"]
    expected_size = [output["coded_width"], output["coded_height"]]
    actual_size = [clip.width, clip.height]
    if actual_size != expected_size:
        raise _error("coded_size", expected_size, actual_size)
    if type(clip.num_frames) is not int or clip.num_frames <= 0:
        raise _error("num_frames", "integer > 0", clip.num_frames)
    if clip.fps_num <= 0 or clip.fps_den <= 0:
        raise _error("fps", "positive rational", [clip.fps_num, clip.fps_den])
    timeline = job["timeline"]
    if timeline["end_frame"] is not None:
        expected_frames = timeline["end_frame"] - timeline["start_frame"]
        if clip.num_frames != expected_frames:
            raise _error("num_frames", expected_frames, clip.num_frames)
    if timeline["fps"] is not None:
        expected_fps = [
            timeline["fps"]["numerator"],
            timeline["fps"]["denominator"],
        ]
        actual_fps = [clip.fps_num, clip.fps_den]
        if actual_fps[0] * expected_fps[1] != expected_fps[0] * actual_fps[1]:
            raise _error("fps", expected_fps, actual_fps)


def _range_value(props: Any) -> str:
    values: list[str] = []
    if "_Range" in props:
        try:
            code = int(props["_Range"])
        except (TypeError, ValueError) as exc:
            raise _error(
                "range", ["limited", "full"], props["_Range"]
            ) from exc
        try:
            values.append({0: "limited", 1: "full"}[code])
        except KeyError as exc:
            raise _error("range", ["limited", "full"], code) from exc
    if "_ColorRange" in props:
        try:
            code = int(props["_ColorRange"])
        except (TypeError, ValueError) as exc:
            raise _error(
                "range", ["limited", "full"], props["_ColorRange"]
            ) from exc
        try:
            values.append({1: "limited", 0: "full"}[code])
        except KeyError as exc:
            raise _error("range", ["limited", "full"], code) from exc
    if not values:
        raise _error("range", "_Range or _ColorRange", None)
    if any(value != values[0] for value in values[1:]):
        raise _error("range", "consistent _Range/_ColorRange", values)
    return values[0]


def _code_value(
    props: Any,
    *,
    prop: str,
    field: str,
    expected_name: str,
    known: dict[str, int],
) -> int:
    if expected_name not in known:
        raise _error(field, sorted(known), expected_name)
    if prop not in props:
        raise _error(field, expected_name, None)
    try:
        code = int(props[prop])
    except (TypeError, ValueError) as exc:
        raise _error(field, expected_name, props[prop]) from exc
    actual_name = _CODE_LABELS.get(code, code)
    if code != known[expected_name]:
        raise _error(field, expected_name, actual_name)
    return code


def _check_frame(frame: Any, clip: Any, job: dict[str, Any]) -> tuple[int, int, int, str]:
    if frame.format is None or frame.format.id != clip.format.id:
        raise _error(
            "pixel_format", _format_name(clip.format), _format_name(frame.format)
        )
    if [frame.width, frame.height] != [clip.width, clip.height]:
        raise _error(
            "coded_size",
            [clip.width, clip.height],
            [frame.width, frame.height],
        )
    expected = job["output"]
    matrix = _code_value(
        frame.props,
        prop="_Matrix",
        field="matrix",
        expected_name=expected["matrix"],
        known=MATRIX_CODES,
    )
    transfer = _code_value(
        frame.props,
        prop="_Transfer",
        field="transfer",
        expected_name=expected["transfer"],
        known=TRANSFER_CODES,
    )
    primaries = _code_value(
        frame.props,
        prop="_Primaries",
        field="primaries",
        expected_name=expected["primaries"],
        known=PRIMARIES_CODES,
    )
    range_name = _range_value(frame.props)
    if range_name != expected["range"]:
        raise _error("range", expected["range"], range_name)
    return matrix, transfer, primaries, range_name


def _sentinel_signature(clip: Any, job: dict[str, Any]) -> tuple[int, int, int, str]:
    indices = sorted({0, clip.num_frames // 2, clip.num_frames - 1})
    baseline: tuple[int, int, int, str] | None = None
    for index in indices:
        frame = clip.get_frame(index)
        try:
            signature = _check_frame(frame, clip, job)
        finally:
            frame.close()
        if baseline is None:
            baseline = signature
        elif signature != baseline:
            raise _error("frame_props", baseline, signature)
    assert baseline is not None
    return baseline


def guard_output0(vs: Any, clip: Any, job: dict[str, Any]) -> Any:
    """为实际消费的每帧追加只读校验，合法时原帧透传。"""

    def selector(n: int, f: Any) -> Any:
        del n
        _check_frame(f, clip, job)
        return f

    return vs.core.std.ModifyFrame(clip=clip, clips=clip, selector=selector)


def _check_editor_clip(clip: Any, job: dict[str, Any]) -> None:
    if clip.width <= 0 or clip.height <= 0:
        raise _error("output.1.size", "positive dimensions", [clip.width, clip.height])
    if clip.num_frames <= 0:
        raise _error("output.1.num_frames", "integer > 0", clip.num_frames)
    if clip.format is None:
        raise _error("output.1.pixel_format", "constant format", None)
    if clip.fps_num <= 0 or clip.fps_den <= 0:
        raise _error(
            "output.1.fps", "positive rational", [clip.fps_num, clip.fps_den]
        )
    source = job["source"]
    timeline = job["timeline"]
    if (
        timeline["end_frame"] is not None
        and clip.num_frames < timeline["end_frame"]
    ):
        raise _error(
            "output.1.num_frames",
            f">= {timeline['end_frame']}",
            clip.num_frames,
        )
    fps = timeline["fps"]
    if fps is not None and (
        clip.fps_num * fps["denominator"]
        != fps["numerator"] * clip.fps_den
    ):
        raise _error(
            "output.1.fps",
            [fps["numerator"], fps["denominator"]],
            [clip.fps_num, clip.fps_den],
        )
    if source["kind"] == "image":
        if clip.num_frames != source["virtual_frame_count"]:
            raise _error(
                "output.1.num_frames",
                source["virtual_frame_count"],
                clip.num_frames,
            )


def validate_outputs(
    vs: Any, job: dict[str, Any], header: dict[str, Any]
) -> ValidatedOutputs:
    """按固定顺序验证注册输出，返回逐帧 guarded output 0。"""
    output0 = _get_output_tuple(vs, 0)
    clip = output0.clip
    _check_node_static(vs, clip, job)
    matrix, transfer, primaries, range_name = _sentinel_signature(clip, job)
    vui = X264Vui(
        colormatrix=X264_MATRIX[matrix],
        colorprim=X264_PRIMARIES[primaries],
        transfer=X264_TRANSFER[transfer],
        range_={"limited": "tv", "full": "pc"}[range_name],
    )
    editor_clip = None
    if header["editor_output"] == 1:
        output1 = _get_output_tuple(vs, 1)
        editor_clip = output1.clip
        _check_editor_clip(editor_clip, job)
    guarded = guard_output0(vs, clip, job)
    return ValidatedOutputs(
        clip=clip,
        guarded_clip=guarded,
        editor_clip=editor_clip,
        vui=vui,
    )


__all__ = [
    "MATRIX_CODES",
    "OutputContractError",
    "PRIMARIES_CODES",
    "RequirementError",
    "TRANSFER_CODES",
    "ValidatedOutputs",
    "X264Vui",
    "decode_output_contract_error",
    "guard_output0",
    "validate_outputs",
    "verify_required_callables",
]
