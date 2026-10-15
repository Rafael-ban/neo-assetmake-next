import json
import subprocess
from pathlib import Path
import unittest
from unittest import mock
import zipfile

from core.http_remote_service import _make_asset_archive
from core.rndis_device_service import (
    DeviceProbeError,
    RNDIS_ADAPTER_NAME,
    RndisDetectionError,
    RndisAdapter,
    detect_rndis_adapter,
    list_rndis_adapters,
    probe_device,
    verify_device_route,
)


class RndisDetectionTests(unittest.TestCase):
    def test_list_rndis_adapters_parses_single_adapter(self):
        payload = {
            "Name": "EPass",
            "InterfaceDescription": RNDIS_ADAPTER_NAME,
            "Status": "Up",
            "MacAddress": "00-11-22-33-44-55",
            "ifIndex": 12,
        }
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )
        with mock.patch("platform.system", return_value="Windows"), mock.patch(
            "subprocess.run", return_value=completed
        ):
            adapters = list_rndis_adapters()

        self.assertEqual(len(adapters), 1)
        self.assertEqual(adapters[0].name, "EPass")
        self.assertTrue(adapters[0].is_up)

    def test_detect_rndis_adapter_prefers_up_adapter(self):
        down = {
            "Name": "EPass down",
            "InterfaceDescription": RNDIS_ADAPTER_NAME,
            "Status": "Disconnected",
        }
        up = {
            "Name": "EPass up",
            "InterfaceDescription": RNDIS_ADAPTER_NAME,
            "Status": "Up",
        }
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps([down, up]),
            stderr="",
        )
        with mock.patch("platform.system", return_value="Windows"), mock.patch(
            "subprocess.run", return_value=completed
        ):
            adapter = detect_rndis_adapter()

        self.assertEqual(adapter.name, "EPass up")

    def test_list_rndis_adapters_returns_empty_on_non_windows(self):
        with mock.patch("platform.system", return_value="Linux"), mock.patch(
            "subprocess.run"
        ) as run:
            self.assertEqual(list_rndis_adapters(), [])
        run.assert_not_called()

    def test_list_rndis_adapters_reports_powershell_failure(self):
        completed = mock.Mock(returncode=1, stdout="", stderr="denied")
        with mock.patch("platform.system", return_value="Windows"), mock.patch(
            "subprocess.run", return_value=completed
        ):
            with self.assertRaisesRegex(RndisDetectionError, "denied"):
                list_rndis_adapters()

    def test_list_rndis_adapters_reports_timeout(self):
        with mock.patch("platform.system", return_value="Windows"), mock.patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired("powershell", 8)
        ):
            with self.assertRaises(RndisDetectionError):
                list_rndis_adapters()

    def test_probe_device_rejects_missing_health_schema(self):
        response = mock.Mock()
        response.json.return_value = {"status": "ok"}
        response.raise_for_status.return_value = None
        with mock.patch("core.rndis_device_service._http_get", return_value=response):
            with self.assertRaisesRegex(DeviceProbeError, "health schema"):
                probe_device()

    def test_probe_device_accepts_epass_health_schema(self):
        response = mock.Mock()
        response.json.return_value = {
            "status": "ok",
            "device": "epass",
            "protocol_version": "1.0",
            "device_id": "device-01",
            "capabilities": ["assets", "stream"],
        }
        response.raise_for_status.return_value = None
        with mock.patch("core.rndis_device_service._http_get", return_value=response):
            result = probe_device()

        self.assertEqual(result.health["device"], "epass")
        self.assertEqual(result.health["device_id"], "device-01")

    def test_verify_device_route_rejects_wrong_if_index(self):
        adapter = RndisAdapter(
            name="EPass",
            interface_description=RNDIS_ADAPTER_NAME,
            status="Up",
            if_index=12,
        )
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps({"DestinationPrefix": "192.168.137.2/32", "ifIndex": 99}),
            stderr="",
        )
        with mock.patch("platform.system", return_value="Windows"), mock.patch(
            "subprocess.run", return_value=completed
        ):
            with self.assertRaisesRegex(RndisDetectionError, "does not use"):
                verify_device_route(adapter)


class AssetArchiveTests(unittest.TestCase):
    def test_make_asset_archive_contains_directory_contents_only(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            asset_dir = root / "asset"
            nested = asset_dir / "nested"
            nested.mkdir(parents=True)
            (asset_dir / "epconfig.json").write_text("{}", encoding="utf-8")
            (nested / "icon.png").write_bytes(b"png")

            archive_path = _make_asset_archive(asset_dir)
            try:
                with zipfile.ZipFile(archive_path, "r") as archive:
                    names = sorted(archive.namelist())
            finally:
                archive_path.unlink(missing_ok=True)

        self.assertEqual(names, ["epconfig.json", "nested/icon.png"])


if __name__ == "__main__":
    unittest.main()
