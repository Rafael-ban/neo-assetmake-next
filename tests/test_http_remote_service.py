import io
from pathlib import Path
import threading
import tempfile
import unittest
import zipfile

from core.http_remote_service import (
    HttpRemoteError,
    HttpRemoteService,
    RemoteAsset,
)


class FakeResponse:
    def __init__(self, payload=None, content=b"", headers=None, chunks=None):
        self._payload = payload if payload is not None else {}
        self.content = content
        self.headers = headers or {}
        self._chunks = chunks or [content]
        self.closed = False

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload

    def iter_content(self, chunk_size=1):
        yield from self._chunks

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


def make_zip(entries):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return data.getvalue()


class RemoteAssetTests(unittest.TestCase):
    def test_remote_asset_rejects_invalid_size(self):
        with self.assertRaisesRegex(HttpRemoteError, "Invalid asset size"):
            RemoteAsset.from_dict({"uuid": "asset-1", "size": "abc"})


class HttpRemoteServiceTests(unittest.TestCase):
    def test_delete_asset_rejects_url_path_injection(self):
        service = HttpRemoteService(session=FakeSession(FakeResponse()))

        with self.assertRaisesRegex(HttpRemoteError, "Invalid asset UUID"):
            service.delete_asset("../evil")

    def test_get_icon_rejects_url_path_injection(self):
        service = HttpRemoteService(session=FakeSession(FakeResponse()))

        with self.assertRaisesRegex(HttpRemoteError, "Invalid asset UUID"):
            service.get_icon("asset/../evil")

    def test_download_asset_rejects_zip_slip_entry(self):
        zip_bytes = make_zip({"../evil.txt": "owned"})
        service = HttpRemoteService(
            session=FakeSession(
                FakeResponse(
                    headers={"content-length": str(len(zip_bytes))},
                    chunks=[zip_bytes],
                )
            )
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(HttpRemoteError, "Unsafe archive entry"):
                service.download_asset("asset-1", temp_dir)

            self.assertFalse((Path(temp_dir).parent / "evil.txt").exists())

    def test_download_asset_extracts_safe_archive(self):
        zip_bytes = make_zip({"epconfig.json": "{}", "nested/icon.png": "png"})
        service = HttpRemoteService(
            session=FakeSession(
                FakeResponse(
                    headers={"content-length": str(len(zip_bytes))},
                    chunks=[zip_bytes],
                )
            )
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            result = service.download_asset("asset-1", temp_dir)

            self.assertEqual(result.name, "asset-1")
            self.assertEqual((result / "epconfig.json").read_text(encoding="utf-8"), "{}")
            self.assertEqual((result / "nested" / "icon.png").read_text(encoding="utf-8"), "png")

    def test_probe_stream_rejects_non_mjpeg_response(self):
        response = FakeResponse(headers={"content-type": "application/json"})
        service = HttpRemoteService(session=FakeSession(response))

        with self.assertRaisesRegex(HttpRemoteError, "MJPEG"):
            service.probe_stream()

        self.assertTrue(response.closed)

    def test_probe_stream_accepts_mjpeg_response(self):
        response = FakeResponse(headers={"content-type": "multipart/x-mixed-replace"})
        service = HttpRemoteService(session=FakeSession(response))

        service.probe_stream()

        self.assertTrue(response.closed)

    def test_device_token_header_is_added_to_requests(self):
        session = FakeSession(FakeResponse(payload={"items": []}))
        service = HttpRemoteService(session=session, device_token="secret-token")

        service.list_assets()

        _, _, kwargs = session.calls[0]
        self.assertEqual(kwargs["headers"]["X-EPass-Token"], "secret-token")

    def test_upload_asset_honors_cancel_event_before_request(self):
        event = threading.Event()
        event.set()
        session = FakeSession(FakeResponse())
        service = HttpRemoteService(session=session)

        with tempfile.TemporaryDirectory() as temp_dir:
            asset_dir = Path(temp_dir) / "asset"
            asset_dir.mkdir()
            (asset_dir / "epconfig.json").write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(HttpRemoteError, "cancelled"):
                service.upload_asset(asset_dir, cancel_event=event)

        self.assertEqual(session.calls, [])


if __name__ == "__main__":
    unittest.main()
