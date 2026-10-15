import unittest
from unittest import mock

from core.remote_asset_manager import RemoteAssetManager, RemoteAssetManagerError
from core.rndis_device_service import DeviceProbeResult, RndisAdapter


def make_probe(auth_required=False):
    return DeviceProbeResult(
        base_url="http://192.168.137.2",
        adapter=RndisAdapter(
            name="EPass",
            interface_description="EPass RNDIS Remote NDIS Compatible Device",
            status="Up",
        ),
        health={
            "status": "ok",
            "device": "epass",
            "protocol_version": "1.0",
            "device_id": "device-01",
            "capabilities": ["assets", "stream"],
            "auth_required": auth_required,
        },
    )


class FakeService:
    def __init__(self):
        self.synced = False
        self.listed = False
        self.uploads = []
        self.restarted = False

    def sync_time(self):
        self.synced = True

    def list_assets(self):
        self.listed = True
        return []

    def upload_asset(self, path, progress=None, cancel_event=None):
        self.uploads.append(str(path))
        if progress:
            progress(50, "upload")
        return {"uuid": "asset-1"}

    def restart_drm(self):
        self.restarted = True


class RemoteAssetManagerTests(unittest.TestCase):
    def test_connect_caches_session_and_syncs_time(self):
        service = FakeService()
        detect = mock.Mock(return_value=make_probe())
        manager = RemoteAssetManager(
            detect_func=detect,
            service_factory=lambda base_url, token: service,
        )

        first = manager.connect()
        second = manager.connect()

        self.assertIs(first, second)
        self.assertEqual(detect.call_count, 1)
        self.assertTrue(service.synced)
        self.assertEqual(first.device_id, "device-01")

    def test_auth_required_blocks_asset_operations_without_token(self):
        manager = RemoteAssetManager(
            detect_func=lambda: make_probe(auth_required=True),
            service_factory=lambda base_url, token: FakeService(),
        )

        with self.assertRaisesRegex(RemoteAssetManagerError, "authentication"):
            manager.list_assets()

    def test_token_marks_auth_required_session_authenticated(self):
        service = FakeService()
        manager = RemoteAssetManager(
            device_token="secret",
            detect_func=lambda: make_probe(auth_required=True),
            service_factory=lambda base_url, token: service,
        )

        manager.list_assets()

        self.assertTrue(manager.session.authenticated)
        self.assertTrue(service.listed)

    def test_upload_can_restart_drm_and_emit_restart_progress(self):
        service = FakeService()
        progress = []
        manager = RemoteAssetManager(
            detect_func=lambda: make_probe(),
            service_factory=lambda base_url, token: service,
        )

        payload = manager.upload_asset(
            "asset-dir",
            restart=True,
            progress=lambda percent, message: progress.append((percent, message)),
        )

        self.assertEqual(payload["uuid"], "asset-1")
        self.assertTrue(service.restarted)
        self.assertIn((95, "正在重启 DrmApp..."), progress)


if __name__ == "__main__":
    unittest.main()
