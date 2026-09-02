import json
import shutil
import tempfile
import unittest
from pathlib import Path


def _read_inno_files_entries(path):
    entries = []
    in_files = False
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if line.startswith("["):
            in_files = line.casefold() == "[files]"
            continue
        if not in_files or not line or line.startswith(";"):
            continue
        entry = {}
        for fragment in line.split(";"):
            key, value = fragment.split(":", 1)
            entry[key.strip().casefold()] = value.strip().strip('"')
        entries.append(entry)
    return entries


def _simulate_inno_files(entries, installer_root, app_dir):
    for entry in entries:
        source_value = entry["source"].replace("\\", "/")
        flags = set(entry.get("flags", "").casefold().split())
        destination = entry["destdir"].replace("{app}", str(app_dir))
        destination_path = Path(destination.replace("\\", "/"))
        excludes = {
            value.strip().casefold()
            for value in entry.get("excludes", "").split(",")
            if value.strip()
        }
        if source_value.endswith("/*"):
            source_base = installer_root / source_value[:-2]
            candidates = source_base.rglob("*")
        else:
            source_base = (installer_root / source_value).parent
            candidates = (installer_root / source_value,)
        for source in candidates:
            if not source.is_file():
                continue
            relative = source.relative_to(source_base)
            wire_relative = str(relative).replace("/", "\\").casefold()
            if wire_relative in excludes:
                continue
            target = destination_path / relative
            if "onlyifdoesntexist" in flags and target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


