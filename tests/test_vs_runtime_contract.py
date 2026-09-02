import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from config.vs_runtime import (
    VSRuntimeConfig,
    VSRuntimeConfigError,
    load_vs_runtime,
    save_vs_runtime_override,
)
from core.vs_runtime.migration import migrate_legacy_vsconfig_once


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "vs_runtime.schema.json"
CONFIG_PATH = ROOT / "config" / "vs_runtime.json"


class VSRuntimeContractTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def write_json(self, payload, name="vs_runtime.json"):
        path = self.root / name
        path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return path

    def test_shipped_runtime_matches_schema_and_model(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(payload)
        self.assertEqual(VSRuntimeConfig.from_dict(payload).to_dict(), payload)

    def test_filter_policy_is_not_a_runtime_field(self):
        serialized = json.dumps(VSRuntimeConfig().to_dict())
        for key in (
            "resampler_kernel",
            "output_format",
            "image_source_format",
            "matrix_s",
            "heuristic",
            "required_plugins",
        ):
            with self.subTest(key=key):
                self.assertNotIn(key, serialized)

    def test_missing_file_returns_defaults(self):
        missing = self.root / "missing.json"
        self.assertEqual(load_vs_runtime(missing), VSRuntimeConfig())

    def test_existing_but_invalid_file_fails_loudly(self):
        path = self.write_json({"schema_version": 999})

        with self.assertRaisesRegex(
            VSRuntimeConfigError, str(path.resolve()).replace("\\", "\\\\")
        ):
            load_vs_runtime(path)

    def test_malformed_json_fails_with_absolute_path(self):
        path = self.root / "broken.json"
        path.write_text("{", encoding="utf-8")

        with self.assertRaises(VSRuntimeConfigError) as raised:
            load_vs_runtime(path)

        self.assertIn(str(path.resolve()), str(raised.exception))

    def test_unknown_negative_and_wrong_type_values_are_rejected(self):
        invalid_payloads = (
            {"unknown": True},
            {"worker": {"frame_timeout_ms": -1}},
            {"core": {"num_threads": True}},
            {"plugins": {"native_plugin_dirs": [1]}},
            {"scripts": {"global_script_path": None}},
        )
        for index, payload in enumerate(invalid_payloads):
            with self.subTest(payload=payload):
                path = self.write_json(payload, f"invalid-{index}.json")
                with self.assertRaises(VSRuntimeConfigError):
                    load_vs_runtime(path)

    def test_python_model_rejects_non_json_plugin_sequence(self):
        payload = VSRuntimeConfig().to_dict()
        payload["plugins"]["native_plugin_dirs"] = ("vs-plugins",)

        with self.assertRaises(VSRuntimeConfigError):
            VSRuntimeConfig.from_dict(payload)

    def test_user_override_changes_global_script_without_writing_shipped_file(self):
        shipped_path = self.write_json(
            VSRuntimeConfig().to_dict(), "shipped.json"
        )
        user_path = self.root / "vapoursynth" / "vs_runtime.user.json"
        shipped_before = shipped_path.read_bytes()

        save_vs_runtime_override(
            user_path,
            {"scripts": {"global_script_path": r"D:\VS\pipeline.vpy"}},
        )
        merged = load_vs_runtime(shipped_path, user_path)

        self.assertEqual(
            merged.scripts.global_script_path, r"D:\VS\pipeline.vpy"
        )
        self.assertEqual(shipped_path.read_bytes(), shipped_before)
        self.assertEqual(
            json.loads(user_path.read_text(encoding="utf-8")),
            {"scripts": {"global_script_path": r"D:\VS\pipeline.vpy"}},
        )
        self.assertFalse(list(user_path.parent.glob(f".{user_path.name}.*.tmp")))

    def test_nested_override_merges_without_resetting_siblings(self):
        shipped = VSRuntimeConfig().to_dict()
        shipped["worker"]["startup_timeout_ms"] = 25_000
        shipped_path = self.write_json(shipped, "shipped.json")
        user_path = self.write_json(
            {"worker": {"frame_timeout_ms": 30_000}}, "user.json"
        )

        merged = load_vs_runtime(shipped_path, user_path)

        self.assertEqual(merged.worker.startup_timeout_ms, 25_000)
        self.assertEqual(merged.worker.frame_timeout_ms, 30_000)
        self.assertEqual(merged.worker.shutdown_timeout_ms, 3_000)


class LegacyVSConfigMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.legacy_path = self.root / "vsconfig.json"
        self.user_path = self.root / "vapoursynth" / "vs_runtime.user.json"
        self.marker_path = self.root / "vapoursynth" / "migration.json"

    def write_legacy(self):
        payload = {
            "version": 1,
            "required_plugins": ["lsmas", "imwri"],
            "extra_plugin_dirs": [r"D:\VS\plugins"],
            "image_source_format": "RGB24",
            "output_format": "YUV420P8",
            "resampler_kernel": "Lanczos",
            "colour": {
                "matrix_s": "709",
                "heuristic": {
                    "height_threshold": 720,
                    "hd_matrix": 1,
                    "sd_matrix": 6,
                },
            },
            "core": {"num_threads": 4, "max_cache_size_mb": 512},
            "unrelated": "leave behind",
        }
        self.legacy_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    def test_only_allowlisted_runtime_fields_are_migrated(self):
        self.write_legacy()
        legacy_before = self.legacy_path.read_bytes()
        expected_hash = hashlib.sha256(legacy_before).hexdigest()

        report = migrate_legacy_vsconfig_once(
            self.legacy_path, self.user_path, self.marker_path
        )

        self.assertTrue(report.applied)
        self.assertEqual(
            report.migrated_fields,
            (
                "core.num_threads",
                "core.max_cache_size_mb",
                "extra_plugin_dirs",
            ),
        )
        self.assertEqual(
            report.ignored_fields,
            (
                "required_plugins",
                "image_source_format",
                "output_format",
                "resampler_kernel",
                "colour",
            ),
        )
        self.assertEqual(report.source_hash, expected_hash)
        self.assertEqual(self.legacy_path.read_bytes(), legacy_before)
        self.assertEqual(
            json.loads(self.user_path.read_text(encoding="utf-8")),
            {
                "core": {"num_threads": 4, "max_cache_size_mb": 512},
                "plugins": {"native_plugin_dirs": [r"D:\VS\plugins"]},
            },
        )
        marker = json.loads(self.marker_path.read_text(encoding="utf-8"))
        self.assertEqual(marker, {"source_hash": expected_hash})

    def test_same_source_hash_is_not_applied_twice(self):
        self.write_legacy()
        first = migrate_legacy_vsconfig_once(
            self.legacy_path, self.user_path, self.marker_path
        )
        user_before = self.user_path.read_bytes()

        second = migrate_legacy_vsconfig_once(
            self.legacy_path, self.user_path, self.marker_path
        )

        self.assertTrue(first.applied)
        self.assertFalse(second.applied)
        self.assertEqual(second.source_hash, first.source_hash)
        self.assertEqual(self.user_path.read_bytes(), user_before)

    def test_missing_legacy_file_is_a_noop(self):
        report = migrate_legacy_vsconfig_once(
            self.legacy_path, self.user_path, self.marker_path
        )

        self.assertFalse(report.applied)
        self.assertEqual(report.migrated_fields, ())
        self.assertEqual(report.ignored_fields, ())
        self.assertEqual(report.source_hash, "")
        self.assertFalse(self.user_path.exists())
        self.assertFalse(self.marker_path.exists())


if __name__ == "__main__":
    unittest.main()
