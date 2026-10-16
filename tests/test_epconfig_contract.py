import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from config.epconfig import EPConfig
from core.validator import EPConfigValidator, ValidationLevel


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "epconfig.schema.json"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "epconfig"


class EPConfigContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.validator = Draft202012Validator(cls.schema)

    def test_new_and_missing_version_default_to_two(self):
        for config in (EPConfig(), EPConfig.from_dict({})):
            with self.subTest(config=config):
                self.assertEqual(config.version, 2)
                self.assertEqual(config.to_dict()["version"], 2)
                self.assertEqual(config.to_dict(normalize_paths=True)["version"], 2)

    def test_supported_versions_roundtrip_validate_and_export(self):
        for version in (-1, 0, 1, 2):
            with self.subTest(version=version):
                config = EPConfig.from_dict({
                    "version": version, "loop": {"file": "loop.mp4"},
                })
                self.assertEqual(config.to_dict()["version"], version)
                exported = config.to_dict(normalize_paths=True)
                self.assertEqual(exported["version"], version)
                self.validator.validate(exported)
                results = EPConfigValidator().validate_config(config)
                self.assertFalse(any(
                    result.field == "version" and result.level == ValidationLevel.ERROR
                    for result in results
                ))

    def test_unsupported_versions_fail_validation_and_export(self):
        for version in (3, 99, "2", True, False, None, 2.0, 1.5):
            with self.subTest(version=version):
                config = EPConfig.from_dict({"version": version})
                results = EPConfigValidator().validate_config(config)
                self.assertTrue(any(
                    result.field == "version" and result.level == ValidationLevel.ERROR
                    for result in results
                ))
                with self.assertRaisesRegex(ValueError, "素材配置版本"):
                    config.to_dict(normalize_paths=True)
                # Loading and saving preserve the source value for diagnosis.
                self.assertEqual(config.to_dict()["version"], version)

    def test_schema_rejects_future_or_noninteger_versions(self):
        for version in (3, "2", True, None, 1.5):
            with self.subTest(version=version):
                payload = EPConfig().to_dict()
                payload["version"] = version
                self.assertTrue(list(self.validator.iter_errors(payload)))

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

    def test_export_shape_strips_project_only_vpy_selection(self):
        config = EPConfig.from_dict(
            {
                "loop": {"file": "loop.mp4"},
                "editor": {
                    "vs_script": {
                        "source": "project",
                        "path": "vapoursynth/pipeline.vpy",
                    }
                },
            }
        )

        self.assertEqual(
            config.to_dict()["editor"]["vs_script"],
            {"source": "project", "path": "vapoursynth/pipeline.vpy"},
        )
        self.assertNotIn("editor", config.to_dict(normalize_paths=True))


if __name__ == "__main__":
    unittest.main()
