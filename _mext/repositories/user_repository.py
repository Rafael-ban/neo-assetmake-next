"""User and library API repository."""

from __future__ import annotations

from _mext.models.material import Material
from _mext.models.user import User
from _mext.services.api_client import ApiClient, ApiError


class UserRepository:
    """Centralize current-user, creator, and library endpoints."""

    def __init__(self, api_client: ApiClient) -> None:
        self._api = api_client

    def current_user(self) -> User:
        return User.from_dict(self._api.get("users/me"))

    def list_downloaded_materials_raw(self) -> list[dict]:
        download_records = self._api.get("users/me/downloads")
        materials: list[dict] = []
        seen_ids: set[str] = set()
        for record in download_records:
            material_id = str(record.get("material_id", ""))
            if not material_id or material_id in seen_ids:
                continue
            seen_ids.add(material_id)
            try:
                materials.append(self._api.get(f"materials/{material_id}"))
            except ApiError:
                continue
        return materials

    def list_downloaded_materials(self) -> list[Material]:
        return [Material.from_dict(item) for item in self.list_downloaded_materials_raw()]

    def list_favorites_raw(self) -> list[dict]:
        return list(self._api.get("users/me/favorites"))

    def list_favorites(self) -> list[Material]:
        return [Material.from_dict(item) for item in self.list_favorites_raw()]

    def creator_profile(self, creator_id: str) -> dict:
        return self._api.get(f"users/{creator_id}/profile")

    def creator_materials_raw(
        self, creator_id: str, *, page: int = 1, per_page: int = 20
    ) -> tuple[list[dict], int]:
        data = self._api.get(
            f"users/{creator_id}/materials",
            params={"page": page, "per_page": per_page},
        )
        return list(data.get("items", [])), int(data.get("total", 0))

    def creator_materials(self, creator_id: str, *, page: int = 1, per_page: int = 20):
        items, total = self.creator_materials_raw(creator_id, page=page, per_page=per_page)
        return [Material.from_dict(item) for item in items], total
