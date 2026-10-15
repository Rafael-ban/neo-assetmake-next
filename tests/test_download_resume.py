"""Regression tests for resume-download corruption (Cluster C1).

When a server ignores a Range request and answers 200 with the full body, the
client must restart (truncate) rather than append to the partial temp file.
"""
import tempfile
import types
import unittest
from pathlib import Path

import httpx

from _mext.services.api_client import ApiClient

FULL = b"ABCDEFGHIJ" * 100  # 1000 bytes


def _client(handler):
    cfg = types.SimpleNamespace(api_url="http://test.local", api_timeout=30, api_stream_timeout=300)
    c = ApiClient(config=cfg)
    c._client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test.local")
    c._build_headers = lambda include_auth=False: {}
    c.should_send_auth_to = lambda url: False
    return c


def _run(handler):
    c = _client(handler)
    d = Path(tempfile.mkdtemp())
    dest = d / "asset.bin"
    dest.with_name(f".{dest.name}.tmp").write_bytes(FULL[:400])  # 400-byte partial
    list(c.stream_download("http://test.local/asset.bin", dest, resume_from=400))
    return dest.read_bytes()


class DownloadResumeTests(unittest.TestCase):
    def test_server_ignores_range_returns_200_full_body(self):
        def handler(request):
            return httpx.Response(200, content=FULL, headers={"content-length": str(len(FULL))})
        got = _run(handler)
        # Old behaviour appended -> 1400 bytes (corrupt). Fixed -> exactly the full body.
        self.assertEqual(got, FULL)

    def test_server_honours_range_returns_206_remaining(self):
        def handler(request):
            start = 400
            return httpx.Response(206, content=FULL[start:],
                                  headers={"content-length": str(len(FULL) - start)})
        got = _run(handler)
        self.assertEqual(got, FULL)


if __name__ == "__main__":
    unittest.main()
