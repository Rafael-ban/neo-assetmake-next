"""Regression tests for invalid-enum validation (M2a).

A config file with an out-of-range screen/transition/overlay value used to be
silently coerced to a default and pass validation as "配置有效". Now the raw
invalid value is surfaced as a validation ERROR (while the app still runs).
"""
import unittest

from config.epconfig import EPConfig
from core.validator import EPConfigValidator, ValidationLevel

VALID_UUID = "12345678-1234-1234-1234-123456789abc"


def _cfg(**overrides):
    base = {
        "version": 1, "uuid": VALID_UUID, "name": "x",
        "screen": "360x640", "loop": {"file": "loop.mp4"},
    }
    base.update(overrides)
    return EPConfig.from_dict(base)


class InvalidEnumValidationTests(unittest.TestCase):
    def _error_fields(self, cfg):
        results = EPConfigValidator().validate_config(cfg)
        return {r.field for r in results if r.level == ValidationLevel.ERROR}

    def test_invalid_screen_is_reported(self):
        cfg = _cfg(screen="1920x1080")
        self.assertEqual(cfg.screen.value, "360x640")   # still coerced so the app runs
        self.assertIn("screen", self._error_fields(cfg))

    def test_invalid_overlay_and_transition_types_are_reported(self):
        cfg = _cfg(overlay={"type": "nope"}, transition_in={"type": "zoom"})
        fields = self._error_fields(cfg)
        self.assertIn("overlay.type", fields)
        self.assertIn("transition_in.type", fields)

    def test_valid_config_has_no_enum_errors(self):
        cfg = _cfg()
        self.assertNotIn("screen", self._error_fields(cfg))
        self.assertEqual(getattr(cfg, "_invalid_fields", []), [])

    def test_fresh_config_has_no_invalid_fields(self):
        self.assertEqual(getattr(EPConfig(), "_invalid_fields", []), [])


if __name__ == "__main__":
    unittest.main()
