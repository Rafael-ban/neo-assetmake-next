"""VapourSynth worker 会话的不可变宿主模型。"""

from __future__ import annotations

import hashlib
import re
import secrets
import shutil
import stat
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

from core.vs_runtime.protocol import ProtocolError
from core.vs_runtime.script_header import ScriptHeader


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GENERATION_STAGING_ROOT_ENV = "ASSETMAKER_VS_STAGING_ROOT"
GENERATION_STAGING_TOKEN_ENV = "ASSETMAKER_VS_STAGING_TOKEN"
_GENERATION_STAGING_PREFIX = "assetmaker-vs-generation-"
_GENERATION_STAGING_MARKER = ".assetmaker-vs-owner"
_NODE_FIELDS = {
    "width",
    "height",
    "num_frames",
    "fps_num",
    "fps_den",
    "pixel_format",
    "matrix",
    "transfer",
    "primaries",
    "range",
}


def _wire_error(message: str, code: str) -> ProtocolError:
    return ProtocolError(message, code=code)


def _strict_object(value: Any, fields: set[str], location: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise _wire_error(
            f"{location} 字段不完整或包含未知字段",
            "protocol.invalid_metadata",
        )
    return value


def _positive_int(value: Any, location: str) -> int:
    if type(value) is not int or value <= 0:
        raise _wire_error(
            f"{location} 必须是严格正整数",
            "protocol.invalid_metadata",
        )
    return value


def _nonempty_string(value: Any, location: str) -> str:
    if type(value) is not str or not value:
        raise _wire_error(
            f"{location} 必须是非空字符串",
            "protocol.invalid_metadata",
        )
    return value


def _sha256(value: Any, field: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{field} 必须是小写 SHA-256")
    return value


def _absolute_path(value: Any, field: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{field} 必须是非空绝对路径")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{field} 必须是绝对路径")
    return str(path.resolve())


def compute_job_sha256(job_path: str | Path) -> str:
    """返回一个 RenderJob 文件当前字节内容的 SHA-256。

    路径不是 job 的身份：同一路径可以在预检与 VSPipe 启动之间被替换。
    因而所有跨进程 RenderSession 都携带这份内容摘要，并在每个消费边界
    复核它。
    """
    return hashlib.sha256(Path(job_path).read_bytes()).hexdigest()


@dataclass(frozen=True)
class NodeMetadata:
    width: int
    height: int
    num_frames: int
    fps_num: int
    fps_den: int
    pixel_format: str
    matrix: str | None
    transfer: str | None
    primaries: str | None
    range: Literal["limited", "full"] | None

    @classmethod
    def from_wire(
        cls, value: Any, *, require_colour: bool
    ) -> "NodeMetadata":
        data = _strict_object(value, _NODE_FIELDS, "node metadata")
        values = {
            field: _positive_int(data[field], f"node.{field}")
            for field in ("width", "height", "num_frames", "fps_num", "fps_den")
        }
        pixel_format = _nonempty_string(data["pixel_format"], "node.pixel_format")
        colour: dict[str, str | None] = {}
        for field in ("matrix", "transfer", "primaries"):
            item = data[field]
            if item is None and not require_colour:
                colour[field] = None
            else:
                colour[field] = _nonempty_string(item, f"node.{field}")
        range_ = data["range"]
        if range_ is None and not require_colour:
            pass
        elif range_ not in ("limited", "full"):
            raise _wire_error(
                "node.range 必须是 limited/full 或允许位置的 null",
                "protocol.invalid_metadata",
            )
        return cls(
            **values,
            pixel_format=pixel_format,
            matrix=colour["matrix"],
            transfer=colour["transfer"],
            primaries=colour["primaries"],
            range=range_,
        )

    def to_wire(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "num_frames": self.num_frames,
            "fps_num": self.fps_num,
            "fps_den": self.fps_den,
            "pixel_format": self.pixel_format,
            "matrix": self.matrix,
            "transfer": self.transfer,
            "primaries": self.primaries,
            "range": self.range,
        }


@dataclass(frozen=True)
class SessionMetadata:
    epoch: int
    mode: Literal["compatible", "raw"]
    capabilities: frozenset[str]
    output0: NodeMetadata
    editor: NodeMetadata | None

    @classmethod
    def from_wire(cls, value: Any) -> "SessionMetadata":
        data = _strict_object(
            value,
            {"epoch", "mode", "capabilities", "output0", "editor"},
            "session metadata",
        )
        epoch = _positive_int(data["epoch"], "metadata.epoch")
        mode = data["mode"]
        if mode not in ("compatible", "raw"):
            raise _wire_error(
                "metadata.mode 必须是 compatible/raw",
                "protocol.invalid_metadata",
            )
        capabilities = data["capabilities"]
        if (
            not isinstance(capabilities, list)
            or any(type(item) is not str or not item for item in capabilities)
            or len(capabilities) != len(set(capabilities))
        ):
            raise _wire_error(
                "metadata.capabilities 必须是不重复的非空字符串数组",
                "protocol.invalid_metadata",
            )
        editor_wire = data["editor"]
        editor = (
            None
            if editor_wire is None
            else NodeMetadata.from_wire(editor_wire, require_colour=False)
        )
        return cls(
            epoch=epoch,
            mode=mode,
            capabilities=frozenset(capabilities),
            output0=NodeMetadata.from_wire(
                data["output0"], require_colour=True
            ),
            editor=editor,
        )

    def to_wire(self) -> dict[str, Any]:
        return {
            "epoch": self.epoch,
            "mode": self.mode,
            "capabilities": sorted(self.capabilities),
            "output0": self.output0.to_wire(),
            "editor": None if self.editor is None else self.editor.to_wire(),
        }


@dataclass(frozen=True)
class ScriptSelection:
    script_path: str
    mode: Literal["compatible", "raw"]
    bundle_hash: str
    api_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "script_path", _absolute_path(self.script_path, "script_path")
        )
        if self.mode not in ("compatible", "raw"):
            raise ValueError("mode 必须是 compatible/raw")
        if type(self.api_version) is not int or self.api_version != 1:
            raise ValueError("api_version 必须是严格整数 1")
        _sha256(self.bundle_hash, "bundle_hash")

    @classmethod
    def from_header(
        cls,
        script_path: str | Path,
        header: ScriptHeader,
        bundle_hash: str,
    ) -> "ScriptSelection":
        if not isinstance(header, ScriptHeader):
            raise TypeError("header 必须是 ScriptHeader")
        return cls(
            script_path=str(Path(script_path).resolve()),
            mode=header.mode,
            bundle_hash=bundle_hash,
            api_version=header.api_version,
        )


@dataclass(frozen=True)
class RenderSession:
    epoch: int
    track: Literal["loop", "intro"]
    selection: ScriptSelection
    job_path: str
    job_sha256: str
    runtime_fingerprint: str

    def __post_init__(self) -> None:
        if type(self.epoch) is not int or self.epoch <= 0:
            raise ValueError("epoch 必须是严格正整数")
        if self.track not in ("loop", "intro"):
            raise ValueError("track 必须是 loop/intro")
        if not isinstance(self.selection, ScriptSelection):
            raise TypeError("selection 必须是 ScriptSelection")
        object.__setattr__(
            self, "job_path", _absolute_path(self.job_path, "job_path")
        )
        _sha256(self.job_sha256, "job_sha256")
        _sha256(self.runtime_fingerprint, "runtime_fingerprint")

    def to_load_message(self, request_id: int) -> dict[str, Any]:
        if type(request_id) is not int or request_id <= 0:
            raise ValueError("request_id 必须是严格正整数")
        return {
            "type": "load",
            "request_id": request_id,
            "api_version": self.selection.api_version,
            "track": self.track,
            "epoch": self.epoch,
            "script_path": self.selection.script_path,
            "job_path": self.job_path,
            "job_sha256": self.job_sha256,
            "bundle_hash": self.selection.bundle_hash,
            "runtime_fingerprint": self.runtime_fingerprint,
            "mode": self.selection.mode,
        }


def resolve_worker_command(app_dir: str | Path) -> list[str]:
    """返回源码或冻结构建的绝对 worker 命令数组。"""
    root = Path(app_dir).resolve()
    if getattr(sys, "frozen", False):
        return [str((root / "vs_worker.exe").resolve())]
    return [
        str(Path(sys.executable).resolve()),
        "-B",
        str((root / "vs_worker.py").resolve()),
    ]


def _read_script_bundle(
    script_path: str | Path,
) -> tuple[Path, list[tuple[str, bytes]]]:
    """一次性读取脚本根内参与 bundle 身份的全部代码。"""
    script = Path(script_path).resolve(strict=True)
    root = script.parent
    files = [
        path.resolve(strict=True)
        for path in root.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".vpy", ".py"}
    ]
    relative_files: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for path in files:
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError(f"脚本 bundle 文件逃逸根目录: {path}") from exc
        collision_key = relative.casefold()
        if collision_key in seen:
            raise ValueError(f"脚本 bundle 存在大小写碰撞: {relative}")
        seen.add(collision_key)
        relative_files.append((relative, path))
    relative_files.sort(key=lambda item: item[0].encode("utf-8"))
    return script, [
        (relative, path.read_bytes()) for relative, path in relative_files
    ]


