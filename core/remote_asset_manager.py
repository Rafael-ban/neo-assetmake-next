"""Use-case layer for EPass RNDIS remote asset management."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
from pathlib import Path
from typing import Any, Callable

from core.http_remote_service import HttpRemoteService, RemoteAsset
from core.rndis_device_service import (
    DEFAULT_BASE_URL,
    DeviceProbeResult,
    RndisAdapter,
    detect_and_probe_device,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, str], None]
DetectFunc = Callable[[], DeviceProbeResult]
ServiceFactory = Callable[[str, str], HttpRemoteService]


class RemoteAssetManagerError(RuntimeError):
    """Raised when a remote asset use case fails."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class RemoteSession:
    """Cached remote device session metadata."""

    base_url: str
    device_id: str
    protocol_version: str
    capabilities: frozenset[str]
    health: dict[str, Any]
    adapter: RndisAdapter | None = None
    auth_required: bool = False
    authenticated: bool = True

    @property
    def adapter_name(self) -> str:
        return self.adapter.name if self.adapter else "RNDIS"

    def to_ui_dict(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "device_id": self.device_id,
            "protocol_version": self.protocol_version,
            "capabilities": sorted(self.capabilities),
            "health": self.health,
            "adapter": self.adapter_name,
            "auth_required": self.auth_required,
            "authenticated": self.authenticated,
        }


class RemoteAssetManager:
    """Coordinates RNDIS detection, HTTP service creation, and asset actions."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        device_token: str = "",
        detect_func: DetectFunc = detect_and_probe_device,
        service_factory: ServiceFactory | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._device_token = device_token
        self._detect_func = detect_func
        self._service_factory = service_factory or self._default_service_factory
        self._session: RemoteSession | None = None
        self._service: HttpRemoteService | None = None

    @property
    def session(self) -> RemoteSession | None:
        return self._session

    @property
    def base_url(self) -> str:
        return self._session.base_url if self._session else self._base_url

    def set_device_token(self, token: str) -> None:
        self._device_token = token
        self._service = None
        if self._session:
            self._session = _session_from_probe(
                DeviceProbeResult(
                    base_url=self._session.base_url,
                    health=self._session.health,
                    adapter=self._session.adapter,
                ),
                token,
            )

    def disconnect(self) -> None:
        self._session = None
        self._service = None

    def connect(self, *, force: bool = False, sync_time: bool = True) -> RemoteSession:
        if self._session is not None and not force:
            return self._session

        try:
            probe = self._detect_func()
        except Exception as exc:
            raise RemoteAssetManagerError("connect_failed", str(exc)) from exc

        session = _session_from_probe(probe, self._device_token)
        self._session = session
        self._service = self._service_factory(session.base_url, self._device_token)
        self._base_url = session.base_url

        if sync_time and self._service and session.authenticated:
            try:
                self._service.sync_time()
            except Exception:
                logger.warning("Failed to sync remote device time", exc_info=True)

        return session

    def list_assets(self) -> list[RemoteAsset]:
        self._require_authenticated()
        return self._service_or_connect().list_assets()

    def upload_asset(
        self,
        local_path: str | Path,
        *,
        restart: bool = False,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        self._require_authenticated()
        service = self._service_or_connect()
        payload = service.upload_asset(local_path, progress=progress, cancel_event=cancel_event)
        if restart:
            if progress:
                progress(95, "正在重启 DrmApp...")
            service.restart_drm()
        return payload

    def download_asset(
        self,
        uuid: str,
        save_dir: str | Path,
        *,
        progress: ProgressCallback | None = None,
    ) -> Path:
        self._require_authenticated()
        return self._service_or_connect().download_asset(uuid, save_dir, progress=progress)

    def delete_asset(self, uuid: str) -> None:
        self._require_authenticated()
        self._service_or_connect().delete_asset(uuid)

    def restart_drm(self) -> None:
        self._require_authenticated()
        self._service_or_connect().restart_drm()

    def probe_stream(self) -> None:
        self._require_authenticated()
        self._service_or_connect().probe_stream()

    def _service_or_connect(self) -> HttpRemoteService:
        if self._service is None:
            self.connect()
        if self._service is None:
            raise RemoteAssetManagerError("connect_failed", "Remote service is unavailable")
        return self._service

    def _require_authenticated(self) -> None:
        session = self.connect()
        if session.auth_required and not session.authenticated:
            raise RemoteAssetManagerError(
                "auth_required",
                "Device requires pairing/authentication before remote management",
            )

    def _default_service_factory(self, base_url: str, token: str) -> HttpRemoteService:
        return HttpRemoteService(base_url, device_token=token)


def _session_from_probe(probe: DeviceProbeResult, token: str) -> RemoteSession:
    health = probe.health
    capabilities = frozenset(str(item) for item in health.get("capabilities", []))
    auth_required = bool(health.get("auth_required", False))
    authenticated = bool(token) if auth_required else True
    return RemoteSession(
        base_url=probe.base_url,
        device_id=str(health.get("device_id") or ""),
        protocol_version=str(health.get("protocol_version") or ""),
        capabilities=capabilities,
        health=health,
        adapter=probe.adapter,
        auth_required=auth_required,
        authenticated=authenticated,
    )
