"""Per-application VS migration and isolation boundaries."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from config import vs_user_data as data
from config.vs_runtime import default_vs_runtime_user_path, load_vs_runtime
from core.vs_runtime.trust import default_trust_path, ProjectTrustStore

class VSUserDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "app"
        self.legacy = self.root / "roaming" / "ArknightsPassMaker" / "vapoursynth"
        self.legacy.mkdir(parents=True)
        patch = mock.patch.dict(os.environ, {"APPDATA": str(self.root / "roaming"), data.USER_CONFIG_DIR_ENV: ""})
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch("config.vs_user_data.get_app_dir", return_value=str(self.app))
        patch.start()
        self.addCleanup(patch.stop)
        self.target = data.vs_user_config_dir()

    def test_getters_are_pure_and_per_application(self):
        self.assertEqual(default_vs_runtime_user_path(), self.target / data.USER_FILENAMES[0])
        self.assertEqual(default_trust_path(), self.target / data.USER_FILENAMES[1])
        other = self.root / "other"
        self.assertEqual(default_vs_runtime_user_path(other).parents[3], other)
        self.assertFalse(self.app.exists())

    def test_migrates_both_files_verbatim_and_preserves_source(self):
        payloads = {data.USER_FILENAMES[0]: b'{"core":{"num_threads":2}}',
                    data.USER_FILENAMES[1]: b'{"schema_version":1,"project_hashes":{}}'}
        for name, payload in payloads.items():
            (self.legacy / name).write_bytes(payload)
        self.assertEqual(load_vs_runtime().core.num_threads, 2)
        self.assertEqual(ProjectTrustStore()._read(), {})
        for name, payload in payloads.items():
            self.assertEqual((self.target / name).read_bytes(), payload)
            self.assertEqual((self.legacy / name).read_bytes(), payload)
        (self.legacy / data.USER_FILENAMES[0]).write_text("{}")
        data.migrate_vs_user_data_once()
        self.assertEqual(load_vs_runtime().core.num_threads, 2)

    def test_local_file_wins_and_empty_migration_does_not_import_later(self):
        self.target.mkdir(parents=True)
        local = self.target / data.USER_FILENAMES[0]
        local.write_bytes(b"{}")
        (self.legacy / local.name).write_bytes(b"legacy")
        data.migrate_vs_user_data_once()
        self.assertEqual(local.read_bytes(), b"{}")
        (self.legacy / data.USER_FILENAMES[1]).write_bytes(b"late")
        data.migrate_vs_user_data_once()
        self.assertFalse((self.target / data.USER_FILENAMES[1]).exists())

    def test_partial_failure_retries_only_missing_files(self):
        for name in data.USER_FILENAMES:
            (self.legacy / name).write_bytes(name.encode())
        original = data.atomic_write_bytes
        def fail_trust(path, payload):
            if Path(path).name == "trust.json":
                raise OSError("copy interrupted")
            original(path, payload)
        with mock.patch.object(data, "atomic_write_bytes", side_effect=fail_trust):
            with self.assertRaisesRegex(OSError, "copy interrupted"):
                data.migrate_vs_user_data_once()
        self.assertFalse((self.target / data.MIGRATION_MARKER).exists())
        first = self.target / data.USER_FILENAMES[0]
        first.write_bytes(b"local edit")
        data.migrate_vs_user_data_once()
        self.assertEqual(first.read_bytes(), b"local edit")
        self.assertEqual((self.target / "trust.json").read_bytes(), b"trust.json")
        self.assertTrue((self.target / data.MIGRATION_MARKER).exists())

    def test_isolated_directory_never_reads_legacy(self):
        isolated = self.root / "isolated"
        with mock.patch.dict(os.environ, {data.USER_CONFIG_DIR_ENV: str(isolated)}):
            with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("legacy read")):
                data.migrate_vs_user_data_once()
            self.assertEqual(default_vs_runtime_user_path(), isolated / data.USER_FILENAMES[0])
            self.assertEqual(ProjectTrustStore().path, isolated / "trust.json")
            self.assertEqual(load_vs_runtime().core.num_threads, 0)
        self.assertFalse(isolated.exists())

    def test_snapshot_reads_selected_app_after_migration(self):
        from core.vs_runtime.snapshot import resolve_runtime_config
        other = self.root / "selected app"
        (self.legacy / data.USER_FILENAMES[0]).write_bytes(b'{"core":{"num_threads":7}}')
        self.assertEqual(resolve_runtime_config(other).core.num_threads, 7)
        self.assertTrue(default_vs_runtime_user_path(other).exists())
        self.assertFalse(self.app.exists())