def _bundle_digest(relative_files: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for relative, data in relative_files:
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def compute_script_bundle_hash(script_path: str | Path) -> str:
    """计算脚本根内 `.vpy/.py` 的稳定代码 bundle SHA-256。"""
    _script, relative_files = _read_script_bundle(script_path)
    return _bundle_digest(relative_files)


@dataclass
class GenerationStagingRoot:
    """由 host 持有、跨越一个 worker generation 的 staging 根。"""

    root_path: Path
    owner_token: str = ""
    _closed: bool = False
    _marker_initialized: bool = field(
        default=False,
        init=False,
        repr=False,
        compare=False,
    )
    _owns_unmarked_root: bool = field(
        default=False,
        init=False,
        repr=False,
        compare=False,
    )
    _lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
        compare=False,
    )

    @classmethod
    def create(cls) -> "GenerationStagingRoot":
        """只分配目录并返回精确 owner；marker 由调用者随后初始化。"""
        staging = cls(
            root_path=Path(
                tempfile.mkdtemp(prefix=_GENERATION_STAGING_PREFIX)
            )
        )
        staging._owns_unmarked_root = True
        return staging

    def initialize_marker(self) -> None:
        """在 owner 已交给 host 后建立跨进程 ownership marker。"""
        with self._lock:
            if self._closed:
                raise RuntimeError("worker generation staging 已关闭")
            if self._marker_initialized:
                return
            root = self.root_path.resolve(strict=True)
            if not root.name.startswith(_GENERATION_STAGING_PREFIX):
                raise RuntimeError("worker generation staging 根名称非法")
            token = secrets.token_hex(32)
            (root / _GENERATION_STAGING_MARKER).write_text(
                token, encoding="ascii"
            )
            self.root_path = root
            self.owner_token = token
            self._marker_initialized = True

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str]
    ) -> "GenerationStagingRoot":
        root_value = environment.get(GENERATION_STAGING_ROOT_ENV)
        token = environment.get(GENERATION_STAGING_TOKEN_ENV)
        if not root_value or not token:
            raise RuntimeError("worker generation staging 身份缺失")
        staging = cls(
            root_path=Path(root_value).resolve(strict=True),
            owner_token=token,
        )
        staging._marker_initialized = True
        staging._verify_owner()
        return staging

    def to_environment(self) -> dict[str, str]:
        self._verify_owner()
        return {
            GENERATION_STAGING_ROOT_ENV: str(self.root_path),
            GENERATION_STAGING_TOKEN_ENV: self.owner_token,
        }

    def _verify_owner(self) -> None:
        if not self._marker_initialized:
            raise RuntimeError("worker generation staging ownership marker 未初始化")
        root = self.root_path.resolve(strict=True)
        if not root.name.startswith(_GENERATION_STAGING_PREFIX):
            raise RuntimeError("worker generation staging 根名称非法")
        marker = root / _GENERATION_STAGING_MARKER
        if (
            not marker.is_file()
            or marker.read_text(encoding="ascii") != self.owner_token
        ):
            raise RuntimeError("worker generation staging ownership marker 不匹配")

    def create_snapshot_root(self) -> Path:
        self._verify_owner()
        snapshot = Path(
            tempfile.mkdtemp(prefix="snapshot-", dir=self.root_path)
        ).resolve(strict=True)
        if snapshot.parent != self.root_path.resolve(strict=True):
            ScriptBundleSnapshot._remove_tree(snapshot)
            raise RuntimeError("snapshot 逃逸 worker generation staging 根")
        return snapshot

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if not self.root_path.exists():
                self._closed = True
                return
            if self._marker_initialized:
                self._verify_owner()
            else:
                if not self._owns_unmarked_root:
                    raise RuntimeError(
                        "worker generation staging 未初始化 owner 不可清理"
                    )
                root = self.root_path.resolve(strict=True)
                if not root.name.startswith(_GENERATION_STAGING_PREFIX):
                    raise RuntimeError("worker generation staging 根名称非法")
                self.root_path = root
            ScriptBundleSnapshot._remove_tree(self.root_path)
            self._closed = True

    def __enter__(self) -> "GenerationStagingRoot":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


