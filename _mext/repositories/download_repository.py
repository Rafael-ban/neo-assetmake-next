"""Download API repository."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from _mext.services.api_client import ApiClient


@dataclass(frozen=True)
class DownloadTicket:
    url: str
    expires_at: str = ""


@dataclass(frozen=True)
class VerifiedDownload:
    presigned_url: str
    file_hash: str = ""
    file_size: int = 0


class DownloadRepository:
    """Centralize download ticket and verification endpoints."""

    def __init__(self, api_client: ApiClient) -> None:
        self._api = api_client

    def generate_url(self, material_id: str) -> DownloadTicket:
        data = self._api.post(
            "downloads/generate-url",
            json={"material_id": material_id},
        )
        return DownloadTicket(
            url=str(data.get("url", "")),
            expires_at=str(data.get("expires_at", "")),
        )

    def verify_url(self, url: str) -> VerifiedDownload:
        data = self._api.get(url)
        file_hash = data.get("file_hash") or ""
        file_size = data.get("file_size") or 0
        return VerifiedDownload(
            presigned_url=str(data.get("presigned_url", "")),
            file_hash=str(file_hash),
            file_size=int(file_size),
        )

    def resolve_download(self, material_id: str) -> VerifiedDownload:
        ticket = self.generate_url(material_id)
        if not ticket.url:
            raise ValueError("Server returned empty download URL")
        verified = self.verify_url(self._absolute_verify_url(ticket.url))
        if not verified.presigned_url:
            raise ValueError("Server returned empty presigned URL")
        return verified

    def _absolute_verify_url(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            return url
        if url.startswith("/"):
            return f"{self._api._config.api_base_url.rstrip('/')}{url}"
        return url
