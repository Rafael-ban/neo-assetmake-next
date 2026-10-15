import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from config.epconfig import EPConfig


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "epconfig.schema.json"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "epconfig"


class EPConfigContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.validator = Draft202012Validator(cls.schema)

    def fixture_paths(self):
        return sorted(FIXTURE_DIR.glob("*.json"))

    def test_fixtures_validate_against_schema(self):
        for path in self.fixture_paths():
            with self.subTest(path=path.name):
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.validator.validate(payload)

    def test_python_model_roundtrips_golden_fixtures(self):
        for path in self.fixture_paths():
            with self.subTest(path=path.name):
                payload = json.loads(path.read_text(encoding="utf-8"))
                config = EPConfig.from_dict(payload)
                roundtrip = config.to_dict()

                self.validator.validate(roundtrip)
                self.assertEqual(roundtrip["uuid"], payload["uuid"])
                self.assertEqual(roundtrip["screen"], payload["screen"])
                self.assertEqual(roundtrip["loop"]["file"], payload["loop"]["file"])
                if payload.get("name"):
                    self.assertEqual(roundtrip["name"], payload["name"])

    def test_normalized_export_shape_validates(self):
        payload = json.loads(
            (FIXTURE_DIR / "full_overlay_transition.json").read_text(encoding="utf-8")
        )
        config = EPConfig.from_dict(payload)
        normalized = config.to_dict(normalize_paths=True)

        self.validator.validate(normalized)
        self.assertEqual(normalized["icon"], "icon.png")
        self.assertEqual(normalized["loop"]["file"], "loop.mp4")


if __name__ == "__main__":
    unittest.main()
