"""Comment API repository."""

from __future__ import annotations

from dataclasses import dataclass

from _mext.models.comment import Comment
from _mext.services.api_client import ApiClient


@dataclass(frozen=True)
class CommentPage:
    items: list[Comment]
    total: int


class CommentRepository:
    """Centralize material comment endpoints."""

    def __init__(self, api_client: ApiClient) -> None:
        self._api = api_client

    def list_comments(
        self,
        material_id: str,
        *,
        page: int = 1,
        per_page: int = 20,
    ) -> CommentPage:
        items, total = self.list_comments_raw(material_id, page=page, per_page=per_page)
        return CommentPage(
            items=[Comment.from_dict(item) for item in items],
            total=total,
        )

    def list_comments_raw(
        self,
        material_id: str,
        *,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[dict], int]:
        data = self._api.get(
            f"materials/{material_id}/comments",
            params={"page": page, "per_page": per_page},
        )
        return list(data.get("items", [])), int(data.get("total", 0))

    def post_comment_raw(self, material_id: str, content: str) -> dict:
        return self._api.post(
            f"materials/{material_id}/comments",
            json={"content": content},
        )

    def post_comment(self, material_id: str, content: str) -> Comment:
        return Comment.from_dict(self.post_comment_raw(material_id, content))

    def delete_comment(self, comment_id: str) -> None:
        self._api.delete(f"comments/{comment_id}")
