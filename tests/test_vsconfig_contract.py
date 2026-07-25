"""V1: VSConfig schema + round-trip contract (mirrors test_epconfig_contract).

Proves the externalized VS config validates against its Draft 2020-12 schema
and that VSConfig.from_dict/to_dict round-trips, and that the DATACLASS
DEFAULTS reproduce the previously-hardcoded literals (so a missing file keeps
the pipeline byte-identical).
"""
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from config.vsconfig import VSConfig, MatrixHeuristic, load_vsconfig


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "vsconfig.schema.json"
CONFIG_PATH = ROOT / "config" / "vsconfig.json"


class VSConfigContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.validator = Draft202012Validator(cls.schema)
        cls.payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    def test_shipped_config_validates_against_schema(self):
        self.validator.validate(self.payload)

    def test_from_dict_roundtrips_and_revalidates(self):
        cfg = VSConfig.from_dict(self.payload)
        roundtrip = cfg.to_dict()
        self.validator.validate(roundtrip)
        self.assertEqual(roundtrip["required_plugins"], self.payload["required_plugins"])
        self.assertEqual(roundtrip["colour"]["matrix_s"], self.payload["colour"]["matrix_s"])

    def test_defaults_reproduce_hardcoded_literals(self):
        # A missing/partial file must yield today's exact values.
        cfg = VSConfig()
        self.assertEqual(cfg.required_plugins, ("lsmas", "imwri"))
        self.assertEqual(cfg.extra_plugin_dirs, ("vs-plugins",))
        self.assertEqual(cfg.image_source_format, "RGB24")
        self.assertEqual(cfg.output_format, "YUV420P8")
        self.assertEqual(cfg.resampler_kernel, "Bicubic")
        self.assertEqual(cfg.matrix_s, "170m")
        self.assertEqual(cfg.heuristic, MatrixHeuristic(720, 1, 6))

    def test_shipped_json_matches_defaults(self):
        # The checked-in default file must equal the dataclass defaults, so
        # shipping-or-not-shipping the file can never change behaviour.
        self.assertEqual(VSConfig.from_dict(self.payload), VSConfig())

    def test_empty_and_malformed_payloads_degrade_to_defaults(self):
        self.assertEqual(VSConfig.from_dict({}), VSConfig())
        self.assertEqual(VSConfig.from_dict(None), VSConfig())
        self.assertEqual(
            VSConfig.from_dict({"required_plugins": [], "colour": "garbage"}),
            VSConfig(),
        )

    def test_load_vsconfig_returns_a_config(self):
        load_vsconfig.cache_clear()
        cfg = load_vsconfig()
        self.assertIsInstance(cfg, VSConfig)
        # Repo checkout has config/vsconfig.json -> equals shipped payload.
        self.assertEqual(cfg, VSConfig.from_dict(self.payload))


if __name__ == "__main__":
    unittest.main()
