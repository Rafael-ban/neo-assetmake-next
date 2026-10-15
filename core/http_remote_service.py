"""HTTP API client for EPass RNDIS remote asset management."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from pathlib import Path
import re
import shutil
import tempfile
import threading
from typing import Any, Callable
from urllib.parse import quote
import zipfile

from core.rndis_device_service import DEFAULT_BASE_URL

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, str], None]


class HttpRemoteError(RuntimeError):
    """Raised when the EPass HTTP API returns an error."""


ASSET_UUID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
MAX_ARCHIVE_FILES = 10000
MAX_ARCHIVE_FILE_SIZE = 512 * 1024 * 1024
MAX_ARCHIVE_TOTAL_SIZE = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_DEPTH = 32


@dataclass(frozen=True)
class RemoteAsset:
    """Remote asset metadata returned by the HTTP API."""

    uuid: str
    name: str
    path: str
    size: int = 0
    updated_at: str = ""
    icon_url: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RemoteAsset":
        try:
            size = int(data.get("size") or 0)
        except (TypeError, ValueError) as exc:
            raise HttpRemoteError("Invalid asset size in HTTP API response") from exc

        return cls(
            uuid=str(data.get("uuid") or ""),
            name=str(data.get("name") or data.get("uuid") or "Unnamed asset"),
            path=str(data.get("path") or ""),
            size=size,
            updated_at=str(data.get("updated_at") or data.get("date") or ""),
            icon_url=str(data.get("icon_url") or ""),
        )

    def to_ui_dict(self) -> dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "path": self.path,
            "size": self.size,
            "date": self.updated_at,
            "icon_url": self.icon_url,
        }


class HttpRemoteService:
    """Client for the device-side RNDIS HTTP API."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 20.0,
        session: Any | None = None,
        device_token: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or _requests().Session()
        self.device_token = device_token

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _request(self, method: str, path: str, **kwargs) -> Any:
        kwargs.setdefault("timeout", self.timeout)
        if self.device_token:
            headers = dict(kwargs.pop("headers", {}) or {})
            headers.setdefault("X-EPass-Token", self.device_token)
            kwargs["headers"] = headers
        try:
            response = self.session.request(method, self._url(path), **kwargs)
            response.raise_for_status()
            return response
        except Exception as exc:
            raise HttpRemoteError(str(exc)) from exc

    def health(self) -> dict[str, Any]:
        response = self._request("GET", "/api/v1/health", timeout=3)
        return _json_object(response)

    def sync_time(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc).astimezone()
        self._request(
            "POST",
            "/api/v1/system/time",
            json={"iso": now.isoformat()},
            timeout=5,
        )

    def probe_stream(self) -> None:
        response = self._request(
            "GET",
            "/api/v1/stream.mjpg",
            stream=True,
            timeout=5,
        )
        try:
            content_type = str(response.headers.get("content-type") or "").lower()
            if "multipart" not in content_type and "jpeg" not in content_type:
                raise HttpRemoteError("Device stream endpoint is not an MJPEG stream")
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    def list_assets(self) -> list[RemoteAsset]:
        response = self._request("GET", "/api/v1/assets")
        payload = _json_object(response)
        items = payload.get("items", [])
        if not isinstance(items, list):
            raise HttpRemoteError("Asset list response must contain an items array")
        return [RemoteAsset.from_dict(item) for item in items if isinstance(item, dict)]

    def get_icon(self, uuid: str) -> bytes | None:
        uuid = validate_asset_uuid(uuid)
        try:
            response = self._request("GET", _asset_api_path(uuid, "icon"))
        except HttpRemoteError:
            return None
        return response.content

    def upload_asset(
        self,
        local_dir: str | Path,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        local_path = Path(local_dir)
        if not local_path.is_dir():
            raise HttpRemoteError(f"素材目录不存在：{local_path}")

        _emit(progress, 5, "正在准备素材压缩包...")
        archive_path = _make_asset_archive(local_path)
        try:
            _raise_if_cancelled(cancel_event)
            _emit(progress, 35, "正在上传到 EPass 设备...")
            with archive_path.open("rb") as file_obj:
                upload_file = _CancellableFile(file_obj, cancel_event)
                files = {"package": (archive_path.name, upload_file, "application/zip")}
                response = self._request(
                    "POST",
                    "/api/v1/assets",
                    files=files,
                    timeout=120,
                )
            _raise_if_cancelled(cancel_event)
            _emit(progress, 90, "正在完成上传...")
            payload = _json_object(response)
            _emit(progress, 100, "上传完成")
            return payload
        finally:
            try:
                archive_path.unlink(missing_ok=True)
            except OSError:
                logger.debug("Failed to remove temporary archive: %s", archive_path)

    def download_asset(
        self,
        uuid: str,
        save_dir: str | Path,
        progress: ProgressCallback | None = None,
    ) -> Path:
        uuid = validate_asset_uuid(uuid)

        output_root = Path(save_dir)
        output_root.mkdir(parents=True, exist_ok=True)
        archive_path = output_root / f"{uuid}.zip"
        extract_dir = output_root / uuid
        temp_extract_dir = output_root / f".{uuid}.extracting"

        _emit(progress, 10, "正在下载素材压缩包...")
        response = self._request(
            "GET",
            _asset_api_path(uuid, "archive"),
            stream=True,
            timeout=120,
        )
        total = int(response.headers.get("content-length") or 0)
        written = 0
        with archive_path.open("wb") as file_obj:
            for chunk in response.iter_content(chunk_size=1024 * 128):
                if not chunk:
                    continue
                file_obj.write(chunk)
                written += len(chunk)
                if total:
                    percent = 10 + int((written / total) * 70)
                    _emit(progress, min(percent, 80), "正在下载素材压缩包...")

        _emit(progress, 85, "正在解压素材压缩包...")
        if temp_extract_dir.exists():
            shutil.rmtree(temp_extract_dir)
        temp_extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                _safe_extract_archive(archive, temp_extract_dir)
            if extract_dir.exists():
                shutil.rmtree(extract_dir)
            temp_extract_dir.replace(extract_dir)
        except Exception:
            shutil.rmtree(temp_extract_dir, ignore_errors=True)
            raise
        finally:
            archive_path.unlink(missing_ok=True)
        _emit(progress, 100, "下载完成")
        return extract_dir

    def delete_asset(self, uuid: str) -> None:
        uuid = validate_asset_uuid(uuid)
        self._request("DELETE", _asset_api_path(uuid), timeout=20)

    def restart_drm(self) -> None:
        self._request("POST", "/api/v1/drm/restart", timeout=20)


def _requests():
    import requests

    return requests


def _json_object(response: Any) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise HttpRemoteError("HTTP API returned non-JSON data") from exc
    if not isinstance(payload, dict):
        raise HttpRemoteError("HTTP API response must be a JSON object")
    return payload


def _emit(progress: ProgressCallback | None, percent: int, message: str) -> None:
    if progress:
        progress(percent, message)


def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise HttpRemoteError("Upload cancelled")


class _CancellableFile:
    def __init__(self, file_obj, cancel_event: threading.Event | None):
        self._file_obj = file_obj
        self._cancel_event = cancel_event

    def read(self, size: int = -1) -> bytes:
        _raise_if_cancelled(self._cancel_event)
        chunk = self._file_obj.read(size)
        _raise_if_cancelled(self._cancel_event)
        return chunk

    def __getattr__(self, name: str):
        return getattr(self._file_obj, name)


def validate_asset_uuid(uuid: str) -> str:
    value = str(uuid or "").strip()
    if not ASSET_UUID_RE.fullmatch(value):
        raise HttpRemoteError("Invalid asset UUID")
    return value


def _asset_api_path(uuid: str, suffix: str = "") -> str:
    safe_uuid = quote(validate_asset_uuid(uuid), safe="")
    path = f"/api/v1/assets/{safe_uuid}"
    if suffix:
        path = f"{path}/{suffix}"
    return path


def _safe_extract_archive(archive: zipfile.ZipFile, extract_dir: Path) -> None:
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_FILES:
        raise HttpRemoteError("Asset archive contains too many files")

    total_size = 0
    extract_root = extract_dir.resolve()
    for info in infos:
        target = _safe_archive_target(info, extract_root)
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue

        if info.file_size > MAX_ARCHIVE_FILE_SIZE:
            raise HttpRemoteError("Asset archive contains an oversized file")
        total_size += info.file_size
        if total_size > MAX_ARCHIVE_TOTAL_SIZE:
            raise HttpRemoteError("Asset archive is too large")

        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info, "r") as source, target.open("wb") as destination:
            shutil.copyfileobj(source, destination)


def _safe_archive_target(info: zipfile.ZipInfo, extract_root: Path) -> Path:
    name = info.filename.replace("\\", "/")
    parts = [part for part in name.split("/") if part]
    if (
        not parts
        or name.startswith("/")
        or ".." in parts
        or ":" in parts[0]
        or len(parts) > MAX_ARCHIVE_DEPTH
    ):
        raise HttpRemoteError(f"Unsafe archive entry: {info.filename}")

    mode = (info.external_attr >> 16) & 0o170000
    if mode == 0o120000:
        raise HttpRemoteError(f"Unsafe archive entry: {info.filename}")

    target = (extract_root / Path(*parts)).resolve()
    if target != extract_root and extract_root not in target.parents:
        raise HttpRemoteError(f"Unsafe archive entry: {info.filename}")
    return target


def _make_asset_archive(local_path: Path) -> Path:
    fd, temp_name = tempfile.mkstemp(prefix="epass_asset_", suffix=".zip")
    # Close the descriptor created by mkstemp; ZipFile will reopen by path.
    import os

    os.close(fd)
    archive_path = Path(temp_name)
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in local_path.rglob("*"):
            if item.is_file():
                archive.write(item, item.relative_to(local_path).as_posix())
    return archive_path