@dataclass
class ScriptBundleSnapshot:
    """供一次 worker load 独占的 job 快照与规范脚本绑定。

    M5 不再把用户 ``.vpy`` 复制到 worker 私有目录。VSPipe 必须以同一个
    canonical script path 执行，才能保证 ``__file__``、相对资源与 preview
    完全一致。代码 bundle 通过 hash 在执行前后核验；本对象只冻结会变动的
    job payload，并负责删除自己的 staging 根。
    """

    root_path: Path
    script_path: Path
    job_path: Path
    job_sha256: str
    _closed: bool = False

    @classmethod
    def create(
        cls,
        script_path: str | Path,
        job_path: str | Path,
        generation_staging: GenerationStagingRoot,
    ) -> "ScriptBundleSnapshot":
        script = Path(script_path).resolve(strict=True)
        job = Path(job_path).resolve(strict=True)
        job_bytes = job.read_bytes()
        job_sha256 = hashlib.sha256(job_bytes).hexdigest()
        root = generation_staging.create_snapshot_root()
        try:
            snapshot_job = root / "job.json"
            snapshot_job.write_bytes(job_bytes)
            snapshot_job.chmod(stat.S_IREAD)
            return cls(
                root_path=root,
                script_path=script,
                job_path=snapshot_job,
                job_sha256=job_sha256,
            )
        except BaseException:
            cls._remove_tree(root)
            raise

    @staticmethod
    def _remove_tree(root: Path) -> None:
        def make_writable_and_retry(function: Any, path: str, _error: Any) -> None:
            Path(path).chmod(stat.S_IWRITE)
            function(path)

        shutil.rmtree(root, onerror=make_writable_and_retry)

    def close(self) -> None:
        if self._closed:
            return
        self._remove_tree(self.root_path)
        self._closed = True


__all__ = [
    "GENERATION_STAGING_ROOT_ENV",
    "GENERATION_STAGING_TOKEN_ENV",
    "GenerationStagingRoot",
    "NodeMetadata",
    "RenderSession",
    "ScriptSelection",
    "ScriptBundleSnapshot",
    "SessionMetadata",
    "compute_script_bundle_hash",
    "resolve_worker_command",
]
