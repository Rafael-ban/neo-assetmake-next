"""更新渠道界面的聚焦测试；不访问网络或真实用户设置。"""
import unittest
from unittest.mock import Mock, patch

from tests.qt_harness import ensure_app
from config.update_preferences import UpdateChannel
from gui.dialogs.update_dialog import UpdateDialog


def setUpModule():
    ensure_app()


class UpdateChannelDialogTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.service.is_checking = False
        self.service.is_downloading = False
        self.patches = [
            patch("gui.dialogs.update_dialog.UpdateService", return_value=self.service),
            patch("gui.dialogs.update_dialog.load_update_channel", return_value=UpdateChannel.STABLE),
            patch("gui.dialogs.update_dialog.save_update_channel"),
        ]
        for mocked in self.patches:
            mocked.start()
            self.addCleanup(mocked.stop)
        self.dialog = UpdateDialog()
        self.addCleanup(self.dialog.deleteLater)

    def test_switch_channel_saves_and_checks_without_old_installer(self):
        self.dialog._downloaded_path = "old-installer.exe"
        self.dialog.combo_channel.setCurrentIndex(1)
        from gui.dialogs.update_dialog import save_update_channel
        save_update_channel.assert_called_once_with(UpdateChannel.PREVIEW)
        self.service.check_for_updates.assert_called_once_with(channel=UpdateChannel.PREVIEW)
        self.assertIsNone(self.dialog._downloaded_path)
        self.assertFalse(self.dialog.combo_channel.isEnabled())
        self.assertFalse(self.dialog.btn_recheck.isEnabled())
        self.dialog._on_check_completed(None)
        self.assertTrue(self.dialog.combo_channel.isEnabled())
        self.assertIn("开发预览版渠道暂无可用更新", self.dialog.label_status.text())

    def test_error_can_retry_and_clears_old_error(self):
        self.dialog._on_check_failed("网络失败")
        self.assertTrue(self.dialog.btn_recheck.isEnabled())
        self.dialog.btn_recheck.click()
        self.service.check_for_updates.assert_called_once_with(channel=UpdateChannel.STABLE)
        self.assertNotIn("网络失败", self.dialog.label_current_version.text())
        self.assertEqual(self.dialog.stack.currentIndex(), 0)

    def test_download_locks_channel_until_finished(self):
        self.dialog._start_download()
        self.assertFalse(self.dialog.combo_channel.isEnabled())
        self.assertFalse(self.dialog.btn_recheck.isEnabled())
        self.dialog._on_download_completed("new-installer.exe")
        self.assertTrue(self.dialog.combo_channel.isEnabled())
        self.assertEqual(self.dialog._downloaded_path, "new-installer.exe")

    def test_saved_channel_is_restored_without_starting_check(self):
        with patch("gui.dialogs.update_dialog.load_update_channel", return_value=UpdateChannel.PREVIEW):
            dialog = UpdateDialog()
            self.addCleanup(dialog.deleteLater)
            self.assertEqual(dialog.combo_channel.currentData(), "preview")
            self.service.check_for_updates.assert_not_called()


if __name__ == "__main__":
    unittest.main()
