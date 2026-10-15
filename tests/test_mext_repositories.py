import unittest
from types import SimpleNamespace

from _mext.repositories import (
    CommentRepository,
    DownloadRepository,
    MaterialRepository,
    UserRepository,
)
from _mext.services.api_worker import LibraryLoadWorker, MaterialsLoadWorker
from _mext.services.api_client import ApiError


class FakeApiClient:
    def __init__(self):
        self.calls = []
        self._config = SimpleNamespace(api_base_url="http://localhost:8000")
        self.responses = {}

    def queue(self, method, path, response):
        self.responses[(method, path)] = response

    def get(self, path, params=None, headers=None):
        self.calls.append(("GET", path, params))
        response = self.responses[("GET", path)]
        if isinstance(response, Exception):
            raise response
        return response

    def post(self, path, json=None, data=None, headers=None):
        self.calls.append(("POST", path, json))
        response = self.responses[("POST", path)]
        if isinstance(response, Exception):
            raise response
        return response

    def delete(self, path, headers=None):
        self.calls.append(("DELETE", path, None))
        response = self.responses[("DELETE", path)]
        if isinstance(response, Exception):
            raise response
        return response


class MaterialRepositoryTests(unittest.TestCase):
    def test_list_materials_maps_page_and_items(self):
        api = FakeApiClient()
        api.queue(
            "GET",
            "materials",
            {
                "items": [
                    {
                        "id": "mat-1",
                        "name": "Carbon fiber",
                        "category": "texture",
                    }
                ],
                "total": 1,
                "page": 2,
                "per_page": 10,
            },
        )

        page = MaterialRepository(api).list_materials({"page": 2, "per_page": 10})

        self.assertEqual(api.calls[0], ("GET", "materials", {"page": 2, "per_page": 10}))
        self.assertEqual(page.total, 1)
        self.assertEqual(page.page, 2)
        self.assertEqual(page.items[0].id, "mat-1")
        self.assertEqual(page.items[0].name, "Carbon fiber")

    def test_like_and_favorite_use_canonical_endpoints(self):
        api = FakeApiClient()
        api.queue("POST", "materials/mat-1/like", {"is_liked": True, "like_count": 7})
        api.queue("DELETE", "users/me/favorites/mat-1", {"is_favorited": False})

        repo = MaterialRepository(api)

        self.assertEqual(repo.set_like("mat-1", True), (True, 7))
        self.assertFalse(repo.set_favorite("mat-1", False))
        self.assertIn(("POST", "materials/mat-1/like", None), api.calls)
        self.assertIn(("DELETE", "users/me/favorites/mat-1", None), api.calls)


class DownloadRepositoryTests(unittest.TestCase):
    def test_resolve_download_expands_relative_verify_url(self):
        api = FakeApiClient()
        verify_path = "/api/v1/downloads/verify/token"
        verify_url = "http://localhost:8000/api/v1/downloads/verify/token"
        api.queue("POST", "downloads/generate-url", {"url": verify_path})
        api.queue(
            "GET",
            verify_url,
            {
                "presigned_url": "https://cdn.example/file.zip",
                "file_hash": None,
                "file_size": 42,
            },
        )

        result = DownloadRepository(api).resolve_download("mat-1")

        self.assertEqual(result.presigned_url, "https://cdn.example/file.zip")
        self.assertEqual(result.file_hash, "")
        self.assertEqual(result.file_size, 42)
        self.assertEqual(api.calls[1], ("GET", verify_url, None))


class CommentRepositoryTests(unittest.TestCase):
    def test_post_comment_maps_response(self):
        api = FakeApiClient()
        api.queue(
            "POST",
            "materials/mat-1/comments",
            {
                "id": "comment-1",
                "material_id": "mat-1",
                "user_id": "user-1",
                "username": "Ada",
                "content": "Looks useful",
            },
        )

        comment = CommentRepository(api).post_comment("mat-1", "Looks useful")

        self.assertEqual(comment.id, "comment-1")
        self.assertEqual(comment.username, "Ada")
        self.assertEqual(
            api.calls[0],
            ("POST", "materials/mat-1/comments", {"content": "Looks useful"}),
        )


class UserRepositoryTests(unittest.TestCase):
    def test_downloaded_materials_are_deduped_and_missing_items_are_skipped(self):
        api = FakeApiClient()
        api.queue(
            "GET",
            "users/me/downloads",
            [
                {"material_id": "mat-1"},
                {"material_id": "mat-1"},
                {"material_id": "missing"},
            ],
        )
        api.queue("GET", "materials/mat-1", {"id": "mat-1", "name": "Only once"})
        api.queue("GET", "materials/missing", ApiError(404, "Not found"))

        materials = UserRepository(api).list_downloaded_materials()

        self.assertEqual([material.id for material in materials], ["mat-1"])
        self.assertEqual(
            api.calls,
            [
                ("GET", "users/me/downloads", None),
                ("GET", "materials/mat-1", None),
                ("GET", "materials/missing", None),
            ],
        )


class ApiWorkerContractTests(unittest.TestCase):
    def test_materials_worker_emits_raw_items_without_dropping_server_fields(self):
        api = FakeApiClient()
        api.queue(
            "GET",
            "materials",
            {
                "items": [
                    {
                        "id": "mat-1",
                        "name": "Carbon fiber",
                        "category": "texture",
                        "server_only": "kept",
                    }
                ],
                "total": 1,
            },
        )
        captured = []
        worker = MaterialsLoadWorker(api, {"page": 1})
        worker.completed.connect(lambda items, total: captured.append((items, total)))

        worker.run()

        self.assertEqual(captured, [([{
            "id": "mat-1",
            "name": "Carbon fiber",
            "category": "texture",
            "server_only": "kept",
        }], 1)])

    def test_library_worker_preserves_downloads_when_favorites_fail(self):
        api = FakeApiClient()
        downloaded = {"id": "mat-1", "name": "Downloaded", "server_only": "kept"}
        api.queue("GET", "users/me/downloads", [{"material_id": "mat-1"}])
        api.queue("GET", "materials/mat-1", downloaded)
        api.queue("GET", "users/me/favorites", ApiError(500, "favorites offline"))
        captured = []
        worker = LibraryLoadWorker(api)
        worker.completed.connect(
            lambda materials, favorites: captured.append((materials, favorites))
        )

        worker.run()

        self.assertEqual(captured, [([downloaded], [])])


if __name__ == "__main__":
    unittest.main()