class MediaPackagingTests(unittest.TestCase):
    def setUp(self):
        self.build_source = Path("build.py").read_text(encoding="utf-8")

    def test_build_script_does_not_package_removed_media_dependencies(self):
        # mpv.exe joined this list when preview moved to in-process VapourSynth:
        # it was ~117MB of the installer and nothing loads it any more.
        for removed_token in ("ffmpeg.exe", "ffprobe.exe", "av.libs", "ffmpeg-sdk",
                              "mpv.exe"):
            with self.subTest(removed_token=removed_token):
                self.assertNotIn(removed_token, self.build_source)

    def test_build_script_keeps_media_tool_candidates(self):
        expected_tokens = (
            "VSPipe.exe",
            "x264-7mod.exe",
            "mp4box.exe",
            "lsmash",
        )
        for expected_token in expected_tokens:
            with self.subTest(expected_token=expected_token):
                self.assertIn(expected_token, self.build_source)

    def test_contract_manifest_has_existing_absolute_sources(self):
        from build import _collect_vs_contract_include_files

        entries = _collect_vs_contract_include_files(Path.cwd())

        self.assertEqual(
            [destination for _, destination in entries],
            [
                "config/vs_runtime.json",
                "config/vsconfig.json",
                "schemas/vs_runtime.schema.json",
                "schemas/vs_job.schema.json",
            ],
        )
        for source, _ in entries:
            with self.subTest(source=source):
                self.assertTrue(Path(source).is_absolute())
                self.assertTrue(Path(source).is_file())

    def test_missing_required_contract_aborts_collection(self):
        from build import _collect_vs_contract_include_files

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for relative in (
                "config/vs_runtime.json",
                "config/vsconfig.json",
                "schemas/vs_runtime.schema.json",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(
                FileNotFoundError, "schemas.vs_job.schema.json"
            ):
                _collect_vs_contract_include_files(root)

    def test_worker_support_manifest_requires_every_runtime_helper_file(self):
        from build import _collect_vs_worker_support_files

        entries = _collect_vs_worker_support_files(Path.cwd())
        destinations = [destination for _, destination in entries]

        helper_root = "resources/vapoursynth/python/assetmaker_vs"
        self.assertEqual(
            destinations,
            [
                "resources/vapoursynth/assetmaker_runner.vpy",
                "resources/vapoursynth/default_pipeline.vpy",
                *[
                    f"{helper_root}/{filename}"
                    for filename in (
                        "__init__.py",
                        "job_api.py",
                        "script_header.py",
                        "executor.py",
                        "contract.py",
                        "display.py",
                    )
                ],
            ],
        )
        for source, _destination in entries:
            self.assertTrue(Path(source).is_absolute())
            self.assertTrue(Path(source).is_file())

    def test_missing_worker_helper_aborts_collection(self):
        from build import VS_WORKER_SUPPORT_FILES, _collect_vs_worker_support_files

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for relative in VS_WORKER_SUPPORT_FILES[:-1]:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# test", encoding="utf-8")

            with self.assertRaisesRegex(FileNotFoundError, "display.py"):
                _collect_vs_worker_support_files(root)

    def test_ci_extracts_media_before_tests_and_self_tests_frozen_worker(self):
        workflow = Path(".github/workflows/build-app.yml").read_text(
            encoding="utf-8"
        )

        self.assertLess(
            workflow.index("- name: Extract media tools"),
            workflow.index("- name: Run Python tests"),
        )
        self.assertIn("vs_worker.exe", workflow)
        self.assertIn("SyncVSWorkerProcess", workflow)
        self.assertIn("--self-test", workflow)
        for artifact in (
            "ArknightsPassMaker/tools/media/vapoursynth.pyd",
            "ArknightsPassMaker/tools/media/vapoursynth.dll",
            "ArknightsPassMaker/tools/media/portable.vs",
            "ArknightsPassMaker/tools/media/vs-plugins/LSMASHSource.dll",
            "ArknightsPassMaker/tools/media/vs-plugins/libimwri.dll",
            "ArknightsPassMaker/resources/vapoursynth/python/assetmaker_vs/job_api.py",
            "ArknightsPassMaker/resources/vapoursynth/python/assetmaker_vs/display.py",
        ):
            with self.subTest(artifact=artifact):
                self.assertIn(artifact, workflow)
        self.assertIn('"中文自测" in message', workflow)
        self.assertIn("len(message) > 65_536", workflow)

    def test_installer_upgrade_preserves_legacy_until_migration(self):
        from core.vs_runtime.migration import migrate_legacy_vsconfig_once

        entries = _read_inno_files_entries(Path("installer.iss"))
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = root / "ArknightsPassMaker"
            app_dir = root / "installed"
            shipped_legacy = payload / "config" / "vsconfig.json"
            shipped_runtime = payload / "config" / "vs_runtime.json"
            shipped_legacy.parent.mkdir(parents=True)
            shipped_legacy.write_text(
                '{"core":{"num_threads":0,"max_cache_size_mb":0}}',
                encoding="utf-8",
            )
            shipped_runtime.write_text('{"schema_version":1}', encoding="utf-8")

            installed_legacy = app_dir / "config" / "vsconfig.json"
            installed_runtime = app_dir / "config" / "vs_runtime.json"
            installed_legacy.parent.mkdir(parents=True)
            legacy_bytes = (
                b'{"core":{"num_threads":7,"max_cache_size_mb":256}}'
            )
            installed_legacy.write_bytes(legacy_bytes)
            installed_runtime.write_text("old-runtime", encoding="utf-8")

            _simulate_inno_files(entries, root, app_dir)

            self.assertEqual(installed_legacy.read_bytes(), legacy_bytes)
            self.assertEqual(
                installed_runtime.read_text(encoding="utf-8"),
                '{"schema_version":1}',
            )
            user_path = root / "user" / "vs_runtime.user.json"
            report = migrate_legacy_vsconfig_once(
                installed_legacy,
                user_path,
                root / "user" / "migration.json",
            )
            self.assertTrue(report.applied)
            self.assertEqual(
                json.loads(user_path.read_text(encoding="utf-8"))["core"][
                    "num_threads"
                ],
                7,
            )

    def test_installer_fresh_install_receives_legacy_default(self):
        entries = _read_inno_files_entries(Path("installer.iss"))
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "ArknightsPassMaker" / "config" / "vsconfig.json"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"shipped-default")
            app_dir = root / "installed"

            _simulate_inno_files(entries, root, app_dir)

            self.assertEqual(
                (app_dir / "config" / "vsconfig.json").read_bytes(),
                b"shipped-default",
            )

    def test_runtime_source_does_not_reference_removed_ffmpeg_stack(self):
        disallowed_tokens = (
            "import av",
            "av.open",
            "av.VideoFrame",
            "ffmpeg-next",
            "ffmpeg-sdk",
            "ffmpeg.exe",
            "ffprobe",
            "libx264",
        )
        source_roots = (
            Path("core"),
            Path("gui"),
            Path("simulator") / "src",
            Path("simulator") / "Cargo.toml",
            Path("pyproject.toml"),
        )
        offenders = []

        for root in source_roots:
            paths = [root] if root.is_file() else root.rglob("*")
            for path in paths:
                if path.suffix not in {".py", ".rs", ".toml"}:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                for token in disallowed_tokens:
                    if token in text:
                        offenders.append(f"{path}:{token}")

        self.assertEqual([], offenders)

    def test_runtime_source_has_no_executable_mpv_references(self):
        """Regression lock: mpv must not come back as a code dependency.

        Matches *identifiers and command strings*, not the word "mpv" — several
        docstrings still explain why the VapourSynth design replaced the mpv one,
        and that history is worth keeping.
        """
        disallowed_tokens = (
            "mpv_path",           # toolchain field / constructor arg
            "mpv.exe",
            "_mpv_process",
            "_mpv_socket",
            "_send_mpv_command",
            "_MpvMetadataSession",
            "_MpvSurface",
            "input-ipc-server",   # mpv CLI
            "screenshot-to-file",
            "video-rotate",
            "MpvLaunchWorker",
        )
        offenders = []
        for root in (Path("core"), Path("gui"), Path("config"), Path("utils"),
                     Path("main.py"), Path("build.py")):
            paths = [root] if root.is_file() else root.rglob("*.py")
            for path in paths:
                text = path.read_text(encoding="utf-8", errors="ignore")
                for token in disallowed_tokens:
                    if token in text:
                        offenders.append(f"{path}:{token}")

        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
