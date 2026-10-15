"""Authentication API repository."""

from __future__ import annotations

import urllib.parse
from typing import Any

from _mext.services.api_client import ApiClient


class AuthRepository:
    """Centralize authentication endpoints used by the forum."""

    def __init__(self, api_client: ApiClient) -> None:
        self._api = api_client

    def initiate_drm_login(self, redirect_uri: str) -> dict[str, Any]:
        encoded = urllib.parse.quote(redirect_uri)
        return self._api.post(f"auth/drm-login/initiate?redirect_uri={encoded}")

    def complete_drm_login(self, code: str, state: str) -> dict[str, Any]:
        return self._api.post(
            "auth/drm-login/callback",
            json={"code": code, "state": state},
        )

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        return self._api.post(
            "auth/refresh",
            json={"refresh_token": refresh_token},
        )

    def logout(self, refresh_token: str | None = None) -> None:
        self._api.post("auth/logout", json={"refresh_token": refresh_token})

    def list_fido2_credentials(self) -> list[dict[str, Any]]:
        data = self._api.get("auth/fido2/credentials")
        return data.get("credentials", [])

    def begin_fido2_register(self) -> dict[str, Any]:
        return self._api.post("auth/fido2/register/begin")

    def complete_fido2_register(
        self,
        *,
        credential_name: str,
        attestation: dict[str, Any],
        state: str,
    ) -> dict[str, Any]:
        return self._api.post(
            "auth/fido2/register/complete",
            json={
                "credential_name": credential_name,
                "attestation": attestation,
                "state": state,
            },
        )

    def delete_fido2_credential(self, credential_id: str) -> None:
        self._api.delete(f"auth/fido2/credentials/{credential_id}")
