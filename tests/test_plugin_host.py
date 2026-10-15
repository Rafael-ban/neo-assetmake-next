import unittest

from gui.main_window import MainWindow
from gui.plugin_host import PluginHandle, ThemeTokens, default_plugin_context
from _mext.ui import styles as mext_styles


class PluginHostTests(unittest.TestCase):
    def test_mext_styles_accept_host_theme_tokens(self):
        tokens = ThemeTokens(
            color_text_primary=("#111111", "#eeeeee"),
            color_text_secondary=("#222222", "#dddddd"),
            color_text_muted=("#333333", "#cccccc"),
            color_bg_surface=("#444444", "#bbbbbb"),
            color_bg_inset=("#555555", "#aaaaaa"),
            color_bg_elevated=("#666666", "#999999"),
            color_border=("#777777", "#888888"),
            color_success=("#008800", "#00aa00"),
            color_error=("#880000", "#aa0000"),
            color_warning=("#886600", "#aa8800"),
            color_accent=("#123456", "#654321"),
        )

        mext_styles.configure_theme_tokens(tokens)

        self.assertEqual(mext_styles.COLOR_ACCENT, ("#123456", "#654321"))
        self.assertEqual(mext_styles.COLOR_TEXT_PRIMARY, ("#111111", "#eeeeee"))

    def test_plugin_handle_shutdown_is_idempotent(self):
        class FakeWidget:
            def __init__(self):
                self.shutdown_calls = 0

            def shutdown(self):
                self.shutdown_calls += 1

        widget = FakeWidget()
        handle = PluginHandle(widget=widget, context=default_plugin_context())

        self.assertTrue(handle.shutdown())
        self.assertTrue(handle.shutdown())

        self.assertEqual(widget.shutdown_calls, 1)
        self.assertTrue(handle.health()["shutdown"])

    def test_plugin_handle_shutdown_swallows_plugin_errors(self):
        class FailingWidget:
            def shutdown(self, timeout_ms=0):
                raise RuntimeError("boom")

        handle = PluginHandle(widget=FailingWidget(), context=default_plugin_context())

        with self.assertLogs("gui.plugin_host", level="ERROR"):
            self.assertFalse(handle.shutdown(timeout_ms=1))
        self.assertTrue(handle.health()["shutdown"])

    def test_plugin_handle_apply_theme_calls_widget_hook(self):
        class ThemeAwareWidget:
            def __init__(self):
                self.apply_theme_calls = 0

            def apply_theme(self):
                self.apply_theme_calls += 1

        widget = ThemeAwareWidget()
        handle = PluginHandle(widget=widget, context=default_plugin_context())

        self.assertTrue(handle.apply_theme())

        self.assertEqual(widget.apply_theme_calls, 1)

    def test_default_plugin_context_migrates_legacy_language_values(self):
        self.assertEqual(
            default_plugin_context({"language": "English"}).locale,
            "en_US",
        )
        self.assertEqual(
            default_plugin_context({"language": "中文"}).locale,
            "zh_CN",
        )

    def test_main_window_forum_context_uses_user_settings(self):
        window = MainWindow.__new__(MainWindow)
        window._read_user_settings = lambda: {"language": "English"}

        context = MainWindow._create_forum_plugin_context(window)

        self.assertEqual(context.settings, {"language": "English"})
        self.assertEqual(context.locale, "en_US")


if __name__ == "__main__":
    unittest.main()
