"""Optional RIFE DLC activates only when a script declares rife.RIFE."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from config.vs_runtime import PluginConfig, VSRuntimeConfig
from core.vs_runtime.snapshot import RuntimeSnapshot
from resources.vapoursynth.python.assetmaker_vs.native_plugins import (
    NativePluginPolicyError,
    activate_rife_plugin,
    configure_native_plugins,
    verify_native_plugin_requirements,
)
from tests.test_vs_native_plugins import _Core, _Plugin
from tests.test_vs_runtime_layout import _build_r79_fixture


class RifeAutoDiscoveryTests(unittest.TestCase):
    def test_missing_dlc_is_only_an_error_for_rife_scripts(self):
        with tempfile.TemporaryDirectory() as temporary:
            core = _Core({})
            state = configure_native_plugins(core, ())
            unchanged, handle = activate_rife_plugin(
                core, state, temporary, ("lsmas.LWLibavSource",)
            )
            self.assertIs(unchanged, state)
            self.assertIsNone(handle)
            self.assertEqual(core.loaded_paths, [])
            with self.assertRaisesRegex(
                NativePluginPolicyError, "请将 RIFE DLC 解压到应用目录"
            ):
                activate_rife_plugin(core, state, temporary, ("rife.RIFE",))

    def test_app_local_dlc_loads_once_and_verifies_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "tools" / "vs-rife" / "rife"
            directory.mkdir(parents=True)
            dll = directory / "librife_windows_x86-64.dll"
            dll.write_bytes(b"test")
            core = _Core({
                directory.resolve(): (_Plugin("rife", str(dll), ("RIFE",)),),
            })
            state = configure_native_plugins(core, ())
            loaded, handle = activate_rife_plugin(
                core, state, temporary, ("rife.RIFE",)
            )
            if handle is not None:
                handle.close()
            verify_native_plugin_requirements(core, loaded, ("rife.RIFE",))
            again, handle = activate_rife_plugin(
                core, loaded, temporary, ("rife.RIFE",)
            )
            self.assertIs(again, loaded)
            self.assertIsNone(handle)
            self.assertEqual(core.loaded_paths, [directory.resolve()])

    def test_app_local_dlc_plugin_and_models_change_runtime_fingerprint(self):
        from core.vs_runtime.vs_loader import compute_runtime_fingerprint

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _build_r79_fixture(root)
            runtime = VSRuntimeConfig()
            without_dlc = compute_runtime_fingerprint(root, runtime)
            rife = root / "tools" / "vs-rife" / "rife"
            models = rife / "models" / "rife-v4.6_ensembleFalse"
            models.mkdir(parents=True)
            plugin = rife / "librife_windows_x86-64.dll"
            plugin.write_bytes(b"plugin-v1")
            binary = models / "flownet.bin"
            binary.write_bytes(b"model-v1")
            (models / "flownet.param").write_bytes(b"parameters-v1")
            with_dlc = compute_runtime_fingerprint(root, runtime)
            self.assertNotEqual(with_dlc, without_dlc)
            binary.write_bytes(b"model-v2")
            changed_model = compute_runtime_fingerprint(root, runtime)
            self.assertNotEqual(changed_model, with_dlc)
            plugin.write_bytes(b"plugin-v2")
            self.assertNotEqual(compute_runtime_fingerprint(root, runtime), changed_model)

    def test_snapshot_drops_only_legacy_rife_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime = VSRuntimeConfig(plugins=PluginConfig(
                native_plugin_dirs=(
                    r"C:\old\tools\vs-rife\rife",
                    r"C:\custom\other-plugin",
                ),
            ))
            with (
                mock.patch("core.vs_runtime.snapshot.load_vs_runtime", return_value=runtime),
                mock.patch(
                    "core.vs_runtime.snapshot.compute_runtime_fingerprint",
                    return_value="a" * 64,
                ),
            ):
                snapshot = RuntimeSnapshot.resolve(temporary, user_path="unused")
            self.assertEqual(
                snapshot.runtime.plugins.native_plugin_dirs,
                (r"C:\custom\other-plugin",),
            )
            self.assertEqual(
                runtime.plugins.native_plugin_dirs[0],
                r"C:\old\tools\vs-rife\rife",
            )


if __name__ == "__main__":
    unittest.main()
