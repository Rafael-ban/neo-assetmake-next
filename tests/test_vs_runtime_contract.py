import concurrent.futures
import hashlib
import json
import multiprocessing
import tempfile
import threading
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

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
RUNTIME_SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _save_override_process(path, patch, barrier, results):
    barrier.wait(timeout=10)
    try:
        save_vs_runtime_override(path, patch)
    except Exception as exc:
        results.put(("error", str(exc)))
    else:
        results.put(("ok", ""))


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
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

        Draft202012Validator.check_schema(RUNTIME_SCHEMA)
        Draft202012Validator(RUNTIME_SCHEMA).validate(payload)
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

    def test_runtime_paths_share_one_canonical_windows_wire_contract(self):
        valid_payloads = []
        for script_path, plugin_path in (
            (r"D:\VS\pipeline.vpy", r"D:\VS\plugins"),
            (r"\\server\share\pipeline.vpy", r"\\server\share\plugins"),
            ("", r"D:\VS\plugins"),
        ):
            payload = VSRuntimeConfig().to_dict()
            payload["scripts"]["global_script_path"] = script_path
            payload["plugins"]["native_plugin_dirs"] = [plugin_path]
            valid_payloads.append(payload)
        for payload in valid_payloads:
            with self.subTest(valid=payload):
                Draft202012Validator(RUNTIME_SCHEMA).validate(payload)
                VSRuntimeConfig.from_dict(payload)

        invalid_paths = (
            "D:/VS/pipeline.vpy",
            r"D:\VS\..\pipeline.vpy",
            r"\VS\pipeline.vpy",
            r"relative\pipeline.vpy",
            r"D:\bad?name\pipeline.vpy",
        )
        for invalid_path in invalid_paths:
            payload = VSRuntimeConfig().to_dict()
            payload["scripts"]["global_script_path"] = invalid_path
            with self.subTest(global_script_path=invalid_path):
                with self.assertRaises(ValidationError):
                    Draft202012Validator(RUNTIME_SCHEMA).validate(payload)
                with self.assertRaises(VSRuntimeConfigError):
                    VSRuntimeConfig.from_dict(payload)

            payload = VSRuntimeConfig().to_dict()
            payload["plugins"]["native_plugin_dirs"] = [invalid_path]
            with self.subTest(plugin_dir=invalid_path):
                with self.assertRaises(ValidationError):
                    Draft202012Validator(RUNTIME_SCHEMA).validate(payload)
                with self.assertRaises(VSRuntimeConfigError):
                    VSRuntimeConfig.from_dict(payload)

    def test_save_patch_canonicalizes_paths(self):
        path = self.root / "vs_runtime.user.json"
        save_vs_runtime_override(
            path,
            {
                "plugins": {"native_plugin_dirs": ["D:/VS/./plugins"]},
                "scripts": {"global_script_path": "D:/VS/./pipeline.vpy"},
            },
        )

        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["plugins"]["native_plugin_dirs"], [r"D:\VS\plugins"]
        )
        self.assertEqual(
            payload["scripts"]["global_script_path"], r"D:\VS\pipeline.vpy"
        )

    def test_save_patch_preserves_existing_fields(self):
        path = self.root / "vs_runtime.user.json"
        save_vs_runtime_override(
            path,
            {
                "worker": {"frame_timeout_ms": 12_345},
                "plugins": {"native_plugin_dirs": [r"D:\VS\plugins"]},
            },
        )

        save_vs_runtime_override(
            path,
            {"scripts": {"global_script_path": r"D:\VS\pipeline.vpy"}},
        )

        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            payload.get("worker", {}).get("frame_timeout_ms"), 12_345
        )
        self.assertEqual(
            payload.get("plugins", {}).get("native_plugin_dirs"),
            [r"D:\VS\plugins"],
        )

    def test_save_patch_rejects_non_object(self):
        with self.assertRaises(VSRuntimeConfigError):
            save_vs_runtime_override(self.root / "invalid.user.json", [])

    def test_concurrent_thread_patches_do_not_lose_fields(self):
        path = self.root / "thread.user.json"
        barrier = threading.Barrier(2)
        patches = (
            {"worker": {"frame_timeout_ms": 12_345}},
            {"core": {"num_threads": 4}},
        )

        def apply_patch(patch):
            barrier.wait(timeout=10)
            save_vs_runtime_override(path, patch)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(apply_patch, patches))

        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            payload.get("worker", {}).get("frame_timeout_ms"), 12_345
        )
        self.assertEqual(payload.get("core", {}).get("num_threads"), 4)

    def test_concurrent_process_patches_do_not_lose_fields(self):
        path = self.root / "process.user.json"
        context = multiprocessing.get_context("spawn")
        barrier = context.Barrier(2)
        results = context.Queue()
        patches = (
            {"worker": {"frame_timeout_ms": 12_345}},
            {"core": {"num_threads": 4}},
        )
        processes = [
            context.Process(
                target=_save_override_process,
                args=(path, patch, barrier, results),
            )
            for patch in patches
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=15)
            self.assertFalse(process.is_alive(), "patch 进程未按时退出")
            self.assertEqual(process.exitcode, 0)
        outcomes = [results.get(timeout=5) for _ in processes]
        self.assertEqual([state for state, _ in outcomes], ["ok", "ok"])

        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(
            payload.get("worker", {}).get("frame_timeout_ms"), 12_345
        )
        self.assertEqual(payload.get("core", {}).get("num_threads"), 4)

    def test_float_schema_version_is_rejected_by_model_and_loader(self):
        payload = VSRuntimeConfig().to_dict()
        payload["schema_version"] = 1.0
        path = self.write_json(payload, "float-version.json")

        with self.assertRaises(VSRuntimeConfigError):
            VSRuntimeConfig.from_dict(payload)
        with self.assertRaises(VSRuntimeConfigError):
            load_vs_runtime(path)


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

    def test_existing_user_override_wins_over_legacy_values(self):
        self.write_legacy()
        self.user_path.parent.mkdir(parents=True)
        self.user_path.write_text(
            json.dumps(
                {
                    "core": {"num_threads": 9},
                    "plugins": {
                        "native_plugin_dirs": [r"D:\User\plugins"]
                    },
                }
            ),
            encoding="utf-8",
        )

        migrate_legacy_vsconfig_once(
            self.legacy_path, self.user_path, self.marker_path
        )

        payload = json.loads(self.user_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["core"]["num_threads"], 9)
        self.assertEqual(payload["core"]["max_cache_size_mb"], 512)
        self.assertEqual(
            payload["plugins"]["native_plugin_dirs"], [r"D:\User\plugins"]
        )

    def test_changed_hash_reapplies_and_migrates_new_allowlisted_field(self):
        self.legacy_path.write_text(
            json.dumps({"core": {"num_threads": 4}}), encoding="utf-8"
        )
        first = migrate_legacy_vsconfig_once(
            self.legacy_path, self.user_path, self.marker_path
        )
        self.legacy_path.write_text(
            json.dumps(
                {"core": {"num_threads": 6, "max_cache_size_mb": 768}}
            ),
            encoding="utf-8",
        )

        second = migrate_legacy_vsconfig_once(
            self.legacy_path, self.user_path, self.marker_path
        )

        self.assertTrue(second.applied)
        self.assertNotEqual(second.source_hash, first.source_hash)
        payload = json.loads(self.user_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["core"]["num_threads"], 4)
        self.assertEqual(payload["core"]["max_cache_size_mb"], 768)

    def test_corrupt_marker_and_target_fail_loudly_with_path(self):
        self.write_legacy()
        self.marker_path.parent.mkdir(parents=True)
        self.marker_path.write_text("{", encoding="utf-8")
        with self.assertRaises(VSRuntimeConfigError) as marker_error:
            migrate_legacy_vsconfig_once(
                self.legacy_path, self.user_path, self.marker_path
            )
        self.assertIn(str(self.marker_path.resolve()), str(marker_error.exception))

        self.marker_path.unlink()
        self.user_path.write_text("{", encoding="utf-8")
        with self.assertRaises(VSRuntimeConfigError) as target_error:
            migrate_legacy_vsconfig_once(
                self.legacy_path, self.user_path, self.marker_path
            )
        self.assertIn(str(self.user_path.resolve()), str(target_error.exception))

    def test_relative_legacy_plugin_dir_is_canonicalized_from_install_root(self):
        install_root = self.root / "ArknightsPassMaker"
        legacy_path = install_root / "config" / "vsconfig.json"
        user_path = self.root / "user" / "vs_runtime.user.json"
        marker_path = self.root / "user" / "migration.json"
        legacy_path.parent.mkdir(parents=True)
        legacy_path.write_text(
            json.dumps({"extra_plugin_dirs": ["vs-plugins"]}),
            encoding="utf-8",
        )

        migrate_legacy_vsconfig_once(legacy_path, user_path, marker_path)

        payload = json.loads(user_path.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["plugins"]["native_plugin_dirs"],
            [str(install_root / "tools" / "media" / "vs-plugins")],
        )

    def test_global_script_patch_after_migration_keeps_migrated_fields(self):
        self.write_legacy()
        migrate_legacy_vsconfig_once(
            self.legacy_path, self.user_path, self.marker_path
        )

        save_vs_runtime_override(
            self.user_path,
            {"scripts": {"global_script_path": r"D:\VS\pipeline.vpy"}},
        )

        payload = json.loads(self.user_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["core"]["num_threads"], 4)
        self.assertEqual(
            payload["plugins"]["native_plugin_dirs"],
            [r"D:\VS\plugins"],
        )
        self.assertEqual(
            payload["scripts"]["global_script_path"], r"D:\VS\pipeline.vpy"
        )

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
