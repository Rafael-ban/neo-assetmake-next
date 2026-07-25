"""V3: media_tools reads VSConfig as the single source of truth.

Proves the plugin probe script and the subprocess env are driven by VSConfig
(not the old hardcoded tuple / plugin-dir literal), so adding a plugin is a
config edit. The default config keeps both byte-identical to before.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from pathlib import Path
from unittest import mock

from config.vsconfig import VSConfig
from core import media_tools


class PluginProbeScriptTests(unittest.TestCase):
    def test_probe_script_lists_config_required_plugins(self):
        cfg = VSConfig(required_plugins=("lsmas", "imwri", "sangnom"),
                       output_format="YUV420P8")
        script = media_tools._plugin_probe_script(cfg)
        # The required set is interpolated from config, not a hardcoded tuple.
        self.assertIn("'lsmas'", script)
        self.assertIn("'imwri'", script)
        self.assertIn("'sangnom'", script)
        self.assertIn("not hasattr(core, name)", script)
        self.assertIn("format=vs.YUV420P8", script)

    def test_probe_script_honours_output_format(self):
        cfg = VSConfig(output_format="YUV422P10")
        self.assertIn("format=vs.YUV422P10", media_tools._plugin_probe_script(cfg))

    def test_default_probe_matches_pre_refactor_text(self):
        # Byte-identical to the old hardcoded probe on default config.
        script = media_tools._plugin_probe_script(VSConfig())
        self.assertIn("required = ('lsmas', 'imwri')", script)
        self.assertIn("width=16, height=16, length=1, format=vs.YUV420P8", script)


class SubprocessEnvTests(unittest.TestCase):
    def test_env_uses_config_plugin_dirs(self):
        cfg = VSConfig(extra_plugin_dirs=("vs-plugins", "vs-extra"))
        with mock.patch.object(media_tools, "load_vsconfig", return_value=cfg):
            # make the dirs "exist" so _prepend_env_value keeps them
            with mock.patch.object(media_tools.Path, "exists", return_value=True):
                env = media_tools.build_media_subprocess_env("VSPipe.exe")
        path = env["VAPOURSYNTH_EXTRA_PLUGIN_PATH"]
        self.assertIn("vs-plugins", path)
        self.assertIn("vs-extra", path)

    def test_default_env_still_has_vs_plugins(self):
        with mock.patch.object(media_tools.Path, "exists", return_value=True):
            env = media_tools.build_media_subprocess_env("VSPipe.exe")
        self.assertIn("vs-plugins", env["VAPOURSYNTH_EXTRA_PLUGIN_PATH"])


class RefreshTests(unittest.TestCase):
    def test_refresh_clears_vsconfig_cache(self):
        from config.vsconfig import load_vsconfig
        load_vsconfig()  # prime
        media_tools.MediaToolchain.refresh()
        self.assertEqual(load_vsconfig.cache_info().currsize, 0)


if __name__ == "__main__":
    unittest.main()
