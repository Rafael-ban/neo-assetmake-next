"""更新渠道的筛选、排序与独立设置存储。"""
import json
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QEventLoop, QSettings, QTimer

from config.constants import UPDATE_API_SOURCES
from config.update_preferences import (
    UpdateChannel, load_update_channel, save_update_channel,
)
from core.update_service import (
    SourceResult, UpdateCheckWorker, UpdateService, VersionComparer,
)
from tests.qt_harness import ensure_app


def setUpModule():
    ensure_app()


def release(tag="v2.3.0", preview=False, **extra):
    base = "https://github.com/Rafael-ban/neo-assetmake-next/releases/download/"
    name = f"ArknightsPassMaker_{tag}_Setup.exe"
    return {
        "tag_name": tag, "prerelease": preview,
        "assets": [
            {"name": name, "browser_download_url": f"{base}{tag}/{name}"},
            {"name": "SHA256SUMS", "browser_download_url": f"{base}{tag}/SHA256SUMS"},
        ], **extra,
    }


class Response:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return json.dumps(self.data).encode()


class UpdateChannelTests(unittest.TestCase):
    def fetch(self, pages, channel=UpdateChannel.STABLE):
        worker = UpdateCheckWorker("2.1.0", channel=channel)
        with patch("core.update_service.urlopen", side_effect=[Response(p) for p in pages]) as request:
            with patch.object(worker, "_fetch_expected_sha256", return_value="a" * 64):
                result = worker._fetch_from_source(UPDATE_API_SOURCES[0])
        return result, request

    def test_stable_filters_preview_draft_tools_and_incomplete(self):
        items = [release(preview=True), release("v9.0.0", draft=True),
                 release("media-tools-r79-v1"), release("v8.0.0", assets=[]),
                 release("v7.0.0", assets=[{"name": "other.exe"}, {"name": "SHA256SUMS"}]),
                 release("v2.2.0")]
        result, _ = self.fetch([items])
        self.assertEqual(result.version, "2.2.0")

    def test_preview_uses_version_before_publication_or_channel(self):
        result, _ = self.fetch([[release("v2.2.0"), release("v2.3.0", preview=True)]], UpdateChannel.PREVIEW)
        self.assertEqual(result.version, "2.3.0")

    def test_formal_wins_same_base_version(self):
        result, _ = self.fetch([[release("v2.3.0-rc.9", preview=True), release("v2.3.0")]], UpdateChannel.PREVIEW)
        self.assertEqual(result.version, "2.3.0")

    def test_pagination_finds_app_after_tools(self):
        result, request = self.fetch([[release("media-tools-r79-v1")] * 100, [release()]])
        self.assertEqual(result.version, "2.3.0")
        self.assertIn("page=2", request.call_args_list[1].args[0].full_url)
        self.assertNotIn("latest", request.call_args_list[0].args[0].full_url)

    def test_no_matching_release_is_normal(self):
        result, _ = self.fetch([[release(preview=True)]])
        self.assertIsNone(result)

    def test_selected_checksum_failure_propagates(self):
        worker = UpdateCheckWorker("2.1.0")
        with patch("core.update_service.urlopen", return_value=Response([release()])):
            with patch.object(worker, "_fetch_expected_sha256", side_effect=ValueError("bad checksum")):
                with self.assertRaisesRegex(ValueError, "bad checksum"):
                    worker._fetch_from_source(UPDATE_API_SOURCES[0])

    def test_network_failure_propagates(self):
        with patch("core.update_service.urlopen", side_effect=OSError("offline")):
            with self.assertRaisesRegex(OSError, "offline"):
                UpdateCheckWorker("2.1.0")._fetch_from_source(UPDATE_API_SOURCES[0])

    def test_current_preview_can_upgrade_same_number_formal_only(self):
        worker = UpdateCheckWorker("2.2.1")
        with patch("core.update_service.APP_VERSION", "2.2.1"), patch("core.update_service.APP_VERSION_LABEL", "2.2.1（开发预览版）"):
            self.assertTrue(worker._is_update(worker._parse_release_data(release("v2.2.1"))))
            self.assertFalse(worker._is_update(worker._parse_release_data(release("v2.2.1", preview=True))))
            self.assertFalse(worker._is_update(worker._parse_release_data(release("v2.2.0"))))

    def test_prerelease_numeric_order(self):
        self.assertTrue(VersionComparer.is_newer("2.3.0-rc.10", "2.3.0-rc.2"))
        self.assertTrue(VersionComparer.is_newer("2.3.0", "2.3.0-rc.10"))

    def test_settings_default_persist_and_invalid_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(f"{directory}/updates.ini", QSettings.Format.IniFormat)
            with patch("config.update_preferences.QSettings", return_value=settings):
                self.assertEqual(load_update_channel(), UpdateChannel.STABLE)
                save_update_channel(UpdateChannel.PREVIEW)
                self.assertEqual(load_update_channel(), UpdateChannel.PREVIEW)
                settings.setValue("update_channel", "broken")
                self.assertEqual(load_update_channel(), UpdateChannel.STABLE)

    def test_completion_clears_busy_before_reentrant_retry(self):
        service = UpdateService("2.1.0")
        loop = QEventLoop()
        completions = []

        def completed(result):
            completions.append((result, service.is_checking))
            if len(completions) == 1:
                service.check_for_updates(UpdateChannel.PREVIEW)
            else:
                loop.quit()

        service.check_completed.connect(completed)
        with patch("core.update_service.MultiSourceRequestManager.race_request", return_value=SourceResult("test", True, None)):
            service.check_for_updates(UpdateChannel.STABLE)
            QTimer.singleShot(3000, loop.quit)
            loop.exec()
            if service._check_worker:
                service._check_worker.wait(3000)
                service.close()
        self.assertEqual(completions, [(None, False), (None, False)])
