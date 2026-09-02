"""不可变渲染作业 ABI 及其 JSON 边界。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from config.constants import RESOLUTION_SPECS
from core.file_utils import atomic_write_json
from utils.file_utils import get_app_dir


class RenderJobError(ValueError):
    """渲染作业读取、结构或阶段语义错误。"""


def _object(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RenderJobError(f"{location} 必须是对象")
    return value


def _keys(data: dict[str, Any], required: set[str], location: str) -> None:
    unknown = sorted(set(data) - required)
    missing = sorted(required - set(data))
    if unknown:
        raise RenderJobError(
            f"{location} 包含未知字段: {', '.join(unknown)}"
        )
    if missing:
        raise RenderJobError(
            f"{location} 缺少必需字段: {', '.join(missing)}"
        )


def _integer(value: Any, location: str, *, minimum: int = 0) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
    ):
        raise RenderJobError(f"{location} 必须是 >= {minimum} 的整数")
    return value


def _normalized_absolute_path(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value:
        raise RenderJobError(f"{location} 必须是非空字符串路径")
    if not os.path.isabs(value) or os.path.normpath(value) != value:
        raise RenderJobError(f"{location} 必须是规范化绝对路径: {value!r}")
    return value


@dataclass(frozen=True)
class RationalFPS:
    numerator: int
    denominator: int

    def validate(self) -> None:
        _integer(self.numerator, "timeline.fps.numerator", minimum=1)
        _integer(self.denominator, "timeline.fps.denominator", minimum=1)

    def to_dict(self) -> dict[str, int]:
        return {
            "numerator": self.numerator,
            "denominator": self.denominator,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "RationalFPS":
        data = _object(value, "timeline.fps")
        _keys(data, {"numerator", "denominator"}, "timeline.fps")
        fps = cls(data["numerator"], data["denominator"])
        fps.validate()
        return fps


@dataclass(frozen=True)
class SourceSpec:
    path: str
    kind: Literal["video", "image"]
    virtual_frame_count: int | None

    def validate(self) -> None:
        _normalized_absolute_path(self.path, "source.path")
        if self.kind not in ("video", "image"):
            raise RenderJobError(f"未知 source.kind: {self.kind!r}")
        if self.kind == "video":
            if self.virtual_frame_count is not None:
                raise RenderJobError(
                    "video 的 source.virtual_frame_count 必须为 null"
                )
        elif self.virtual_frame_count is None:
            raise RenderJobError(
                "image 的 source.virtual_frame_count 必须为正整数"
            )
        else:
            _integer(
                self.virtual_frame_count,
                "source.virtual_frame_count",
                minimum=1,
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "virtual_frame_count": self.virtual_frame_count,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "SourceSpec":
        data = _object(value, "source")
        _keys(data, {"path", "kind", "virtual_frame_count"}, "source")
        source = cls(data["path"], data["kind"], data["virtual_frame_count"])
        source.validate()
        return source


@dataclass(frozen=True)
class TimelineSpec:
    start_frame: int
    end_frame: int | None
    fps: RationalFPS | None

    def validate(self) -> None:
        _integer(self.start_frame, "timeline.start_frame")
        if self.end_frame is not None:
            _integer(self.end_frame, "timeline.end_frame", minimum=1)
            if self.end_frame <= self.start_frame:
                raise RenderJobError(
                    "resolved timeline.end_frame 必须大于 start_frame"
                )
        if self.fps is not None:
            if not isinstance(self.fps, RationalFPS):
                raise RenderJobError("timeline.fps 类型无效")
            self.fps.validate()

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "fps": None if self.fps is None else self.fps.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "TimelineSpec":
        data = _object(value, "timeline")
        _keys(data, {"start_frame", "end_frame", "fps"}, "timeline")
        fps = None if data["fps"] is None else RationalFPS.from_dict(data["fps"])
        timeline = cls(data["start_frame"], data["end_frame"], fps)
        timeline.validate()
        return timeline


@dataclass(frozen=True)
class CropSpec:
    coordinate_space: Literal["post_rotation_source_pixels"]
    x: int
    y: int
    width: int
    height: int

    def validate(self) -> None:
        if self.coordinate_space != "post_rotation_source_pixels":
            raise RenderJobError(
                f"未知 crop.coordinate_space: {self.coordinate_space!r}"
            )
        _integer(self.x, "transform.crop.x")
        _integer(self.y, "transform.crop.y")
        _integer(self.width, "transform.crop.width")
        _integer(self.height, "transform.crop.height")
        full_frame = self.width == 0 and self.height == 0
        explicit_crop = self.width > 0 and self.height > 0
        if not (full_frame or explicit_crop):
            raise RenderJobError(
                "crop width/height 必须同时为 0，或同时为正整数"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "coordinate_space": self.coordinate_space,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "CropSpec":
        data = _object(value, "transform.crop")
        required = {"coordinate_space", "x", "y", "width", "height"}
        _keys(data, required, "transform.crop")
        crop = cls(
            data["coordinate_space"],
            data["x"],
            data["y"],
            data["width"],
            data["height"],
        )
        crop.validate()
        return crop


@dataclass(frozen=True)
class TransformSpec:
    rotation: Literal[0, 90, 180, 270]
    crop: CropSpec

    def validate(self) -> None:
        if isinstance(self.rotation, bool) or self.rotation not in (
            0,
            90,
            180,
            270,
        ):
            raise RenderJobError(f"未知 transform.rotation: {self.rotation!r}")
        if not isinstance(self.crop, CropSpec):
            raise RenderJobError("transform.crop 类型无效")
        self.crop.validate()

    def to_dict(self) -> dict[str, Any]:
        return {"rotation": self.rotation, "crop": self.crop.to_dict()}

    @classmethod
    def from_dict(cls, value: Any) -> "TransformSpec":
        data = _object(value, "transform")
        _keys(data, {"rotation", "crop"}, "transform")
        transform = cls(data["rotation"], CropSpec.from_dict(data["crop"]))
        transform.validate()
        return transform


@dataclass(frozen=True)
class PathSpec:
    cache_dir: str

    def validate(self) -> None:
        _normalized_absolute_path(self.cache_dir, "paths.cache_dir")

    def to_dict(self) -> dict[str, str]:
        return {"cache_dir": self.cache_dir}

    @classmethod
    def from_dict(cls, value: Any) -> "PathSpec":
        data = _object(value, "paths")
        _keys(data, {"cache_dir"}, "paths")
        paths = cls(data["cache_dir"])
        paths.validate()
        return paths


@dataclass(frozen=True)
class OutputSpec:
    profile: Literal["360x640", "720x1080"]
    display_width: int
    display_height: int
    coded_width: int
    coded_height: int
    pixel_format: Literal["YUV420P8"] = "YUV420P8"
    matrix: str = "170m"
    transfer: str = "170m"
    primaries: str = "170m"
    range: Literal["limited", "full"] = "limited"
    final_rotate_180: bool = False

    @classmethod
    def from_profile(cls, profile: str) -> "OutputSpec":
        if profile not in ("360x640", "720x1080"):
            raise RenderJobError(f"未知 output.profile: {profile!r}")
        spec = RESOLUTION_SPECS.get(profile)
        if spec is None:
            raise RenderJobError(f"RESOLUTION_SPECS 缺少 profile: {profile!r}")
        return cls(
            profile=profile,
            display_width=int(spec["width"]),
            display_height=int(spec["height"]),
            coded_width=int(spec["padded_width"]),
            coded_height=int(spec["padded_height"]),
            final_rotate_180=bool(spec["rotate_180"]),
        )

    def validate(self) -> None:
        expected = self.from_profile(self.profile)
        for field_name in (
            "display_width",
            "display_height",
            "coded_width",
            "coded_height",
        ):
            _integer(getattr(self, field_name), f"output.{field_name}", minimum=1)
        if not isinstance(self.final_rotate_180, bool):
            raise RenderJobError("output.final_rotate_180 必须是布尔值")
        geometry = (
            self.display_width,
            self.display_height,
            self.coded_width,
            self.coded_height,
            self.final_rotate_180,
        )
        expected_geometry = (
            expected.display_width,
            expected.display_height,
            expected.coded_width,
            expected.coded_height,
            expected.final_rotate_180,
        )
        if geometry != expected_geometry:
            raise RenderJobError(
                f"output 几何参数与 profile {self.profile!r} 不一致"
            )
        if self.pixel_format != "YUV420P8":
            raise RenderJobError(f"未知 output.pixel_format: {self.pixel_format!r}")
        for field_name in ("matrix", "transfer", "primaries"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise RenderJobError(f"output.{field_name} 必须是非空字符串")
        if self.range not in ("limited", "full"):
            raise RenderJobError(f"未知 output.range: {self.range!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "display_width": self.display_width,
            "display_height": self.display_height,
            "coded_width": self.coded_width,
            "coded_height": self.coded_height,
            "pixel_format": self.pixel_format,
            "matrix": self.matrix,
            "transfer": self.transfer,
            "primaries": self.primaries,
            "range": self.range,
            "final_rotate_180": self.final_rotate_180,
        }

    @classmethod
    def from_dict(cls, value: Any) -> "OutputSpec":
        data = _object(value, "output")
        required = {
            "profile",
            "display_width",
            "display_height",
            "coded_width",
            "coded_height",
            "pixel_format",
            "matrix",
            "transfer",
            "primaries",
            "range",
            "final_rotate_180",
        }
        _keys(data, required, "output")
        output = cls(
            profile=data["profile"],
            display_width=data["display_width"],
            display_height=data["display_height"],
            coded_width=data["coded_width"],
            coded_height=data["coded_height"],
            pixel_format=data["pixel_format"],
            matrix=data["matrix"],
            transfer=data["transfer"],
            primaries=data["primaries"],
            range=data["range"],
            final_rotate_180=data["final_rotate_180"],
        )
        output.validate()
        return output


@dataclass(frozen=True)
class RenderJob:
    api_version: Literal[1]
    epoch: int
    track: Literal["loop", "intro"]
    project_root: str
    source: SourceSpec
    timeline: TimelineSpec
    transform: TransformSpec
    output: OutputSpec
    paths: PathSpec

    def validate(self, *, for_export: bool = False) -> None:
        if isinstance(self.api_version, bool) or self.api_version != 1:
            raise RenderJobError(f"未知 api_version: {self.api_version!r}")
        _integer(self.epoch, "epoch")
        if self.track not in ("loop", "intro"):
            raise RenderJobError(f"未知 track: {self.track!r}")
        _normalized_absolute_path(self.project_root, "project_root")
        for value, expected_type, location in (
            (self.source, SourceSpec, "source"),
            (self.timeline, TimelineSpec, "timeline"),
            (self.transform, TransformSpec, "transform"),
            (self.output, OutputSpec, "output"),
            (self.paths, PathSpec, "paths"),
        ):
            if not isinstance(value, expected_type):
                raise RenderJobError(f"{location} 类型无效")
            value.validate()
        if for_export and (
            self.timeline.end_frame is None or self.timeline.fps is None
        ):
            raise RenderJobError("导出作业必须解析 timeline.end_frame 和 fps")

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": self.api_version,
            "epoch": self.epoch,
            "track": self.track,
            "project_root": self.project_root,
            "source": self.source.to_dict(),
            "timeline": self.timeline.to_dict(),
            "transform": self.transform.to_dict(),
            "output": self.output.to_dict(),
            "paths": self.paths.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Any) -> "RenderJob":
        data = _object(value, "job")
        required = {
            "api_version",
            "epoch",
            "track",
            "project_root",
            "source",
            "timeline",
            "transform",
            "output",
            "paths",
        }
        _keys(data, required, "job")
        job = cls(
            api_version=data["api_version"],
            epoch=data["epoch"],
            track=data["track"],
            project_root=data["project_root"],
            source=SourceSpec.from_dict(data["source"]),
            timeline=TimelineSpec.from_dict(data["timeline"]),
            transform=TransformSpec.from_dict(data["transform"]),
            output=OutputSpec.from_dict(data["output"]),
            paths=PathSpec.from_dict(data["paths"]),
        )
        job.validate()
        return job


def _schema_path() -> Path:
    return Path(get_app_dir()) / "schemas" / "vs_job.schema.json"


@lru_cache(maxsize=1)
def _job_validator() -> Draft202012Validator:
    path = _schema_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, SchemaError) as exc:
        raise RenderJobError(f"{path.resolve()}: 无法加载 job schema: {exc}") from exc
    return Draft202012Validator(payload)


def load_render_job(
    path: str | os.PathLike[str], *, for_export: bool = False
) -> RenderJob:
    """从唯一 JSON 边界加载并完整验证 RenderJob。"""
    source_path = Path(path)
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RenderJobError(f"{source_path.resolve()}: {exc}") from exc
    try:
        _job_validator().validate(payload)
        job = RenderJob.from_dict(payload)
        job.validate(for_export=for_export)
        return job
    except (ValidationError, RenderJobError) as exc:
        raise RenderJobError(f"{source_path.resolve()}: {exc}") from exc


def write_render_job(job: RenderJob) -> Path:
    """原子新建 ``job-<epoch>.json``，拒绝复用旧 epoch。"""
    if not isinstance(job, RenderJob):
        raise RenderJobError("job 类型无效")
    job.validate()
    target = Path(job.paths.cache_dir) / f"job-{job.epoch}.json"
    if target.exists():
        raise RenderJobError(f"拒绝覆盖既有 RenderJob: {target.resolve()}")
    try:
        atomic_write_json(target, job.to_dict(), indent=2)
    except OSError as exc:
        raise RenderJobError(f"{target.resolve()}: {exc}") from exc
    return target
