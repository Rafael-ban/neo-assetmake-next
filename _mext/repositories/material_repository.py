"""Material API repository."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from _mext.models.material import Material
from _mext.services.api_client import ApiClient


@dataclass(frozen=True)
class Page:
    items: list[Any]
    total: int
    page: int = 1
    per_page: int = 20


class MaterialRepository:
    """Centralize material endpoints and response mapping."""

    def __init__(self, api_client: ApiClient) -> None:
        self._api = api_client

    def list_materials_raw(self, params: dict[str, Any]) -> tuple[list[dict], int]:
        data = self._api.get("materials", params=params)
        return list(data.get("items", [])), int(data.get("total", 0))

    def list_materials(self, params: dict[str, Any]) -> Page:
        data = self._api.get("materials", params=params)
        return Page(
            items=[Material.from_dict(item) for item in data.get("items", [])],
            total=int(data.get("total", 0)),
            page=int(data.get("page", params.get("page", 1))),
            per_page=int(data.get("per_page", params.get("per_page", 20))),
        )

    def get_material_raw(self, material_id: str) -> dict:
        return self._api.get(f"materials/{material_id}")

    def get_material(self, material_id: str) -> Material:
        return Material.from_dict(self.get_material_raw(material_id))

    def list_featured_raw(self, limit: int = 10) -> list[dict]:
        data = self._api.get("materials/featured", params={"limit": limit})
        return list(data.get("items", []) if isinstance(data, dict) else data)

    def list_featured(self, limit: int = 10) -> list[Material]:
        return [Material.from_dict(item) for item in self.list_featured_raw(limit)]

    def list_related_raw(self, material_id: str, limit: int = 6) -> list[dict]:
        data = self._api.get(
            f"materials/{material_id}/related",
            params={"limit": limit},
        )
        return list(data.get("items", []) if isinstance(data, dict) else data)

    def list_related(self, material_id: str, limit: int = 6) -> list[Material]:
        return [Material.from_dict(item) for item in self.list_related_raw(material_id, limit)]

    def set_like(self, material_id: str, should_like: bool) -> tuple[bool, int]:
        if should_like:
            data = self._api.post(f"materials/{material_id}/like")
        else:
            data = self._api.delete(f"materials/{material_id}/like")
        return bool(data.get("is_liked", should_like)), int(data.get("like_count", 0))

    def set_favorite(self, material_id: str, should_favorite: bool) -> bool:
        """Set favorite using the users/me endpoint as the canonical writer."""
        if should_favorite:
            data = self._api.post(f"users/me/favorites/{material_id}")
        else:
            data = self._api.delete(f"users/me/favorites/{material_id}")
        return bool(data.get("is_favorited", should_favorite))
