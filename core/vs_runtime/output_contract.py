"""便携输出契约的宿主模型适配入口。"""

from __future__ import annotations

from typing import Any

from core.vs_runtime.job import RenderJob
from core.vs_runtime.script_header import ScriptHeader
from resources.vapoursynth.python.assetmaker_vs.contract import (
    MATRIX_CODES,
    PRIMARIES_CODES,
    TRANSFER_CODES,
    OutputContractError,
    RequirementError,
    ValidatedOutputs,
    X264Vui,
    decode_output_contract_error,
    guard_output0,
    validate_outputs as _validate_outputs,
    verify_required_callables,
)


def _job_payload(job: RenderJob | dict[str, Any]) -> dict[str, Any]:
    return job.to_dict() if isinstance(job, RenderJob) else job


def _header_payload(
    header: ScriptHeader | dict[str, Any],
) -> dict[str, Any]:
    if isinstance(header, ScriptHeader):
        return {
            "api_version": header.api_version,
            "mode": header.mode,
            "capabilities": list(header.capabilities),
            "requires": list(header.requires),
            "editor_output": header.editor_output,
        }
    return header


def validate_outputs(
    vs: Any,
    job: RenderJob | dict[str, Any],
    header: ScriptHeader | dict[str, Any],
) -> ValidatedOutputs:
    """仅适配 frozen 应用模型；全部契约规则仍由 helper 实现。"""
    return _validate_outputs(vs, _job_payload(job), _header_payload(header))


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
