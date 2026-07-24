"""S5: cross-project contamination regression for ConfigPanel.set_config.

Old defect: set_config only wrote transition/overlay sub-widgets when the
incoming config HAD those sections — widgets kept the previous project's
values, and the next update_config_from_ui() wrote them into the new
project's config (silent corruption on the very first edit).
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest

from tests.qt_harness import ensure_app

from config.epconfig import (
    EPConfig,
    Transition,
    TransitionOptions,
    TransitionType,
    Overlay,
    OverlayType,
    ArknightsOverlayOptions,
)


def setUpModule():
    ensure_app()


def _rich_config() -> EPConfig:
    cfg = EPConfig()
    cfg.transition_in = Transition(
        type=TransitionType.FADE,
        options=TransitionOptions(duration=800000, background_color="#123456"),
    )
    cfg.transition_loop = Transition(
        type=TransitionType.FADE,
        options=TransitionOptions(duration=900000, background_color="#654321"),
    )
    cfg.overlay = Overlay(
        type=OverlayType.ARKNIGHTS,
        arknights_options=ArknightsOverlayOptions(
            operator_name="AMIYA", operator_code="RHODES - 001", color="#ff0000"
        ),
    )
    return cfg


class ConfigPanelResetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from gui.widgets.config_panel import ConfigPanel

        cls.panel = ConfigPanel()

    def test_opening_plain_project_after_rich_one_does_not_inherit(self):
        self.panel.set_config(_rich_config(), "")
        plain = EPConfig()
        self.panel.set_config(plain, "")

        # The first edit rebuilds the config from widget state — the old code
        # resurrected project A's fade transition and operator data here.
        self.panel.update_config_from_ui()
        cfg = self.panel.get_config()

        self.assertEqual(cfg.transition_in.type, TransitionType.NONE)
        self.assertEqual(cfg.transition_loop.type, TransitionType.NONE)
        self.assertEqual(cfg.overlay.type, OverlayType.NONE)
        self.assertEqual(self.panel.spin_trans_in_duration.value(), 500000)
        self.assertEqual(self.panel.edit_trans_in_color.text(), "#000000")
        defaults = ArknightsOverlayOptions()
        self.assertEqual(self.panel.edit_ark_name.text(), defaults.operator_name)
        self.assertEqual(self.panel.edit_ark_color.text(), defaults.color)

    def test_rich_config_still_populates_widgets_after_reset(self):
        self.panel.set_config(EPConfig(), "")
        rich = _rich_config()
        self.panel.set_config(rich, "")
        self.assertEqual(self.panel.spin_trans_in_duration.value(), 800000)
        self.assertEqual(self.panel.edit_trans_in_color.text(), "#123456")
        self.assertEqual(self.panel.edit_ark_name.text(), "AMIYA")


if __name__ == "__main__":
    unittest.main()
