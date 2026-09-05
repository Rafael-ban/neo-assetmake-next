"""M5/C1：legacy VSConfig 不再参与 production VSPipe/export 资格门。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import media_tools


class LegacyVapourSynthIsolationTests(unittest.TestCase):
    def test_non_vspipe_media_subprocess_env_does_not_read_legacy_vsconfig(self):
        """x264/muxer 仍获得媒体根 PATH，但不再写 VS 插件环境。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            media_dir = Path(temp_dir) / "tools" / "media"
            media_dir.mkdir(parents=True)
            muxer = media_dir / "MP4Box.exe"
            muxer.touch()
            (media_dir / "Lib" / "site-packages").mkdir(parents=True)
            with mock.patch(
                "config.vsconfig.load_vsconfig",
                side_effect=AssertionError("legacy VSConfig must not be read"),
            ), mock.patch.dict(
                os.environ,
                {"VAPOURSYNTH_EXTRA_PLUGIN_PATH": "poisoned-legacy-plugin"},
                clear=False,
            ):
                env = media_tools.build_media_subprocess_env(str(muxer))

        self.assertEqual(env["VAPOURSYNTH_EXTRA_PLUGIN_PATH"], "poisoned-legacy-plugin")
        self.assertTrue(env["PATH"].startswith(str(media_dir)))
        self.assertTrue(
            env["PYTHONPATH"].startswith(str(media_dir / "Lib" / "site-packages"))
        )

    def test_refresh_does_not_touch_legacy_vsconfig_cache(self):
        from config.vsconfig import load_vsconfig

        load_vsconfig.cache_clear()
        load_vsconfig()
        before = load_vsconfig.cache_info()
        media_tools.MediaToolchain.refresh()

        self.assertEqual(load_vsconfig.cache_info(), before)


if __name__ == "__main__":
    unittest.main()
