"""S6.8 + S6.11: rotation preserves the crop box; basic panel doesn't clobber
a custom class icon."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest

from tests.qt_harness import ensure_app


def setUpModule():
    ensure_app()


class RotationCropRemapTests(unittest.TestCase):
    def _widget(self, crop):
        from gui.widgets.video_preview import VideoPreviewWidget

        w = VideoPreviewWidget()
        w.video_width, w.video_height = 240, 360
        w._mpv_process = None
        w.cropbox = list(crop)
        w._rotation = 0
        return w

    def test_rotation_keeps_box_over_same_original_pixels(self):
        w = self._widget([20, 40, 100, 150])
        original = w._cropbox_to_original_coords(*w.cropbox)  # at rotation 0
        for angle in (90, 180, 270, 0):
            w.set_rotation(angle)
            mapped_back = w._cropbox_to_original_coords(*w.cropbox)
            self.assertEqual(
                mapped_back, original,
                f"crop drifted after rotating to {angle}° "
                f"(box={w.cropbox}, back={mapped_back}, want={original})",
            )

    def test_full_turn_is_identity(self):
        w = self._widget([16, 48, 80, 120])
        start = list(w.cropbox)
        for angle in (90, 180, 270, 0):
            w.set_rotation(angle)
        self.assertEqual(w.cropbox, start)

    def test_rotation_does_not_reset_to_default_center(self):
        w = self._widget([10, 10, 60, 90])
        w.set_rotation(90)
        # The old code called _init_cropbox() here, which recomputed a default
        # centred 75%-of-frame box; the remapped box must NOT equal that.
        w2 = self._widget([0, 0, 0, 0])
        w2.set_rotation(90)
        w2._init_cropbox()
        self.assertNotEqual(w.cropbox, w2.cropbox)

    def test_inverse_helpers_round_trip(self):
        w = self._widget([12, 34, 56, 78])
        for angle in (0, 90, 180, 270):
            w._rotation = angle
            orig = w._cropbox_to_original_coords(12, 34, 56, 78)
            back = w._original_to_rotated_coords(*orig)
            self.assertEqual(back, (12, 34, 56, 78), f"round trip failed at {angle}°")


class BasicPanelClassIconTests(unittest.TestCase):
    def _panel(self):
        from gui.widgets.basic_config_panel import BasicConfigPanel
        from config.epconfig import EPConfig

        panel = BasicConfigPanel()
        panel._config = EPConfig()
        panel._updating = False
        idx = panel.combo_template.findText("明日方舟模板")
        if idx >= 0:
            panel.combo_template.setCurrentIndex(idx)
        # pick a class combo entry that has actual preset data
        panel._preset_idx = next(
            (i for i in range(panel.combo_ark_class.count())
             if panel.combo_ark_class.itemData(i)),
            0,
        )
        return panel

    def test_custom_class_icon_survives_template_apply(self):
        from config.epconfig import ArknightsOverlayOptions

        panel = self._panel()
        panel._config.overlay.arknights_options = ArknightsOverlayOptions(
            operator_class_icon="my_custom_icon.png"
        )
        panel.combo_ark_class.setCurrentIndex(panel._preset_idx)  # a preset selected
        panel.update_config_from_ui()
        self.assertEqual(
            panel._config.overlay.arknights_options.operator_class_icon,
            "my_custom_icon.png",
            "a custom (advanced-panel) class icon must not be clobbered",
        )

    def test_preset_class_icon_is_driven_by_combo(self):
        from config.epconfig import ArknightsOverlayOptions

        panel = self._panel()
        # existing value is a preset -> combo may drive it
        panel._config.overlay.arknights_options = ArknightsOverlayOptions(
            operator_class_icon="class_icons/warrior.png"
        )
        panel.combo_ark_class.setCurrentIndex(panel._preset_idx)
        data = panel.combo_ark_class.currentData()
        panel.update_config_from_ui()
        icon = panel._config.overlay.arknights_options.operator_class_icon
        if data:
            self.assertEqual(icon, f"class_icons/{data}.png")
        else:
            self.assertEqual(icon, "")


if __name__ == "__main__":
    unittest.main()
