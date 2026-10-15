"""Repository layer for material forum API access."""

from _mext.repositories.auth_repository import AuthRepository
from _mext.repositories.comment_repository import CommentRepository
from _mext.repositories.download_repository import DownloadRepository
from _mext.repositories.material_repository import MaterialRepository
from _mext.repositories.user_repository import UserRepository

__all__ = [
    "AuthRepository",
    "CommentRepository",
    "DownloadRepository",
    "MaterialRepository",
    "UserRepository",
]
