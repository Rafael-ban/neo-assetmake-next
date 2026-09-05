import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.qt_harness import ensure_app


def setUpModule():
    ensure_app()


class VSScriptPanelTests(unittest.TestCase):
    def test_panel_only_displays_script_metadata_and_trust_dialog_has_two_actions(self):
        from core.vs_runtime.script_header import ScriptHeader
        from core.vs_runtime.trust import ScriptReference
        from gui.dialogs.vs_script_trust_dialog import VSScriptTrustDialog
        from gui.widgets.vs_script_panel import VSScriptPanel

        panel = VSScriptPanel()
        header = ScriptHeader(
            api_version=1,
            mode="compatible",
            capabilities=("source", "crop"),
            requires=("lsmas.LWLibavSource",),
            editor_output=0,
        )
        panel.set_script_info(
            reference=ScriptReference("project", "vapoursynth/pipeline.vpy"),
            canonical_root="C:/project/vapoursynth",
            main_script="C:/project/vapoursynth/pipeline.vpy",
            header=header,
            bundle_hash="a" * 64,
            trusted=False,
        )

        self.assertIn("project", panel.source_label.text())
        self.assertIn("compatible", panel.header_label.text())
        self.assertIn("未获信任", panel.trust_label.text())
        self.assertFalse(hasattr(panel, "script_editor"))

        dialog = VSScriptTrustDialog(
            canonical_root="C:/project/vapoursynth",
            main_script="C:/project/vapoursynth/pipeline.vpy",
            code_files=("pipeline.vpy", "modules/helper.py"),
            bundle_hash="a" * 64,
        )
        self.assertEqual(dialog.trust_button.text(), "信任并运行")
        self.assertEqual(dialog.cancel_button.text(), "取消")


if __name__ == "__main__":
    unittest.main()
